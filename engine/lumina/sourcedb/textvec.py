"""Text vectors in the SigLIP image space, from a kept-alive analyzer process.

Narration is abstract ("의외로 인기를 얻은 조연"); SigLIP finds pictures from
what is on screen ("녹색 머리에 야구모자를 쓴 소녀"). Measured on 395 Claude
descriptions used as queries within their work (median 536 cuts): the right
cut was in the top 8 for 77%, the top 20 for 89% (random: 3%, 7%). Past
narration captions as queries reached only 7% in the top 8.
"""

from __future__ import annotations

import base64
import json
import subprocess
import threading
from pathlib import Path
from typing import Optional

import numpy as np

ANALYZERS = Path(__file__).resolve().parents[2] / "analyzers"
MODEL = "siglip2-so400m-384"   # the image embeddings' model name in the DB
START_TIMEOUT = 180


class TextEncoder:
    def __init__(self, python: Optional[Path] = None):
        self.python = python or ANALYZERS / ".venv" / "Scripts" / "python.exe"
        self.proc: Optional[subprocess.Popen] = None
        self.lock = threading.Lock()

    def _start(self) -> None:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.proc = subprocess.Popen(
            [str(self.python), "-I", str(ANALYZERS / "text_encoder.py")], cwd=ANALYZERS,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", creationflags=flags)
        ready = self._read(START_TIMEOUT)
        if not ready.get("ready"):
            raise RuntimeError(f"text encoder did not start: {ready}")

    def _read(self, timeout: float) -> dict:
        box: dict = {}
        t = threading.Thread(target=lambda: box.update(line=self.proc.stdout.readline()), daemon=True)
        t.start()
        t.join(timeout)
        line = box.get("line")
        if not line:
            self.close()
            raise RuntimeError("text encoder gave no answer")
        return json.loads(line)

    def encode(self, texts: list[str], timeout: float = 60) -> np.ndarray:
        """[n, d] float32, L2-normalised."""
        with self.lock:
            if self.proc is None or self.proc.poll() is not None:
                self._start()
            self.proc.stdin.write(json.dumps({"texts": texts}, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
            r = self._read(timeout)
        if "error" in r:
            raise RuntimeError(r["error"])
        return np.frombuffer(base64.b64decode(r["f16"]), dtype="<f2").reshape(r["shape"]).astype(np.float32)

    def close(self) -> None:
        if self.proc is not None:
            try:
                self.proc.kill()
            except OSError:
                pass
            self.proc = None


_shared: Optional[TextEncoder] = None


def shared() -> TextEncoder:
    """One encoder per process (the MCP server keeps it between calls)."""
    global _shared
    if _shared is None:
        _shared = TextEncoder()
    return _shared
