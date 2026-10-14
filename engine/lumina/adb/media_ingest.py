"""Pure logic + isolated ffmpeg I/O for turning a raw recorded clip into
source_db candidates (scene_cut / dialogue rows).

Design mirrors this repo's existing convention (srt_parser.py,
premiere_video_analysis.py): parsing/decision logic is pure and unit
testable without ffmpeg installed; the one function that shells out to
ffmpeg (extract_frame) is a thin, isolated wrapper.

Semantic work - what a cut *shows* (description_text) or what tags apply -
is deliberately NOT done here. That's a judgment call for whichever AI/
session is driving ingestion (see CLAUDE.md "Media ingest" rules); this
module only produces the mechanical scaffolding: cut boundaries, confidence
flags, representative-frame timestamps, and subtitle-cue alignment.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any, Optional
from .quiet_process import NO_WINDOW

DEFAULT_CLUSTER_WINDOW_MS = 350
DEFAULT_FRAME_AVOID_MS = 200
DEFAULT_BOUNDARY_SNAP_TOLERANCE_MS = 300

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}


# ---------------------------------------------------------------------------
# scene-event clustering
# ---------------------------------------------------------------------------

def cluster_scene_events(
    scene_events: list[dict[str, Any]], *, cluster_window_ms: int = DEFAULT_CLUSTER_WINDOW_MS
) -> list[list[dict[str, Any]]]:
    """Chain-links scene-change events that land within cluster_window_ms of
    each other into one cluster. A single-event cluster is an ordinary hard
    cut; a multi-event cluster is ffmpeg's frame-difference detector
    stuttering across a gradual transition (fade/dissolve) rather than
    finding one clean spike - that's the signal we use to flag low
    confidence downstream, not just noise to ignore."""
    if cluster_window_ms <= 0:
        raise ValueError("cluster_window_ms must be positive.")
    ordered = sorted(scene_events, key=lambda e: e["ptsSeconds"])
    clusters: list[list[dict[str, Any]]] = []
    for event in ordered:
        if clusters and (event["ptsSeconds"] * 1000.0 - clusters[-1][-1]["ptsSeconds"] * 1000.0) <= cluster_window_ms:
            clusters[-1].append(event)
        else:
            clusters.append([event])
    return clusters


def build_cut_boundaries(
    scene_events: list[dict[str, Any]],
    media_duration_ms: int,
    *,
    cluster_window_ms: int = DEFAULT_CLUSTER_WINDOW_MS,
) -> list[dict[str, Any]]:
    """Turns scene-change events into an ordered list of cut segments
    spanning [0, media_duration_ms). Each segment carries a confidence flag:
    'high' for a boundary produced by a single clean event, 'low' when the
    boundary came from a multi-event cluster (probable fade/dissolve, exact
    cut point ambiguous)."""
    if isinstance(media_duration_ms, bool) or not isinstance(media_duration_ms, (int, float)) or media_duration_ms <= 0:
        raise ValueError("media_duration_ms must be a positive number.")
    clusters = cluster_scene_events(scene_events, cluster_window_ms=cluster_window_ms)

    boundaries: list[dict[str, Any]] = []
    for cluster in clusters:
        span_start_ms = cluster[0]["ptsSeconds"] * 1000.0
        span_end_ms = cluster[-1]["ptsSeconds"] * 1000.0
        boundaries.append({
            "boundaryMs": (span_start_ms + span_end_ms) / 2.0,
            "confidence": "high" if len(cluster) == 1 else "low",
            "transitionSpanMs": span_end_ms - span_start_ms,
            "sourceEventCount": len(cluster),
        })

    cuts: list[dict[str, Any]] = []
    cut_start_ms = 0.0
    for cut_index, boundary in enumerate(boundaries):
        cuts.append({
            "cutIndex": cut_index,
            "startMs": round(cut_start_ms),
            "endMs": round(boundary["boundaryMs"]),
            "boundaryConfidence": boundary["confidence"],
            "transitionSpanMs": round(boundary["transitionSpanMs"]),
            "sourceEventCount": boundary["sourceEventCount"],
        })
        cut_start_ms = boundary["boundaryMs"]

    cuts.append({
        "cutIndex": len(boundaries),
        "startMs": round(cut_start_ms),
        "endMs": round(media_duration_ms),
        "boundaryConfidence": "high",
        "transitionSpanMs": 0,
        "sourceEventCount": 0,
    })
    non_empty = [c for c in cuts if c["endMs"] > c["startMs"]]
    for new_index, cut in enumerate(non_empty):
        cut["cutIndex"] = new_index
    return non_empty


# ---------------------------------------------------------------------------
# representative frame selection + extraction
# ---------------------------------------------------------------------------

def pick_frame_timestamps_ms(
    cuts: list[dict[str, Any]], *, avoid_ms: int = DEFAULT_FRAME_AVOID_MS
) -> dict[int, int]:
    """Picks a representative-frame timestamp per cut. Ordinary cuts (both
    edges high confidence) use the plain midpoint. A cut adjacent to a 'low'
    confidence boundary (probable fade/dissolve - see build_cut_boundaries)
    gets pushed avoid_ms away from that edge instead, so the sampled frame
    doesn't land mid-blend. When a cut is flanked by low-confidence edges on
    both sides, there's no side left to push toward, so it falls back to the
    midpoint - the least-bad option. cuts must be the contiguous,
    cutIndex-ordered list build_cut_boundaries returns."""
    timestamps: dict[int, int] = {}
    for i, cut in enumerate(cuts):
        start_ms, end_ms = cut["startMs"], cut["endMs"]
        duration_ms = end_ms - start_ms
        left_low = i > 0 and cuts[i - 1]["boundaryConfidence"] == "low"
        right_low = cut["boundaryConfidence"] == "low"
        margin = min(avoid_ms, duration_ms / 2.0)
        if left_low and not right_low:
            target = end_ms - margin
        elif right_low and not left_low:
            target = start_ms + margin
        else:
            target = start_ms + duration_ms / 2.0
        timestamps[cut["cutIndex"]] = round(target)
    return timestamps


def extract_frame(
    ffmpeg_path: str, video_path: "str | Path", timestamp_ms: int, out_path: "str | Path", *, timeout_seconds: float = 20.0
) -> tuple[bool, Optional[dict[str, Any]]]:
    """Shells out to ffmpeg to grab a single frame at timestamp_ms. The only
    I/O boundary in this module - kept separate so the rest of the pipeline
    stays unit-testable without ffmpeg installed."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg_path, "-y", "-ss", f"{timestamp_ms / 1000.0:.3f}",
        "-i", str(video_path), "-frames:v", "1", str(out_path),
    ]
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout_seconds, check=False, **NO_WINDOW
        )
    except subprocess.TimeoutExpired:
        return False, {"code": "FRAME_EXTRACTION_TIMEOUT", "message": f"ffmpeg timed out after {timeout_seconds}s."}
    except OSError as exc:
        return False, {"code": "FRAME_EXTRACTION_FAILED", "message": str(exc)}
    if completed.returncode != 0 or not out_path.exists():
        return False, {"code": "FRAME_EXTRACTION_FAILED", "message": completed.stderr[-1000:]}
    return True, None


def compute_file_hash(path: "str | Path", *, chunk_size: int = 1024 * 1024) -> str:
    """sha256 of a file's bytes - the same identity key source_db.get_or_create_media
    dedupes on, so callers that hash with this before writing get idempotent
    re-ingestion for free."""
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


# ---------------------------------------------------------------------------
# unregistered project media discovery
# ---------------------------------------------------------------------------

def filter_project_images(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Narrows a list_project_media_items-shaped item list (name, mediaPath,
    binPath, ...) down to image files, by extension."""
    return [
        item for item in items
        if item.get("mediaPath") and Path(item["mediaPath"]).suffix.lower() in IMAGE_EXTENSIONS
    ]


def scan_local_images(root_dir: "str | Path") -> list[dict[str, Any]]:
    """Walks root_dir on disk and returns image files in the same shape
    filter_project_images produces (name, mediaPath, binPath), so a folder
    that was never imported into a Premiere project can go through the same
    unregistered-media diff as a project's ProjectItems. binPath is the
    file's directory path relative to root_dir, split into parts - mirrors
    Premiere's bin-path-as-folder-path convention, so a user's existing
    folder-per-work/folder-per-character organization becomes the same
    origin-hint signal either way."""
    root = Path(root_dir).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(str(root))
    items = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        rel_parts = path.relative_to(root).parent.parts
        items.append({"name": path.name, "mediaPath": str(path), "binPath": list(rel_parts)})
    return items


def plan_path_relink(
    db_rows: list[dict[str, Any]], items_with_hash: list[dict[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    """Matches registered media against what's currently on disk by file
    hash, so reorganizing a folder doesn't orphan the DB - a moved or
    renamed file keeps its bytes, and therefore its identity.

    db_rows: dicts with mediaId, filePath, fileHash (hash may be None).
    items_with_hash: scan_local_images output annotated with fileHash.

    Returns three buckets: 'moved' (same hash, new path -> file_path should
    be updated), 'missing' (registered but its hash is nowhere on disk) and
    'unregistered' (on disk but not in the DB). Nothing is decided here
    beyond the hash match; callers apply the moves and review the rest.
    """
    disk_by_hash: dict[str, dict[str, Any]] = {}
    for item in items_with_hash:
        file_hash = item.get("fileHash")
        if file_hash and file_hash not in disk_by_hash:
            disk_by_hash[file_hash] = item

    moved: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    matched_hashes: set[str] = set()

    for row in db_rows:
        file_hash = row.get("fileHash")
        match = disk_by_hash.get(file_hash) if file_hash else None
        if match is None:
            missing.append(row)
            continue
        matched_hashes.add(file_hash)
        if match["mediaPath"] != row["filePath"]:
            moved.append({
                "mediaId": row["mediaId"],
                "oldPath": row["filePath"],
                "newPath": match["mediaPath"],
                "binPath": match.get("binPath"),
            })

    unregistered = [
        item for item in items_with_hash
        if item.get("fileHash") and item["fileHash"] not in matched_hashes
    ]
    return {"moved": moved, "missing": missing, "unregistered": unregistered}


def find_unregistered_media(
    items_with_hash: list[dict[str, Any]], registered_hashes: set[str]
) -> list[dict[str, Any]]:
    """Pure diff: given project items already annotated with a 'fileHash' key
    (see compute_file_hash) and the set of hashes already present in
    source_db (source_db.list_media_file_hashes), returns the items whose
    hash isn't registered yet - candidates for source investigation and
    ingestion. Items with fileHash=None (unreadable file) pass through too,
    since 'unregistered' is also true for them, just unverifiable by hash."""
    return [item for item in items_with_hash if item.get("fileHash") not in registered_hashes]


# ---------------------------------------------------------------------------
# subtitle-cue alignment
# ---------------------------------------------------------------------------

def align_cues_to_cuts(cues: list[dict[str, Any]], cuts: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    """Maps each cut's cutIndex to the SRT cues (srt_parser shape) whose
    [startMilliseconds, endMilliseconds) range overlaps that cut's span."""
    result: dict[int, list[dict[str, Any]]] = {cut["cutIndex"]: [] for cut in cuts}
    for cue in cues:
        cue_start, cue_end = cue["startMilliseconds"], cue["endMilliseconds"]
        for cut in cuts:
            if cue_start < cut["endMs"] and cue_end > cut["startMs"]:
                result[cut["cutIndex"]].append(cue)
    return result


def snap_low_confidence_boundaries_to_cues(
    cuts: list[dict[str, Any]], cues: list[dict[str, Any]], *, tolerance_ms: int = DEFAULT_BOUNDARY_SNAP_TOLERANCE_MS
) -> list[dict[str, Any]]:
    """Nudges a 'low' confidence cut boundary (ambiguous fade/dissolve) onto
    the nearest subtitle cue edge within tolerance_ms, on the theory that
    dialogue almost always starts/ends close to the real cut point even when
    the frame-difference detector can't find a clean spike there. Boundaries
    with no nearby cue edge, or already 'high' confidence, are left alone."""
    if not cues or len(cuts) < 2:
        return cuts
    cue_edges = sorted({ms for cue in cues for ms in (cue["startMilliseconds"], cue["endMilliseconds"])})

    snapped = [dict(c) for c in cuts]
    for i in range(len(snapped) - 1):
        left, right = snapped[i], snapped[i + 1]
        if left["boundaryConfidence"] != "low":
            continue
        boundary_ms = left["endMs"]
        nearest = min(cue_edges, key=lambda edge: abs(edge - boundary_ms))
        if abs(nearest - boundary_ms) <= tolerance_ms:
            left["endMs"] = nearest
            right["startMs"] = nearest
            left["boundarySnappedToCueEdge"] = True
    return snapped


# ---------------------------------------------------------------------------
# source_db batch write
# ---------------------------------------------------------------------------

def ingest_cuts_to_db(
    con, *, source: dict[str, Any], media: dict[str, Any], cuts: list[dict[str, Any]]
) -> dict[str, Any]:
    """Batch-writes a finalized cut list (each cut already carrying a
    Claude-authored descriptionText, per this repo's plan-then-commit
    convention - see CLAUDE.md) into source.db via source_db's get-or-create/
    upsert helpers. Purely mechanical: no cut here is accepted, rejected, or
    rewritten, just persisted."""
    from . import source_db  # local import: keeps this module importable without source_db's sqlite dependency chain in pure-logic tests

    for cut in cuts:
        if not cut.get("descriptionText"):
            raise ValueError(f"cut {cut.get('cutIndex')} is missing descriptionText.")

    source_id = source_db.get_or_create_source(
        con,
        source_category=source["sourceCategory"],
        is_official=source["isOfficial"],
        work_title=source.get("workTitle"),
        origin_ref=source.get("originRef"),
        subtitle=source.get("subtitle"),
        episode=source.get("episode"),
    )
    media_id = source_db.get_or_create_media(
        con,
        media_type=media["mediaType"],
        file_path=media["filePath"],
        source_id=source_id,
        file_hash=media.get("fileHash"),
        duration_ms=media.get("durationMs"),
        fps=media.get("fps"),
        summary_text=media.get("summaryText"),
        source_offset_ms=media.get("sourceOffsetMs"),
    )

    cut_ids: list[int] = []
    for cut in cuts:
        cut_id = source_db.upsert_cut(
            con,
            media_id=media_id,
            cut_index=cut["cutIndex"],
            start_ms=cut["startMs"],
            end_ms=cut["endMs"],
            description_text=cut["descriptionText"],
            shot_type=cut.get("shotType"),
        )
        cut_ids.append(cut_id)
        attached: list[int] = []
        for tag in cut.get("tags") or []:
            attached.append(source_db.tag_cut_by_value(
                con, cut_id=cut_id, category=tag["category"], value=tag["value"],
                source=tag.get("source"),
            ))
        # Replace this pipeline's own tag set, don't accumulate it: a tag
        # removed in staging must not survive in source.db still searchable.
        source_db.prune_ingest_cut_tags(con, cut_id=cut_id, keep_tag_ids=attached)
        # Replace, don't append: this function is the re-commit path too.
        source_db.clear_cut_dialogue(con, cut_id=cut_id)
        for line in cut.get("dialogue") or []:
            source_db.insert_dialogue(
                con, cut_id=cut_id, text=line["text"], speaker=line.get("speaker"),
                start_ms=line.get("startMs"), end_ms=line.get("endMs"),
            )

    con.commit()
    return {"sourceId": source_id, "mediaId": media_id, "cutIds": cut_ids}
