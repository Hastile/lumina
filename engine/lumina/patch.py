"""Reversible patches over the JSON document.

The document is a tree of dicts (lists only appear as leaf values that are
replaced wholesale), so two primitives are enough:

    {"op": "set", "path": [...], "value": v}   create or replace a key
    {"op": "del", "path": [...]}               remove a key

Applying a patch returns its exact inverse, which is what makes undo, redo,
transaction rollback and op-log replay trivially correct. The same patches are
broadcast to the UI so it can mirror the document without refetching.
"""

from __future__ import annotations

import copy
from typing import Any

Path = list[str]
Patch = dict[str, Any]

_MISSING = object()


class PatchError(Exception):
    pass


def get(doc: dict, path: Path, default: Any = _MISSING) -> Any:
    node: Any = doc
    for key in path:
        if not isinstance(node, dict) or key not in node:
            if default is _MISSING:
                raise PatchError(f"path not found: {'/'.join(path)}")
            return default
        node = node[key]
    return node


def _parent(doc: dict, path: Path) -> dict:
    if not path:
        raise PatchError("empty path")
    parent = get(doc, path[:-1])
    if not isinstance(parent, dict):
        raise PatchError(f"parent of {'/'.join(path)} is not an object")
    return parent


def apply(doc: dict, patch: Patch) -> Patch:
    """Apply one patch in place and return its inverse."""
    path = list(patch["path"])
    parent = _parent(doc, path)
    key = path[-1]
    old = parent.get(key, _MISSING)
    if patch["op"] == "set":
        parent[key] = copy.deepcopy(patch["value"])
    elif patch["op"] == "del":
        if old is _MISSING:
            raise PatchError(f"cannot delete missing {'/'.join(path)}")
        del parent[key]
    else:
        raise PatchError(f"unknown op {patch['op']!r}")
    if old is _MISSING:
        return {"op": "del", "path": path}
    return {"op": "set", "path": path, "value": old}


def apply_all(doc: dict, patches: list[Patch]) -> list[Patch]:
    """Apply in order; return inverses in undo order. All-or-nothing."""
    inverses: list[Patch] = []
    try:
        for p in patches:
            inverses.append(apply(doc, p))
    except Exception:
        for inv in reversed(inverses):
            apply(doc, inv)
        raise
    inverses.reverse()
    return inverses


class Mutator:
    """Records patches while a command edits the document.

    Commands read the document normally and write only through ``set``/``delete``
    so every change is captured with its inverse.
    """

    def __init__(self, doc: dict):
        self.doc = doc
        self.forward: list[Patch] = []
        self.inverse: list[Patch] = []

    def set(self, path: Path, value: Any) -> None:
        patch = {"op": "set", "path": list(path), "value": copy.deepcopy(value)}
        self.inverse.append(apply(self.doc, patch))
        self.forward.append(patch)

    def delete(self, path: Path) -> None:
        patch = {"op": "del", "path": list(path)}
        self.inverse.append(apply(self.doc, patch))
        self.forward.append(patch)

    def rollback(self) -> None:
        for inv in reversed(self.inverse):
            apply(self.doc, inv)
        self.forward.clear()
        self.inverse.clear()

    def undo_patches(self) -> list[Patch]:
        return list(reversed(self.inverse))
