"""SigLIP text vectors for scene search, served over stdin/stdout.

The engine's environment has no torch, so lumina.sourcedb.textvec starts this
once with the analyzers' environment and keeps it: loading the model takes
seconds, encoding a shot plan's lines a few milliseconds. The vectors share
the image embeddings' space (run.py stage_embed, same model).

Protocol, one JSON object per line:
    in:  {"texts": ["녹색 머리에 야구모자를 쓴 소녀", ...]}
    out: {"shape": [n, d], "f16": "<base64 little-endian float16, L2-normalised>"}
         or {"error": "..."}
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

MODELS = Path.home() / ".lumina" / "models"
NAME = "google/siglip2-so400m-patch14-384"   # run.py EMBED_MODEL "siglip2-so400m-384"
MAX_TOKENS = 64                              # SigLIP was trained on max_length padding


def main() -> None:
    import numpy as np
    import torch
    from transformers import AutoModel, AutoProcessor
    from transformers.utils import logging

    logging.set_verbosity_error()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.float16 if device == "cuda" else torch.float32
    model = AutoModel.from_pretrained(NAME, cache_dir=MODELS, dtype=dtype).to(device).eval()
    proc = AutoProcessor.from_pretrained(NAME, cache_dir=MODELS)
    out = sys.stdout
    out.write(json.dumps({"ready": True, "device": device}) + "\n")
    out.flush()
    for line in sys.stdin:
        try:
            texts = json.loads(line)["texts"]
            vecs = []
            for i in range(0, len(texts), 64):
                x = proc(text=texts[i:i + 64], padding="max_length", max_length=MAX_TOKENS, truncation=True,
                         return_tensors="pt").to(device)
                with torch.no_grad():
                    f = model.get_text_features(**x)
                    f = getattr(f, "pooler_output", f)  # transformers 5 returns a model output
                    vecs.append(torch.nn.functional.normalize(f.float(), dim=-1).cpu().numpy())
            v = np.concatenate(vecs).astype("<f2") if vecs else np.zeros((0, 0), "<f2")
            reply = {"shape": list(v.shape), "f16": base64.b64encode(v.tobytes()).decode("ascii")}
        except Exception as e:  # a bad request must not end the server
            reply = {"error": f"{type(e).__name__}: {e}"}
        out.write(json.dumps(reply) + "\n")
        out.flush()


if __name__ == "__main__":
    main()
