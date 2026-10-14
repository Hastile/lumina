"""Editing session: owns the document, undo/redo, transactions, persistence
and the op-log used for crash recovery.

Every mutation goes through ``execute``. It runs a registered command against
a ``Mutator``; on failure the partial patches are rolled back, so a command is
always all-or-nothing. The resulting patches are appended to the op-log and
broadcast to listeners (RPC clients / the GUI).
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from pydantic import ValidationError

from . import commands as _commands  # noqa: F401  (registers commands)
from .commands.registry import REGISTRY, CommandError, Context
from .model import new_project, validate_project
from .patch import Mutator, Patch, apply_all

FILE_FORMAT = "lumina-project"
FILE_VERSION = 1

Listener = Callable[[dict], None]


@dataclass
class UndoEntry:
    label: str
    forward: list[Patch]
    inverse: list[Patch]


@dataclass
class _Txn:
    label: str
    forward: list[Patch] = field(default_factory=list)
    inverse: list[Patch] = field(default_factory=list)


class Session:
    def __init__(self, services: Any = None, doc: dict | None = None):
        self.services = services
        self.doc: dict = doc or new_project()
        self.version = 0
        self.path: str | None = None
        self.saved_version = 0
        self.undo_stack: list[UndoEntry] = []
        self.redo_stack: list[UndoEntry] = []
        self.txn: _Txn | None = None
        self.listeners: list[Listener] = []
        self.lock = threading.RLock()

    # ------------------------------------------------------------ events

    def subscribe(self, fn: Listener) -> Callable[[], None]:
        self.listeners.append(fn)
        return lambda: self.listeners.remove(fn) if fn in self.listeners else None

    def _emit(self, event: dict) -> None:
        for fn in list(self.listeners):
            try:
                fn(event)
            except Exception:  # noqa: BLE001 - a broken client must not break editing
                pass

    def _publish(self, kind: str, patches: list[Patch], label: str) -> None:
        self.version += 1
        self._append_oplog({"v": self.version, "kind": kind, "label": label, "patches": patches})
        self._emit({"type": "patch", "version": self.version, "kind": kind, "label": label, "patches": patches, **self.history_state()})

    def history_state(self) -> dict:
        return {
            "can_undo": bool(self.undo_stack),
            "can_redo": bool(self.redo_stack),
            "undo_label": self.undo_stack[-1].label if self.undo_stack else None,
            "redo_label": self.redo_stack[-1].label if self.redo_stack else None,
            "dirty": self.version != self.saved_version,
            "in_transaction": self.txn is not None,
        }

    # ------------------------------------------------------------ commands

    def execute(self, name: str, params: dict | None = None, label: str | None = None) -> dict:
        with self.lock:
            spec = REGISTRY.get(name)
            try:
                parsed = spec.params.model_validate(params or {})
            except ValidationError as exc:
                raise CommandError("BAD_PARAMS", f"invalid params for {name}", errors=json.loads(exc.json())) from None
            m = Mutator(self.doc)
            try:
                result = spec.handler(Context(m=m, services=self.services), parsed)
            except CommandError:
                m.rollback()
                raise
            except Exception as exc:
                m.rollback()
                raise CommandError("INTERNAL", f"{name} failed: {exc}") from exc
            if not m.forward:
                return {"result": result, "version": self.version, "changed": False}
            label = label or name
            if spec.undoable:
                if self.txn is not None:
                    self.txn.forward.extend(m.forward)
                    self.txn.inverse = m.undo_patches() + self.txn.inverse
                else:
                    self.undo_stack.append(UndoEntry(label, m.forward, m.undo_patches()))
                    self.redo_stack.clear()
            self._publish("command", m.forward, label)
            return {"result": result, "version": self.version, "changed": True}

    def undo(self) -> dict:
        with self.lock:
            if self.txn is not None:
                raise CommandError("IN_TRANSACTION", "commit or roll back the transaction first")
            if not self.undo_stack:
                raise CommandError("NOTHING_TO_UNDO", "nothing to undo")
            entry = self.undo_stack.pop()
            apply_all(self.doc, entry.inverse)
            self.redo_stack.append(entry)
            self._publish("undo", entry.inverse, entry.label)
            return {"undone": entry.label, "version": self.version}

    def redo(self) -> dict:
        with self.lock:
            if self.txn is not None:
                raise CommandError("IN_TRANSACTION", "commit or roll back the transaction first")
            if not self.redo_stack:
                raise CommandError("NOTHING_TO_REDO", "nothing to redo")
            entry = self.redo_stack.pop()
            apply_all(self.doc, entry.forward)
            self.undo_stack.append(entry)
            self._publish("redo", entry.forward, entry.label)
            return {"redone": entry.label, "version": self.version}

    # ------------------------------------------------------------ transactions

    def begin(self, label: str = "Edit") -> dict:
        with self.lock:
            if self.txn is not None:
                raise CommandError("IN_TRANSACTION", "a transaction is already open")
            self.txn = _Txn(label)
            return {"version": self.version}

    def commit(self) -> dict:
        with self.lock:
            if self.txn is None:
                raise CommandError("NO_TRANSACTION", "no open transaction")
            txn, self.txn = self.txn, None
            if txn.forward:
                self.undo_stack.append(UndoEntry(txn.label, txn.forward, txn.inverse))
                self.redo_stack.clear()
            self._emit({"type": "history", **self.history_state()})
            return {"version": self.version, "patches": len(txn.forward)}

    def rollback(self) -> dict:
        with self.lock:
            if self.txn is None:
                raise CommandError("NO_TRANSACTION", "no open transaction")
            txn, self.txn = self.txn, None
            if txn.inverse:
                apply_all(self.doc, txn.inverse)
                self._publish("rollback", txn.inverse, txn.label)
            return {"version": self.version}

    # ------------------------------------------------------------ persistence

    def snapshot(self) -> dict:
        with self.lock:
            return {"version": self.version, "path": self.path, "document": self.doc, **self.history_state()}

    def new(self, name: str = "Untitled") -> dict:
        with self.lock:
            self._reset(new_project(name), None)
            return self.snapshot()

    def save(self, path: str | None = None) -> dict:
        with self.lock:
            target = path or self.path
            if not target:
                raise CommandError("NO_PATH", "project has never been saved; pass a path")
            validate_project(self.doc)
            payload = {"format": FILE_FORMAT, "file_version": FILE_VERSION, "version": self.version,
                       "saved_at": time.time(), "document": self.doc}
            _atomic_write(Path(target), json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
            old_log = self._oplog_path()
            self.path = str(Path(target).resolve())
            self.saved_version = self.version
            for log in {old_log, self._oplog_path()}:
                if log and log.exists():
                    log.unlink()
            self._emit({"type": "saved", "path": self.path, **self.history_state()})
            return {"path": self.path, "version": self.version}

    def open(self, path: str) -> dict:
        with self.lock:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            if raw.get("format") != FILE_FORMAT:
                raise CommandError("BAD_FILE", f"{path} is not a Lumina project")
            # normalizing through the schema also migrates older files (fills new fields with defaults)
            doc = validate_project(raw["document"]).model_dump(mode="json")
            self._reset(doc, str(Path(path).resolve()), version=int(raw.get("version", 0)))
            recovered = self._replay_oplog()
            snap = self.snapshot()
            snap["recovered_patches"] = recovered
            return snap

    def _reset(self, doc: dict, path: str | None, version: int = 0) -> None:
        self.doc = doc
        self.path = path
        self.version = version
        self.saved_version = version
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.txn = None
        self._emit({"type": "reset", "version": self.version, "document": self.doc, "path": path, **self.history_state()})

    # ------------------------------------------------------------ op-log (crash recovery)

    def _oplog_path(self) -> Path | None:
        if not self.path:
            return None
        return Path(self.path + ".oplog")

    def _append_oplog(self, entry: dict) -> None:
        log = self._oplog_path()
        if log is None:
            return
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")
            fh.flush()

    def _replay_oplog(self) -> int:
        """Re-apply patches written after the last save (crash recovery)."""
        log = self._oplog_path()
        if log is None or not log.exists():
            return 0
        count = 0
        for line in log.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                break  # torn final line from a crash
            if entry["v"] <= self.version:
                continue
            apply_all(self.doc, entry["patches"])
            self.version = entry["v"]
            count += len(entry["patches"])
        validate_project(self.doc)
        return count


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
