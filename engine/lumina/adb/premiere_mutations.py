"""Privacy-conscious JSONL mutation journal primitives."""

from __future__ import annotations

import hashlib
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_JOURNAL = Path(tempfile.gettempdir()) / "adb-mcp-premiere-mutations.jsonl"


def text_digest(value: str) -> dict[str, Any]:
    encoded = value.encode("utf-8")
    return {"sha256": hashlib.sha256(encoded).hexdigest(), "utf8Bytes": len(encoded)}


def redact_sensitive(value: Any, key: str = "") -> Any:
    lowered = key.lower()
    if isinstance(value, str) and any(token in lowered for token in ("text", "caption", "subtitle", "transcript")):
        return {"redactedText": text_digest(value)}
    if isinstance(value, dict):
        return {str(k): redact_sensitive(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_sensitive(item, key) for item in value]
    return value


def append_journal(entry: dict[str, Any], path: Path = DEFAULT_JOURNAL) -> dict[str, Any]:
    payload = redact_sensitive(dict(entry))
    payload.setdefault("recordedAt", datetime.now(timezone.utc).isoformat())
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    return {"path": str(path), "entry": payload}
