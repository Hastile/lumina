"""Write the document and command JSON Schemas consumed by the UI codegen."""

from __future__ import annotations

import json
from pathlib import Path

from .commands.registry import REGISTRY
from .model import Project


def write_schemas(out_dir: str) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "document.schema.json").write_text(json.dumps(Project.model_json_schema(mode="serialization"), indent=2), encoding="utf-8")
    (out / "commands.json").write_text(json.dumps(REGISTRY.describe(), indent=2), encoding="utf-8")
    print(f"wrote schemas to {out.resolve()}")
