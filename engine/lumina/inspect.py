"""Agent-friendly read views of the document (seconds + timecode alongside ticks)."""

from __future__ import annotations

from . import timebase
from .commands.project import seq_of


def _t(ticks: int | None, st: dict) -> dict | None:
    if ticks is None:
        return None
    return {"ticks": ticks, "seconds": round(timebase.ticks_to_seconds(ticks), 4),
            "timecode": timebase.timecode(ticks, st["fps_num"], st["fps_den"])}


def sequence_duration(seq: dict) -> int:
    return max((c["end"] for t in seq["tracks"].values() for c in t["clips"].values()), default=0)


def project_summary(doc: dict) -> dict:
    return {
        "name": doc["name"],
        "active_sequence_id": doc.get("active_sequence_id"),
        "bins": [{"id": b["id"], "name": b["name"], "parent_id": b["parent_id"]} for b in doc["bins"].values()],
        "media": [
            {"id": m["id"], "name": m["name"], "kind": m["kind"], "path": m["path"], "bin_id": m["bin_id"],
             "duration_seconds": round(timebase.ticks_to_seconds(m["duration"]), 3),
             "video": m.get("video"), "audio": m.get("audio"), "has_proxy": bool(m.get("proxy_path"))}
            for m in doc["media"].values()
        ],
        "sequences": [
            {"id": s["id"], "name": s["name"], "settings": s["settings"],
             "duration_seconds": round(timebase.ticks_to_seconds(sequence_duration(s)), 3)}
            for s in doc["sequences"].values()
        ],
    }


def timeline_state(doc: dict, sequence_id: str | None = None) -> dict:
    seq = seq_of(doc, sequence_id)
    st = seq["settings"]

    def track_view(tid: str, label: str) -> dict:
        t = seq["tracks"][tid]
        return {
            "id": tid, "label": label, "name": t["name"],
            "flags": {k: t[k] for k in ("locked", "sync_lock", "hidden", "muted", "solo", "targeted")},
            "clips": [
                {"id": c["id"], "name": c["name"], "media_id": c["media_id"], "start": _t(c["start"], st), "end": _t(c["end"], st),
                 "source_in": _t(c["source_in"], st), "speed": c["speed"], "enabled": c["enabled"], "link_id": c["link_id"],
                 "opacity": c["opacity"], "volume_db": c["volume_db"]}
                for c in sorted(t["clips"].values(), key=lambda c: c["start"])
            ],
        }

    return {
        "id": seq["id"], "name": seq["name"], "settings": st,
        "duration": _t(sequence_duration(seq), st), "playhead": _t(seq["playhead"], st),
        "in_point": _t(seq["in_point"], st), "out_point": _t(seq["out_point"], st),
        "video_tracks": [track_view(tid, f"V{i + 1}") for i, tid in enumerate(seq["video_tracks"])],
        "audio_tracks": [track_view(tid, f"A{i + 1}") for i, tid in enumerate(seq["audio_tracks"])],
        "markers": [
            {"id": m["id"], "start": _t(m["start"], st), "duration": _t(m["duration"], st), "name": m["name"],
             "comment": m["comment"], "color": m["color"]}
            for m in sorted(seq["markers"].values(), key=lambda m: m["start"])
        ],
    }
