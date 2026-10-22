"""GPU analyzers for the source DB. Run with this folder's environment:

    uv run --project engine/analyzers python engine/analyzers/run.py <db> [stage ...]

Stages: tagger, embed, speech. Each writes through lumina.sourcedb.db, so the
DB's rules (producer replaces only its rows, rejected facts stay rejected)
hold here too, and records its version in `job` so a rerun skips done media.
"""

from __future__ import annotations

import csv
import sqlite3
import sys
from fractions import Fraction
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from lumina.sourcedb import catalog, db  # noqa: E402
from lumina.sourcedb.ingest import thumb_path  # noqa: E402
from lumina.timebase import TICKS_PER_SECOND  # noqa: E402

S = TICKS_PER_SECOND
MODELS = Path.home() / ".lumina" / "models"

TAGGER_V = "wd-swinv2-v3@2"
EMBED_V = "siglip2-so400m-384@1"
EMBED_MODEL = "siglip2-so400m-384"
SPEECH_V = "whisper-large-v3@1"

GENERAL_MIN = 0.35
CHARACTER_MIN = 0.85


def todo(con, stage: str, version: str, kinds=("video", "image")) -> list[sqlite3.Row]:
    q = ",".join("?" * len(kinds))
    return [
        m for m in con.execute(
            f"SELECT * FROM media WHERE kind IN ({q}) AND status != 'dropped' ORDER BY media_id", kinds
        )
        if db.job_version(con, m["media_id"], stage) != version
        and db.job_version(con, m["media_id"], "cuts") is not None
    ]


def cuts_of(con, media_id):
    return con.execute("SELECT * FROM cut WHERE media_id = ? ORDER BY idx", (media_id,)).fetchall()


def span(m, c) -> dict:
    return {} if m["kind"] == "image" else {"t0_ticks": c["start_ticks"], "t1_ticks": c["end_ticks"]}


# ---------------------------------------------------------------------------
# WD tagger
# ---------------------------------------------------------------------------



def stage_tagger(con):
    import onnxruntime as ort

    ort.preload_dlls()
    folder = MODELS / "wd-swinv2-tagger-v3"
    sess = ort.InferenceSession(str(folder / "model.onnx"),
                                providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
    print("tagger on", sess.get_providers()[0])
    inp = sess.get_inputs()[0]
    size = inp.shape[1]
    with open(folder / "selected_tags.csv", encoding="utf-8") as f:
        tags = list(csv.DictReader(f))
    names = [t["name"] for t in tags]
    cats = np.array([int(t["category"]) for t in tags])
    lex = db.lexicon_lookup(con, "danbooru")

    def prep(path: Path) -> np.ndarray:
        im = Image.open(path).convert("RGB")
        side = max(im.size)
        canvas = Image.new("RGB", (side, side), (255, 255, 255))
        canvas.paste(im, ((side - im.width) // 2, (side - im.height) // 2))
        arr = np.asarray(canvas.resize((size, size), Image.Resampling.BICUBIC), dtype=np.float32)
        return arr[:, :, ::-1]  # the model was trained on BGR

    for m in todo(con, "tagger", TAGGER_V):
        rows = []
        cuts = cuts_of(con, m["media_id"])
        batch = np.stack([prep(thumb_path(m["sha256"], c["keyframe_ticks"])) for c in cuts])
        probs = np.concatenate([sess.run(None, {inp.name: batch[i:i + 16]})[0] for i in range(0, len(batch), 16)])
        for c, p in zip(cuts, probs):
            sp = span(m, c)
            rating = np.where(cats == 9)[0]
            r = rating[np.argmax(p[rating])]
            rows.append({"kind": "rating", "value": names[r], "num": round(float(p[r]), 3), **sp})
            for i in np.where((cats == 0) & (p >= GENERAL_MIN))[0]:
                rows.append({"kind": "wd_tag", "value": names[i], "num": round(float(p[i]), 3), **sp})
                kind, value = lex.get(names[i], (None, None))
                if kind and value:
                    rows.append({"kind": kind, "value": value, "num": round(float(p[i]), 3),
                                 "basis": f"danbooru {names[i]}", **sp})
            for i in np.where((cats == 4) & (p >= CHARACTER_MIN))[0]:
                eid = catalog.resolve_entity(con, "character", [names[i]])
                rows.append({"kind": "character", "ref_id": eid, "num": round(float(p[i]), 3),
                             "basis": f"wd {names[i]} {p[i]:.2f}", **sp})
        db.put_facts(con, m["media_id"], TAGGER_V, rows)
        db.job_done(con, m["media_id"], "tagger", TAGGER_V)
        con.commit()
        print(f"tagger {m['media_id']:5d} {len(cuts):4d} cuts {len(rows):5d} facts")


# ---------------------------------------------------------------------------
# SigLIP 2 image embeddings
# ---------------------------------------------------------------------------

def stage_embed(con):
    import torch
    from transformers import AutoModel, AutoProcessor

    name = "google/siglip2-so400m-patch14-384"
    model = AutoModel.from_pretrained(name, cache_dir=MODELS, dtype=torch.float16).to("cuda").eval()
    proc = AutoProcessor.from_pretrained(name, cache_dir=MODELS)
    for m in todo(con, "embed", EMBED_V):
        cuts = cuts_of(con, m["media_id"])
        for i in range(0, len(cuts), 32):
            part = cuts[i:i + 32]
            ims = [Image.open(thumb_path(m["sha256"], c["keyframe_ticks"])).convert("RGB") for c in part]
            with torch.no_grad():
                x = proc(images=ims, return_tensors="pt").to("cuda")
                f = model.get_image_features(pixel_values=x["pixel_values"].half())
                f = getattr(f, "pooler_output", f)  # transformers 5 returns a model output
                f = torch.nn.functional.normalize(f.float(), dim=-1).cpu().numpy()
            for c, v in zip(part, f):
                db.put_embedding(con, m["media_id"], c["keyframe_ticks"], EMBED_MODEL, v)
        db.job_done(con, m["media_id"], "embed", EMBED_V)
        con.commit()
        print(f"embed  {m['media_id']:5d} {len(cuts):4d} cuts")


# ---------------------------------------------------------------------------
# speech and lyrics
# ---------------------------------------------------------------------------

def decode_audio(path: str) -> np.ndarray | None:
    """16 kHz mono float32 through ffmpeg; None when there is no audio.
    Decoding here keeps faster-whisper off PyAV, whose API keeps moving."""
    import subprocess

    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
        capture_output=True,
    )
    if proc.returncode != 0 or not proc.stdout:
        return None
    return np.frombuffer(proc.stdout, dtype="<f4")


def _cuda12_dlls() -> None:
    """CTranslate2 needs CUDA 12 cuBLAS and cuDNN, which ship as pip wheels
    outside the DLL search path. Run speech in its own process: cuDNN 9 has
    the same file name for CUDA 12 and 13, so it must not share one with torch."""
    import os
    import site

    for root in site.getsitepackages():
        for d in Path(root).glob("nvidia/*/bin"):
            os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
            os.add_dll_directory(str(d))


def stage_speech(con):
    _cuda12_dlls()
    from faster_whisper import WhisperModel

    model = WhisperModel("large-v3", device="cuda", compute_type="float16", download_root=str(MODELS))
    for m in todo(con, "speech", SPEECH_V, kinds=("video",)):
        path = db.media_path(con, m["media_id"])
        rows = []
        audio = decode_audio(path)
        try:
            if audio is None:
                raise ValueError("no audio stream")
            segs, info = model.transcribe(audio, vad_filter=True, beam_size=5)
            for s in segs:
                text = s.text.strip()
                if text and s.no_speech_prob < 0.6:
                    rows.append({
                        "kind": "speech", "value": text,
                        "num": round(float(np.exp(s.avg_logprob)), 3),
                        "t0_ticks": round(Fraction(s.start).limit_denominator(1000) * S),
                        "t1_ticks": round(Fraction(s.end).limit_denominator(1000) * S),
                        "basis": f"lang {info.language} {info.language_probability:.2f}",
                    })
        except Exception as e:  # no audio stream is normal for some clips
            if "audio" not in str(e).lower():
                db.job_failed(con, m["media_id"], "speech", SPEECH_V, str(e))
                con.commit()
                print(f"speech {m['media_id']:5d} FAILED {e}")
                continue
        db.put_facts(con, m["media_id"], SPEECH_V, rows)
        db.job_done(con, m["media_id"], "speech", SPEECH_V)
        con.commit()
        print(f"speech {m['media_id']:5d} {len(rows):4d} segments")


STAGES = {"tagger": stage_tagger, "embed": stage_embed, "speech": stage_speech}

if __name__ == "__main__":
    con = db.connect(sys.argv[1])
    for stage in sys.argv[2:] or list(STAGES):
        STAGES[stage](con)
    con.close()
