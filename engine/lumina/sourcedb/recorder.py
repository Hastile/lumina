"""Recording clips with OBS straight into the source DB.

While a clip is recorded, everything the DB needs is on screen: the work and
episode in the player tab's title, the moment in the player's clock. The
Chrome extension in recorder_ext/ reports that playback state here; OBS
reports recording start and stop over obs-websocket. A finished recording is
moved into the chosen project folder, named the way the channel names clips
("S2 E16 0931 첫 대사.mp4"), and ingested with its episode and source time.
No AI tokens are spent.

    python -m lumina.sourcedb.recorder [--folder <project folder>]   # file what you record by hand
    python -m lumina.sourcedb.recorder plan clips.txt [--folder ...]  # record a list of clips by itself
    python -m lumina.sourcedb.recorder folder <project folder>        # change the folder while running
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
    seeking: bool = False
    received: float = field(default_factory=time.monotonic)

    def now(self) -> float:
        """The player's clock at this instant, assuming it kept playing."""
        return self.current + (0.0 if self.paused else time.monotonic() - self.received)


class PlaybackBoard:
    """The latest report of every playing frame, and the commands waiting to
    be handed to a frame in the answer to its next report."""

    def __init__(self):
        self._lock = threading.Lock()
        self._tabs: dict[str, Playback] = {}
        self._commands: dict[str, dict] = {}

    def report(self, p: Playback) -> Optional[dict]:
        with self._lock:
            self._tabs[p.tab] = p
            return self._commands.pop(p.tab, None)

    def send(self, tab: str, **cmd) -> None:
        with self._lock:
            self._commands.setdefault(tab, {}).update(cmd)

    def current(self) -> Optional[Playback]:
        """The video playing now: freshest report, playing over paused."""
        with self._lock:
            fresh = [p for p in self._tabs.values() if time.monotonic() - p.received < FRESH_SECONDS]
        if not fresh:
            return None
        return max(fresh, key=lambda p: (not p.paused, p.received))

    def get(self, tab: str) -> Optional[Playback]:
        with self._lock:
            p = self._tabs.get(tab)
        return p if p and time.monotonic() - p.received < FRESH_SECONDS else None


def serve_board(board: PlaybackBoard, port: int = PORT) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 - http.server API
            try:
                d = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                cmd = board.report(Playback(str(d["tab"]), d.get("title") or "", d.get("url") or "",
                                            float(d["currentTime"]), float(d.get("duration") or 0),
                                            bool(d.get("paused")), bool(d.get("seeking"))))
            except (ValueError, KeyError, TypeError):
                self.send_response(400)
                self.end_headers()
                return
            if cmd:
                body = json.dumps(cmd).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(204)
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


class ObsClient:
    """Record-state events in, StartRecord / StopRecord out. Reconnects
    whenever OBS is closed and opened again."""

    def __init__(self, on_record: Callable[[str, Optional[str]], None], log=print):
        self.on_record, self.log = on_record, log
        self._ws = None
        self._send_lock = threading.Lock()
        self._pending: dict[str, "queue.Queue[dict]"] = {}
        self.connected = threading.Event()

    def run(self, stop: threading.Event) -> None:
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
                    self._ws = ws
                    while not stop.is_set():
                        try:
                            msg = json.loads(ws.recv(timeout=1))
                        except TimeoutError:
                            continue
                        op, d = msg.get("op"), msg.get("d", {})
                        if op == 2:
                            self.connected.set()
                            self.log("OBS connected")
                        elif op == 5 and d.get("eventType") == "RecordStateChanged":
                            e = d.get("eventData", {})
                            self.on_record(e.get("outputState", ""), e.get("outputPath"))
                        elif op == 7 and d.get("requestId") in self._pending:
                            self._pending.pop(d["requestId"]).put(d)
            except Exception as e:  # OBS closed, not started yet, or restarted
                self.connected.clear()
                self._ws = None
                if not stop.is_set():
                    self.log(f"OBS not reachable ({type(e).__name__}); retrying")
                    stop.wait(5)

    def request(self, kind: str, timeout: float = 10) -> dict:
        if not self.connected.wait(timeout):
            raise ConnectionError("OBS is not connected")
        rid = f"{kind}-{time.monotonic_ns()}"
        box: "queue.Queue[dict]" = queue.Queue()
        self._pending[rid] = box
        with self._send_lock:
            self._ws.send(json.dumps({"op": 6, "d": {"requestType": kind, "requestId": rid}}))
        d = box.get(timeout=timeout)
        status = d.get("requestStatus", {})
        if not status.get("result"):
            raise RuntimeError(f"OBS {kind}: {status.get('comment') or status.get('code')}")
        return d.get("responseData") or {}


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
        self.busy = False

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
            self.busy = True
            try:
                self.file(path, take)
            except Exception as e:
                self.log(f"  FAILED {path.name}: {type(e).__name__}: {e}")
            finally:
                self.busy = False

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


# ---------------------------------------------------------------------------
# recording a list of clips
# ---------------------------------------------------------------------------

PREROLL = 1.0       # seconds played before the clip, so the player is running when recording starts
START_LEAD = 0.3    # recording starts this early; the clip's source_time records the true start
POSTROLL = 0.3
STALL_LIMIT = 20.0  # seconds without the clock moving (buffering, an ad) before a clip is given up
PAGE_WAIT = 600.0   # how long to wait for the right episode page to be opened

_TIME = r"(?:\d{1,2}:)?\d{1,2}:\d{2}(?:\.\d+)?|\d{4}(?:\d{2})?"


@dataclass
class Clip:
    episode: Optional[str]
    start: float
    end: float
    note: str = ""
    url: Optional[str] = None
    folder: Optional[Path] = None


def parse_time(t: str) -> float:
    """'09:20', '1:02:03', '0920' (mmss) or '010203' (hhmmss) -> seconds."""
    if ":" in t:
        parts = [float(x) for x in t.split(":")]
    else:
        parts = [int(t[i:i + 2]) for i in range(0, len(t), 2)]
    return float(sum(p * 60 ** (len(parts) - 1 - i) for i, p in enumerate(parts)))


def parse_plan(text: str) -> list[Clip]:
    """One clip per line: '[episode] start-end [url] [note]'. A line
    'folder: <path>' sends the clips after it to that project folder; a line
    starting with '#' is a comment. The episode is optional (then: whatever page is open)."""
    clips, folder = [], None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("folder:"):
            folder = Path(line.split(":", 1)[1].strip().strip('"'))
            continue
        m = re.search(rf"(?P<a>{_TIME})\s*[-~–]\s*(?P<b>{_TIME})", line)
        if not m:
            raise ValueError(f"no start-end time in: {raw}")
        ep = ingest.episode_of(line[:m.start()])
        rest = line[m.end():].strip()
        url = None
        um = re.search(r"https?://\S+", rest)
        if um:
            url, rest = um.group(), (rest[:um.start()] + rest[um.end():]).strip()
        start, end = parse_time(m["a"]), parse_time(m["b"])
        if end <= start:
            raise ValueError(f"end before start in: {raw}")
        clips.append(Clip(ep[0] if ep else None, start, end, rest, url, folder))
    return clips


class PlanRunner:
    """Seek, play, record, stop - for each clip of a plan, on the player the
    Chrome extension reports. OBS events go to the Recorder as usual, so
    every clip is filed and ingested the same way as a hand-made recording."""

    def __init__(self, rec: "Recorder", obs, log=print, poll: float = 0.1):
        self.rec, self.obs, self.log, self.poll = rec, obs, log, poll

    def _wait(self, cond: Callable[[], bool], timeout: float) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if cond():
                return True
            time.sleep(self.poll)
        return False

    def _page_for(self, clip: Clip) -> Optional[Playback]:
        def right() -> Optional[Playback]:
            p = self.rec.board.current()
            if p is None:
                return None
            if clip.episode is None:
                return p
            ep = ingest.episode_of(clean_title(p.title))
            return p if ep and ep[0] == clip.episode else None

        if right() is None and clip.url:
            p = self.rec.board.current()
            if p is not None:
                self.rec.board.send(p.tab, navigate=clip.url)
            self.log(f"opening {clip.url}")
        elif right() is None:
            self.log(f"open the page of {clip.episode} in Chrome")
        found: list[Playback] = []
        self._wait(lambda: bool(found.append(right()) or found[-1]), PAGE_WAIT if not clip.url else 60)
        return found[-1] if found else None

    def record(self, clip: Clip) -> bool:
        board = self.rec.board
        p = self._page_for(clip)
        if p is None:
            self.log(f"  skipped {clip.episode} {clip.start:.0f}s: the page never opened")
            return False
        tab = p.tab
        board.send(tab, seek=max(0.0, clip.start - PREROLL), pause=True)
        at = lambda: board.get(tab)  # noqa: E731
        if not self._wait(lambda: (q := at()) is not None and not q.seeking
                          and abs(q.current - max(0.0, clip.start - PREROLL)) < 1.0, 20):
            self.log("  skipped: the player did not seek")
            return False
        board.send(tab, play=True)
        if not self._progress(tab, clip.start - START_LEAD):
            board.send(tab, pause=True)
            self.log("  skipped: the player did not start")
            return False
        if clip.folder:
            set_target_folder(clip.folder)
        self.obs.request("StartRecord")
        ok = self._progress(tab, clip.end + POSTROLL)
        self.obs.request("StopRecord")
        board.send(tab, pause=True)
        if not ok:
            self.log(f"  stalled before {clip.end:.1f}s; the recording is kept as it is")
        self._wait(lambda: self.rec.take is None, 15)  # the stop event has filed it
        return ok

    def _progress(self, tab: str, target: float) -> bool:
        """Wait for the player's clock to reach target, giving up when it
        stops moving for STALL_LIMIT seconds."""
        last, moved = None, time.monotonic()
        while True:
            q = self.rec.board.get(tab)
            if q is not None:
                if q.now() >= target:
                    return True
                if last is None or q.current > last + 0.01:
                    last, moved = q.current, time.monotonic()
            if time.monotonic() - moved > STALL_LIMIT:
                return False
            time.sleep(self.poll)

    def run(self, clips: list[Clip]) -> list[bool]:
        results = []
        for i, c in enumerate(clips, 1):
            self.log(f"[{i}/{len(clips)}] {c.episode or '(open page)'} {c.start:.1f}-{c.end:.1f}s {c.note}")
            results.append(self.record(c))
        return results



def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python -m lumina.sourcedb.recorder")
    ap.add_argument("cmd", nargs="?", choices=["run", "folder", "plan"], default="run")
    ap.add_argument("path", nargs="?", help="folder: the project folder; plan: the list of clips")
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
    clips = parse_plan(Path(a.path).read_text(encoding="utf-8")) if a.cmd == "plan" else None
    rec = Recorder(run_analyzers=not a.no_analyzers)
    stop = threading.Event()
    serve_board(rec.board)
    obs = ObsClient(rec.on_record)
    threading.Thread(target=obs.run, args=(stop,), daemon=True).start()
    worker = threading.Thread(target=rec.work_forever, args=(stop,), daemon=True)
    worker.start()
    print(f"listening for the player on 127.0.0.1:{PORT}; recordings go to {target_folder() or 'the OBS folder'}")
    try:
        if clips is None:
            stop.wait()
        else:
            if not obs.connected.wait(30):
                raise SystemExit("OBS is not running or obs-websocket is off")
            results = PlanRunner(rec, obs).run(clips)
            print(f"recorded {sum(results)}/{len(results)} clips; filing the rest")
            while not rec.jobs.empty() or rec.busy:
                time.sleep(1)
            stop.set()
    except KeyboardInterrupt:
        stop.set()


if __name__ == "__main__":
    main()
