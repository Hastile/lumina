"""Scene understanding by Claude, at the lowest token cost that still works.

Local models measure; they do not understand a scene well enough to say
what it is for. Claude does, but only cheaply if it never sits in a large
context: each call here is a fresh headless session with a short system
prompt, one contact sheet of up to 12 keyframes (~1.5k tokens), and the
local facts for those keyframes as text. It answers in a fixed JSON shape,
one entry per tile, which becomes proposed facts.

Everything it writes is 'proposed'. A fact becomes accepted only when a
person accepts it or an independent source agrees (see verify in ingest).
So Claude is never shown another producer's unconfirmed guess about who or
what is on screen: a guess it repeats would then "agree" with itself. It
names works and characters from what it sees, in Korean and in the original
spelling, and catalog.resolve_entity turns every spelling into one entity.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Optional

from PIL import Image, ImageDraw, ImageFont

from . import catalog, db
from .ingest import thumb_path
from ..timebase import TICKS_PER_SECOND

S = TICKS_PER_SECOND
VERSION = "claude-sonnet@3"
MODEL = "sonnet"
PER_SHEET = 12
COLS = 4
CELL = (384, 288)

SHOTS = ["extreme_close", "close", "medium", "wide", "insert", "ui", "text", "collage"]
SOURCE_TYPES = ["trailer", "pv", "mv", "gameplay", "key_visual", "illustration", "fanart", "design_sheet",
                "screenshot", "sns_post", "article", "store_page", "search_result", "recording", "meme", "logo"]

NAME = {
    "type": "object",
    "properties": {"ko": {"type": "string", "description": "한국에서 부르는 이름"},
                   "original": {"type": "string", "description": "원어 표기 (일본어 등)"},
                   "romaji": {"type": "string", "description": "로마자/영어 표기 (예: Gotoh Hitori)"}},
    "required": ["ko", "original", "romaji"],
}

SCHEMA = {
    "type": "object",
    "properties": {"tiles": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "n": {"type": "integer"},
            "desc": {"type": "string", "description": "한국어 한 줄, 25자 안팎: 누가 무엇을 하는 화면인지"},
            "shot": {"type": "string", "enum": SHOTS},
            "source_type": {"type": "string", "enum": SOURCE_TYPES},
            "characters": {"type": "array", "items": NAME, "description": "알아본 인물만. 모르면 비움"},
            "works": {"type": "array", "items": NAME, "description": "화면이 속한 작품. 모르면 비움"},
            "variant": {"type": "string", "enum": ["", "original", "copy", "official", "fan"],
                        "description": "원작/모방작(copy)/공식/팬메이드. 모르면 빈 문자열"},
            "text": {"type": "string", "description": "화면 속 글자 원문, 40자 이내. 없으면 빈 문자열"},
            "text_overlay": {"type": "number", "description": "덧씌운 글자가 화면을 덮는 비율 0~1"},
            "subject_x": {"type": "number", "description": "주 피사체 중심의 가로 위치 0~1"},
            "subject_area": {"type": "number", "description": "주 피사체가 화면을 차지하는 비율 0~1"},
            "mood": {"type": "array", "items": {"type": "string"}, "description": "분위기 1~2개 (한국어)"},
            "action": {"type": "array", "items": {"type": "string"}, "description": "동작 1~2개 (한국어)"},
            "use_case": {"type": "array", "items": {"type": "string"},
                         "description": "이 컷이 어울리는 내레이션 1~2개, 짧은 한국어 구절"},
        },
        "required": ["n", "desc", "shot", "source_type", "characters", "works", "variant", "text",
                     "text_overlay", "subject_x", "subject_area", "mood", "action", "use_case"],
    }}},
    "required": ["tiles"],
}

SYSTEM = (
    "너는 쇼츠 편집용 소스 DB에 장면 메타데이터를 쓰는 분석가다. 콘택트 시트의 번호 붙은 타일마다 JSON 한 "
    "항목을 쓴다. 보이는 것만 쓰고 추측하지 않는다. 작품과 인물은 화면 속 글자, 파일 제목, 대사, 또는 네가 "
    "확실히 아는 캐릭터의 생김새로 알아볼 때만 쓰고, 한국어·원어·로마자 이름을 각 칸에 하나씩 쓴다. 작품명에 '원작', '모방작' 같은 말을 붙이지 않고 variant에 쓴다. 모르는 인물은 "
    "이름 대신 desc에 생김새로 쓴다(예: 분홍 머리 소녀). 타일마다 따로 판단한다: 같은 시트라고 같은 작품이 "
    "아니다. 설명은 검색어가 될 명사와 동사를 담은 한국어 한 줄."
)


def _font(size: int):
    for name in ("malgunbd.ttf", "arialbd.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def contact_sheet(thumbs: list[Path], out: Path) -> None:
    rows = (len(thumbs) + COLS - 1) // COLS
    sheet = Image.new("RGB", (COLS * CELL[0], rows * CELL[1]), (24, 24, 24))
    draw = ImageDraw.Draw(sheet)
    font = _font(30)
    for i, t in enumerate(thumbs):
        with Image.open(t) as im:
            im = im.convert("RGB")
            im.thumbnail((CELL[0] - 4, CELL[1] - 4))
            x, y = (i % COLS) * CELL[0], (i // COLS) * CELL[1]
            sheet.paste(im, (x + (CELL[0] - im.width) // 2, y + (CELL[1] - im.height) // 2))
            draw.rectangle([x + 2, y + 2, x + 46, y + 40], fill=(255, 220, 0))
            draw.text((x + 8, y + 4), str(i + 1), fill=(0, 0, 0), font=font)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, quality=88)


def _tile_context(con, m, c) -> str:
    """What is already known about one tile, as a short line of text."""
    t0, t1 = c["start_ticks"], c["end_ticks"]
    parts = []
    facts = con.execute(
        "SELECT f.kind, f.value, f.num, f.status, e.name FROM fact f LEFT JOIN entity e ON e.entity_id = f.ref_id "
        "WHERE f.media_id = ? AND f.kind IN ('title','credit','work','speech','character') "
        "AND (f.status = 'accepted' OR f.kind IN ('speech')) "
        "AND (f.t0_ticks IS NULL OR (f.t0_ticks < ? AND f.t1_ticks > ?) OR (f.t0_ticks = f.t1_ticks AND f.t0_ticks BETWEEN ? AND ?)) "
        "ORDER BY f.kind, f.num DESC",
        (m["media_id"], max(t1, t0 + 1), t0, t0, t1),
    ).fetchall()
    by: dict[str, list[str]] = {}
    for f in facts:
        by.setdefault(f["kind"], []).append(f["name"] or f["value"])
    for k, limit in (("title", 1), ("credit", 1), ("work", 2), ("character", 3), ("speech", 2)):
        if k in by:
            parts.append(f"{k}: {' / '.join(dict.fromkeys(by[k][:limit]))[:80]}")
    used = con.execute(
        "SELECT caption FROM usage WHERE media_id = ? AND caption IS NOT NULL AND src_in_ticks <= ? AND src_out_ticks >= ?",
        (m["media_id"], t1, t0),
    ).fetchall()
    if used:
        parts.append("used under: " + " | ".join(u[0][:50] for u in used[:2]))
    if m["kind"] == "video":
        parts.append(f"{t0 / S:.1f}-{t1 / S:.1f}s")
    return "; ".join(parts)



def ask_claude(sheet: Path, lines: list[str]) -> list[dict]:
    prompt = (
        f"{sheet.name} 를 Read로 열어 보고 타일 {len(lines)}개를 분석해.\n\n"
        "타일별로 확인된 정보 (파일 제목, 확정된 작품과 인물, 대사, 이 컷이 쓰인 내레이션):\n"
        + "\n".join(f"{i + 1}. {l}" for i, l in enumerate(lines))
    )
    proc = subprocess.run(
        ["claude", "-p", prompt, "--model", MODEL, "--system-prompt", SYSTEM, "--tools", "Read",
         "--allowedTools", "Read", "--strict-mcp-config", "--no-session-persistence",
         "--output-format", "json", "--json-schema", json.dumps(SCHEMA, ensure_ascii=False)],
        cwd=sheet.parent, capture_output=True, text=True, encoding="utf-8", timeout=600,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-1000:] or proc.stdout[-1000:])
    out = json.loads(proc.stdout)
    data = out.get("structured_output") or json.loads(out["result"])
    usage = out.get("usage", {})
    print(f"    tokens in {usage.get('input_tokens', 0)}+cache {usage.get('cache_read_input_tokens', 0)}"
          f"/{usage.get('cache_creation_input_tokens', 0)} out {usage.get('output_tokens', 0)}"
          f"  ${out.get('total_cost_usd', 0):.4f}")
    return data["tiles"]


def _facts_from_tile(con, tile: dict, sp: dict) -> list[dict]:
    rows = [
        {"kind": "description", "value": tile["desc"], **sp},
        {"kind": "shot", "value": tile["shot"], **sp},
        {"kind": "source_type", "value": tile["source_type"], **sp},
    ]
    if tile.get("variant"):
        rows.append({"kind": "variant", "value": tile["variant"], **sp})
    for k in ("text_overlay", "subject_x", "subject_area"):
        v = tile.get(k)
        if isinstance(v, (int, float)) and 0 <= v <= 1:
            rows.append({"kind": k, "num": round(float(v), 2), **sp})
    if tile.get("text"):
        rows.append({"kind": "ocr", "value": tile["text"], **sp})
    for k, kind in (("mood", "mood"), ("action", "action"), ("use_case", "use_case")):
        for v in tile.get(k, [])[:2]:
            rows.append({"kind": kind, "value": v, **sp})
    work_ref = None
    for w in tile.get("works", []):
        eid = catalog.resolve_entity(con, "work", [w.get("ko", ""), w.get("original", ""), w.get("romaji", "")])
        if eid:
            rows.append({"kind": "work", "ref_id": eid, "basis": f"claude saw {w.get('original') or w.get('ko')}", **sp})
            work_ref = work_ref or con.execute("SELECT catalog_ref FROM entity WHERE entity_id = ?", (eid,)).fetchone()[0]
    for c in tile.get("characters", []):
        eid = catalog.resolve_entity(con, "character", [c.get("ko", ""), c.get("original", ""), c.get("romaji", "")],
                                     ip_ref=work_ref)
        if eid:
            rows.append({"kind": "character", "ref_id": eid, "basis": f"claude saw {c.get('original') or c.get('ko')}", **sp})
    return rows




def run(con, media_ids: Optional[list[int]] = None, log: Callable[[str], None] = print, *, redo: bool = False) -> None:
    """Describe every cut of the given media (default: all never described).

    A new VERSION does not re-describe what an older one did: that costs
    tokens, so it happens only with redo=True."""
    todo = [
        m for m in con.execute("SELECT * FROM media WHERE kind IN ('video','image') AND status != 'dropped' ORDER BY kind DESC, media_id")
        if (media_ids is None or m["media_id"] in media_ids)
        and (redo or db.job_version(con, m["media_id"], "describe") is None)
    ]
    # Video sheets hold one media's cuts in order; images share sheets.
    groups: list[list[tuple]] = []
    images: list[tuple] = []
    for m in todo:
        cuts = con.execute("SELECT * FROM cut WHERE media_id = ? ORDER BY idx", (m["media_id"],)).fetchall()
        if m["kind"] == "image":
            images += [(m, c) for c in cuts]
        else:
            groups += [[(m, c) for c in cuts[i:i + PER_SHEET]] for i in range(0, len(cuts), PER_SHEET)]
    groups += [images[i:i + PER_SHEET] for i in range(0, len(images), PER_SHEET)]
    pending: dict[int, list[dict]] = {}
    left = {m["media_id"]: sum(1 for g in groups for mm, _ in g if mm["media_id"] == m["media_id"]) for m in todo}
    with tempfile.TemporaryDirectory(prefix="lumina-describe-") as tmp:
        for gi, group in enumerate(groups):
            sheet = Path(tmp) / f"sheet_{gi:04d}.jpg"
            contact_sheet([thumb_path(m["sha256"], c["keyframe_ticks"]) for m, c in group], sheet)
            log(f"sheet {gi + 1}/{len(groups)}: {len(group)} tiles")
            try:
                tiles = {t["n"]: t for t in ask_claude(sheet, [_tile_context(con, m, c) for m, c in group])}
            except Exception as e:
                log(f"    FAILED: {e}")
                for m, _ in group:
                    db.job_failed(con, m["media_id"], "describe", VERSION, str(e))
                con.commit()
                continue
            for i, (m, c) in enumerate(group):
                tile = tiles.get(i + 1)
                if tile:
                    sp = {} if m["kind"] == "image" else {"t0_ticks": c["start_ticks"], "t1_ticks": c["end_ticks"]}
                    pending.setdefault(m["media_id"], []).extend(_facts_from_tile(con, tile, sp))
                left[m["media_id"]] -= 1
                if left[m["media_id"]] == 0:
                    db.put_facts(con, m["media_id"], VERSION, pending.pop(m["media_id"], []))
                    db.job_done(con, m["media_id"], "describe", VERSION)
            con.commit()
