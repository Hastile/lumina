"""Subprocess boundary to the HyperFrames composition parser.

The composition contract (`data-start`/`data-duration`/`data-track-index`,
reference-based start expressions, sub-composition nesting) is implemented in
TypeScript in a separate checkout. Reimplementing it here would drift from
upstream without saying so, so the parse is shelled out to
`node/hyperframes_bridge.mjs` and everything downstream stays pure Python.

This module is the only place that knows node exists. Keep it thin: the
flattening, quantization and lint rules live in hyperframes_timeline.py and
hyperframes_treatment_lint.py so they can be unit-tested with fixture JSON
and no node install.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

# The bridge script's own payload version. Bump when its output shape
# changes so a stale script is caught rather than silently misread.
EXPECTED_BRIDGE_VERSION = 1

_REPO_ROOT_ENV = "HYPERFRAMES_REPO_ROOT"
_NODE_ENV = "HYPERFRAMES_NODE_BIN"


class BridgeError(Exception):
    """A typed failure with a code the MCP layer can pass through unchanged."""

    def __init__(self, code: str, message: str, details: Optional[dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def as_dict(self) -> dict[str, Any]:
        return {"status": "ERROR", "code": self.code, "message": self.message, **self.details}


def bridge_script_path() -> Path:
    """The .mjs helper, resolved relative to this file rather than the cwd."""
    return Path(__file__).resolve().parent / "node" / "hyperframes_bridge.mjs"


def resolve_node_binary(explicit: Optional[str] = None) -> str:
    candidate = explicit or os.environ.get(_NODE_ENV) or "node"
    found = shutil.which(candidate)
    if not found:
        raise BridgeError(
            "BRIDGE_NODE_NOT_FOUND",
            f"node executable {candidate!r} is not on PATH. Set {_NODE_ENV} to an absolute path.",
        )
    return found


def resolve_repo_root(explicit: Optional[str] = None) -> Path:
    """Locate the HyperFrames checkout that owns the parser.

    There is no default worth guessing: the parser is a build artifact of a
    checkout this repo does not contain, so an unset root is a configuration
    error, not something to paper over with a search.
    """
    raw = explicit or os.environ.get(_REPO_ROOT_ENV)
    if not raw:
        raise BridgeError(
            "BRIDGE_REPO_ROOT_UNSET",
            f"Set {_REPO_ROOT_ENV} (or pass repo_root) to the HyperFrames checkout that "
            "contains packages/parsers.",
        )
    root = Path(raw).expanduser()
    if not (root / "packages" / "parsers").is_dir():
        raise BridgeError(
            "BRIDGE_REPO_ROOT_INVALID",
            f"{root} does not look like a HyperFrames checkout (no packages/parsers).",
        )
    return root


def read_composition(
    html_path: "str | Path",
    *,
    repo_root: Optional[str] = None,
    node_path: Optional[str] = None,
    max_depth: int = 20,
    timeout_seconds: float = 60.0,
) -> dict[str, Any]:
    """Parse a composition and every sub-composition it reaches.

    Returns the bridge payload: one `documents` entry per composition file,
    each with its parsed `elements`, the `customAttributes` pass that carries
    `data-treatment`, and the statically extracted GSAP `animations`.
    Raises BridgeError; never returns a failure dict.
    """
    script = bridge_script_path()
    if not script.is_file():
        raise BridgeError("BRIDGE_SCRIPT_MISSING", f"{script} is missing from this checkout.")

    composition = Path(html_path).expanduser()
    if not composition.is_absolute():
        composition = composition.resolve()
    if not composition.is_file():
        raise BridgeError("BRIDGE_HTML_NOT_FOUND", f"No composition at {composition}")

    root = resolve_repo_root(repo_root)
    node = resolve_node_binary(node_path)
    # Forward slashes throughout: node on Windows accepts them, and it keeps
    # the JSON argument free of backslash escaping.
    request = json.dumps({
        "htmlPath": composition.as_posix(),
        "repoRoot": root.as_posix(),
        "maxDepth": max_depth,
    })

    try:
        completed = subprocess.run(
            [node, str(script), request],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout_seconds, check=False,
        )
    except subprocess.TimeoutExpired:
        raise BridgeError("BRIDGE_TIMEOUT", f"The composition parser timed out after {timeout_seconds}s.")
    except OSError as exc:
        raise BridgeError("BRIDGE_SPAWN_FAILED", str(exc))

    stdout = (completed.stdout or "").strip()
    if not stdout:
        raise BridgeError(
            "BRIDGE_NO_OUTPUT",
            f"The composition parser produced no output (exit {completed.returncode}).",
            {"stderr": (completed.stderr or "")[-2000:]},
        )
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise BridgeError(
            "BRIDGE_BAD_OUTPUT",
            f"The composition parser did not return JSON: {exc}",
            {"stdout": stdout[:2000], "stderr": (completed.stderr or "")[-2000:]},
        )

    if payload.get("status") != "SUCCESS":
        # The script reports its own typed failures in-band (see its fail()),
        # so pass the code straight through instead of flattening it.
        raise BridgeError(
            payload.get("code") or "BRIDGE_FAILED",
            payload.get("message") or "The composition parser failed.",
        )

    version = payload.get("bridgeVersion")
    if version != EXPECTED_BRIDGE_VERSION:
        raise BridgeError(
            "BRIDGE_VERSION_MISMATCH",
            f"Bridge script reports payload version {version!r}, expected {EXPECTED_BRIDGE_VERSION}.",
        )
    if not payload.get("documents"):
        raise BridgeError("BRIDGE_EMPTY_RESULT", f"No composition documents parsed from {composition}.")
    return payload
