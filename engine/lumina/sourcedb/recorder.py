"""Recording clips with OBS straight into the source DB.

While a clip is recorded, everything the DB needs is on screen: the work and
episode in the player tab's title, the moment in the player's clock. The
Chrome extension in recorder_ext/ reports that playback state here; OBS
reports recording start and stop over obs-websocket. A finished recording is
moved into the chosen project folder, named the way the channel names clips
("S2 E16 0931 첫 대사.mp4"), and ingested with its episode and source time.
No AI tokens are spent.

    python -m lumina.sourcedb.recorder [--folder <project folder>]
    python -m lumina.sourcedb.recorder folder <project folder>   # change it while running
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import queue
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Optional

from . import catalog, db, ingest
from ..timebase import TICKS_PER_SECOND

S = TICKS_PER_SECOND
PORT = 47321
STATE_PATH = Path.home() / ".lumina" / "recorder.json"
OBS_CONFIG = Path.home() / "AppData" / "Roaming" / "obs-studio" / "plugin_config" / "obs-websocket" / "config.json"
ENGINE = Path(__file__).resolve().parents[2]
ANALYZERS = ENGINE / "analyzers"
VERSION = "recorder@1"
FRESH_SECONDS = 3.0      # a playback report older than this is not "now"
BROWSERS = ("Chrome", "Google Chrome", "Whale", "Microsoft Edge", "Edge", "Firefox")


# ---------------------------------------------------------------------------
# what a player tab says
# ---------------------------------------------------------------------------

_EPISODE_BITS = re.compile(
    r"(?i)\bseason\s*\d+|\bepisode\s*\d+|\bS\d+\s*E\d+|\bEP?\.?\s*\d+\b|\d+\s*기|\d+\s*화|第\s*\d+\s*話|\bOVA\b|\bmovie\b")


def clean_title(title: str) -> str:
    """The page's own title: no browser name, no '(3) ' unread counter."""
    t = re.sub(r"^\(\d+\)\s*", "", title or "").strip()
    head, sep, tail = t.rpartition(" - ")
    if sep and tail.strip() in BROWSERS:
        t = head.strip()
    return t


def work_candidates(title: str) -> list[str]:
    """Names the work might go by in a player title, most likely first:
    'JUJUTSU KAISEN Season 2 - Episode 16 | SiteName' -> 'JUJUTSU KAISEN'."""
    t = clean_title(title)
    out = []
    for seg in re.split(r"\s+[|│｜]\s+|\s+[-–—]\s+", t):
        m = _EPISODE_BITS.search(seg)
        name = (seg[:m.start()] if m else seg).strip(" -–—:|[]()")
        if len(catalog.key(name)) >= 2:
            out.append(name)
    return list(dict.fromkeys(out))


def work_from_title(con, title: str) -> Optional[int]:
    """The work a player title names, if the source DB or the catalog knows it."""
    for name in work_candidates(title):
        eid = catalog.find_entity(con, "work", [name])
        if eid is not None:
            return eid
    return None


def clip_name(episode: Optional[str], seconds: Optional[float], line: Optional[str], fallback: str) -> str:
    """'S2 E16 0931 첫 대사' - the channel's own naming. mmss under an hour,
    hhmmss above. Without an episode the work's moment is still the time."""
    parts = []
    if episode:
        parts.append(episode)
    if seconds is not None:
        s = int(seconds)
        parts.append(f"{s // 3600:02d}{s // 60 % 60:02d}{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}{s % 60:02d}")
    if line:
        parts.append(line)
    name = " ".join(parts) or fallback
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", name)
    return re.sub(r"\s+", " ", name).strip(" .")[:120] or fallback


def first_line(text: str, limit: int = 30) -> str:
    """The first sentence of what was said, short enough for a file name."""
    t = re.split(r"(?<=[.!?。！？…])\s", text.strip())[0]
    return t[:limit].rstrip(" ,、")


# ---------------------------------------------------------------------------
# playback reports from the extension
# ---------------------------------------------------------------------------

@dataclass
class Playback:
    tab: str
    title: str
    url: str
    current: float
    duration: float
    paused: bool
    received: float = field(default_factory=time.monotonic)

    def now(self) -> float:
        """The player's clock at this instant, assuming it kept playing."""
        return self.current + (0.0 if self.paused else time.monotonic() - self.received)


class PlaybackBoard:
    def __init__(self):
        self._lock = threading.Lock()
        self._tabs: dict[str, Playback] = {}

    def report(self, p: Playback) -> None:
        with self._lock:
            self._tabs[p.tab] = p

    def current(self) -> Optional[Playback]:
        """The video playing now: freshest report, playing over paused."""
        with self._lock:
            fresh = [p for p in self._tabs.values() if time.monotonic() - p.received < FRESH_SECONDS]
        if not fresh:
            return None
        return max(fresh, key=lambda p: (not p.paused, p.received))


def serve_board(board: PlaybackBoard, port: int = PORT) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 - http.server API
            try:
                d = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                board.report(Playback(str(d["tab"]), d.get("title") or "", d.get("url") or "",
                                      float(d["currentTime"]), float(d.get("duration") or 0), bool(d.get("paused"))))
                self.send_response(204)
            except (ValueError, KeyError, TypeError):
                self.send_response(400)
            self.end_headers()

        def log_message(self, *a):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# ---------------------------------------------------------------------------
# obs-websocket (protocol v5), just what recording needs
# ---------------------------------------------------------------------------

def obs_auth(password: str, salt: str, challenge: str) -> str:
    secret = base64.b64encode(hashlib.sha256((password + salt).encode()).digest()).decode()
    return base64.b64encode(hashlib.sha256((secret + challenge).encode()).digest()).decode()


def obs_record_events(on_event: Callable[[str, Optional[str]], None], *, stop: threading.Event) -> None:
    """Call on_event(state, output_path) for every RecordStateChanged, and
    reconnect whenever OBS is closed and opened again."""
    from websockets.sync.client import connect

    while not stop.is_set():
        try:
            cfg = json.loads(OBS_CONFIG.read_text(encoding="utf-8"))
            with connect(f"ws://127.0.0.1:{cfg.get('server_port', 4455)}", open_timeout=3) as ws:
                hello = json.loads(ws.recv())["d"]
                ident = {"rpcVersion": 1, "eventSubscriptions": 1 << 6}  # Outputs
                if "authentication" in hello:
                    a = hello["authentication"]
                    ident["authentication"] = obs_auth(cfg.get("server_password", ""), a["salt"], a["challenge"])
                ws.send(json.dumps({"op": 1, "d": ident}))
                print("OBS connected")
                while not stop.is_set():
                    try:
                        msg = json.loads(ws.recv(timeout=1))
                    except TimeoutError:
                        continue
                    if msg.get("op") == 5 and msg["d"].get("eventType") == "RecordStateChanged":
                        e = msg["d"].get("eventData", {})
                        on_event(e.get("outputState", ""), e.get("outputPath"))
        except Exception as e:  # OBS closed, not started yet, or restarted
            if not stop.is_set():
                print(f"OBS not reachable ({type(e).__name__}); retrying")
                stop.wait(5)


# ---------------------------------------------------------------------------
# the recorder
# ---------------------------------------------------------------------------

def target_folder() -> Optional[Path]:
    try:
        f = json.loads(STATE_PATH.read_text(encoding="utf-8")).get("folder")
        return Path(f) if f else None
    except (OSError, ValueError):
        return None


def set_target_folder(folder: Path) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps({"folder": str(folder)}, ensure_ascii=False), encoding="utf-8")


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    for i in range(2, 1000):
        p = path.with_name(f"{path.stem} ({i}){path.suffix}")
        if not p.exists():
            return p
    raise FileExistsError(path)


def _wait_closed(path: Path, timeout: float = 60) -> None:
    """OBS reports a stop before the last bytes of a hybrid MP4 land."""
    end, last = time.monotonic() + timeout, -1
    while time.monotonic() < end:
        try:
            size = path.stat().st_size
            with open(path, "rb+"):
                pass
            if size == last:
                return
            last = size
        except OSError:
            pass
        time.sleep(0.5)


@dataclass
class Take:
    started: float                    # wall clock (monotonic) at record start
    playback: Optional[Playback]      # what was playing then
    player_at_start: Optional[float]  # the player's clock at record start


class Recorder:
    def __init__(self, db_path=db.DEFAULT_DB_PATH, *, run_analyzers: bool = True, log=print):
        self.db_path, self.run_analyzers, self.log = db_path, run_analyzers, log
        self.board = PlaybackBoard()
        self.take: Optional[Take] = None
        self.jobs: "queue.Queue[tuple[Path, Take]]" = queue.Queue()

    # OBS event
    def on_record(self, state: str, path: Optional[str]) -> None:
        if state == "OBS_WEBSOCKET_OUTPUT_STARTED":
            p = self.board.current()
            self.take = Take(time.monotonic(), p, p.now() if p else None)
            self.log(f"recording: {clean_title(p.title) if p else '(no player reported)'}"
                     + (f" at {p.now():.1f}s" if p else ""))
        elif state == "OBS_WEBSOCKET_OUTPUT_STOPPED" and path:
            take = self.take or Take(time.monotonic(), None, None)
            self.take = None
            self.jobs.put((Path(path), take))

    def work_forever(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                path, take = self.jobs.get(timeout=1)
            except queue.Empty:
                continue
            try:
                self.file(path, take)
            except Exception as e:
                self.log(f"  FAILED {path.name}: {type(e).__name__}: {e}")

    # one finished recording
    def file(self, path: Path, take: Take) -> Optional[int]:
        _wait_closed(path)
        folder = target_folder() or path.parent
        p = take.playback
        episode = seconds = None
        if p:
            ep = ingest.episode_of(clean_title(p.title))
            episode = ep[0] if ep else None
            seconds = take.player_at_start
        dest = _unique(folder / (clip_name(episode, seconds, None, path.stem) + path.suffix))
        folder.mkdir(parents=True, exist_ok=True)
        path.replace(dest)
        con = db.connect(self.db_path)
        try:
            root = self._work_root(dest)
            folder_work = catalog.find_entity(con, "work", [root.name]) if root else None
            r = ingest.analyze_file(dest, "video")
            media_id, _ = db.register_file(con, dest, "video", probe=r["probe"], sha256=r["sha256"])
            db.set_cuts(con, media_id, r["cuts"])
            db.put_facts(con, media_id, ingest.CUTS_V, r["measures"])
            db.job_done(con, media_id, "probe", ingest.PROBE_V)
            db.job_done(con, media_id, "cuts", ingest.CUTS_V)
            db.put_facts(con, media_id, ingest.FILENAME_V,
                         ingest.filename_facts(con, dest, root or dest.parent, folder_work))
            db.job_done(con, media_id, "filename", ingest.FILENAME_V)
            db.put_facts(con, media_id, VERSION, self._take_facts(con, take))
            db.set_status(con, media_id, "live")
            ingest.verify(con, media_id)
            con.commit()
        finally:
            con.close()
        self.log(f"  -> {dest}")
        if self.run_analyzers:
            dest = self._analyze_and_name(media_id, dest)
        return media_id

    @staticmethod
    def _work_root(path: Path) -> Optional[Path]:
        """The work's folder: the first folder under Project, as ingest uses."""
        projects = ingest.CHANNEL_ROOT / "Project"
        try:
            rel = path.relative_to(projects)
        except ValueError:
            return None
        return projects / rel.parts[0] if len(rel.parts) > 1 else None

    @staticmethod
    def _take_facts(con, take: Take) -> list[dict]:
        p = take.playback
        if p is None:
            return []
        title = clean_title(p.title)
        rows = [{"kind": "title", "value": title, "status": "accepted", "basis": "player tab title"},
                {"kind": "origin_url", "value": p.url, "status": "accepted", "basis": "player tab"}]
        ep = ingest.episode_of(title)
        if ep:
            rows.append({"kind": "episode", "value": ep[0], "status": "accepted", "basis": "player tab title"})
        if take.player_at_start is not None:
            rows.append({"kind": "source_time", "num": round(take.player_at_start, 3), "status": "accepted",
                         "basis": "player clock at record start"})
        work = work_from_title(con, title)
        if work is not None:
            rows.append({"kind": "work", "ref_id": work, "status": "proposed", "basis": "player tab title"})
        return rows

    def _analyze_and_name(self, media_id: int, dest: Path) -> Path:
        """Tagger, speech, embeddings for the new clip (each stage in its own
        process, as run.py requires), then the first spoken line joins the
        file name."""
        py = ANALYZERS / ".venv" / "Scripts" / "python.exe"
        for stage in ("tagger", "speech", "embed"):
            subprocess.run([str(py), str(ANALYZERS / "run.py"), str(self.db_path), stage],
                           capture_output=True, cwd=ANALYZERS)
        con = db.connect(self.db_path)
        try:
            ingest.verify(con, media_id)
            row = con.execute("SELECT value FROM fact WHERE media_id = ? AND kind = 'speech' AND status != 'rejected' "
                              "ORDER BY t0_ticks LIMIT 1", (media_id,)).fetchone()
            if row:
                named = _unique(dest.with_name(f"{dest.stem} {clip_name(None, None, first_line(row[0]), '')}{dest.suffix}"))
                dest.replace(named)
                db.move_path(con, dest, named)
                ingest_refresh_title(con, media_id, named)
                con.commit()
                self.log(f"  -> {named.name}")
                return named
            con.commit()
        finally:
            con.close()
        return dest


def ingest_refresh_title(con, media_id: int, path: Path) -> None:
    """The renamed file's own name becomes its title fact again."""
    root = Recorder._work_root(path)
    work = catalog.find_entity(con, "work", [root.name]) if root else None
    db.put_facts(con, media_id, ingest.FILENAME_V, ingest.filename_facts(con, path, root or path.parent, work))


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python -m lumina.sourcedb.recorder")
    ap.add_argument("cmd", nargs="?", choices=["run", "folder"], default="run")
    ap.add_argument("path", nargs="?")
    ap.add_argument("--folder", help="project folder recordings go to")
    ap.add_argument("--no-analyzers", action="store_true", help="skip tagger, speech and embeddings")
    a = ap.parse_args(argv)
    if a.cmd == "folder":
        if not a.path:
            print(target_folder() or "(not set: recordings stay where OBS writes them)")
            return
        set_target_folder(Path(a.path))
        print(f"recordings go to {a.path}")
        return
    if a.folder:
        set_target_folder(Path(a.folder))
    rec = Recorder(run_analyzers=not a.no_analyzers)
    stop = threading.Event()
    serve_board(rec.board)
    threading.Thread(target=rec.work_forever, args=(stop,), daemon=True).start()
    print(f"listening for the player on 127.0.0.1:{PORT}; recordings go to {target_folder() or 'the OBS folder'}")
    try:
        obs_record_events(rec.on_record, stop=stop)
    except KeyboardInterrupt:
        stop.set()


if __name__ == "__main__":
    main()
