"""Pure planning and analysis helpers for Premiere Core Operations Pack v1."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def normalized_media_path(value: str | None) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return os.path.normcase(os.path.normpath(value.strip()))


def looks_like_filesystem_path(value: str | None) -> bool:
    """Reject Premiere synthetic media tokens without treating them as missing files."""
    if not isinstance(value, str) or not value.strip():
        return False
    text = value.strip()
    return (
        text.startswith(("\\\\", "//"))
        or (len(text) >= 3 and text[1] == ":" and text[2] in {"\\", "/"})
        or Path(text).is_absolute()
    )


def analyze_offline_media(tree: dict[str, Any], verify_filesystem: bool = True, include_sequences: bool = False):
    offline, missing, inaccessible, warnings = [], [], [], []
    checked = 0
    for item in tree.get("items") or []:
        if not isinstance(item, dict):
            warnings.append("Null or malformed ProjectItem skipped.")
            continue
        if item.get("sequence") and not include_sequences:
            continue
        path = item.get("mediaPath")
        premiere_offline = item.get("premiereOffline")
        reason = None
        if premiere_offline is True:
            reason = "premiereOffline"
            offline.append({**item, "offlineReason": reason})
        if not path:
            status = "unsupportedItemType" if item.get("projectItemType") in {"style", "bin", "root"} else "mediaPathUnavailable"
            missing.append({**item, "pathStatus": status})
            continue
        if not looks_like_filesystem_path(path):
            missing.append({**item, "pathStatus": "unsupportedItemType"})
            continue
        if not verify_filesystem:
            continue
        checked += 1
        try:
            exists = Path(path).exists()
        except PermissionError:
            inaccessible.append({**item, "pathStatus": "permissionDenied"})
            continue
        except OSError as exc:
            inaccessible.append({**item, "pathStatus": "networkPathUnavailable", "filesystemError": str(exc)})
            continue
        if not exists:
            missing.append({**item, "pathStatus": "mediaPathMissing"})
    return {
        "offlineItems": offline,
        "missingPathItems": missing,
        "inaccessibleItems": inaccessible,
        "checkedItemCount": checked,
        "warnings": warnings,
    }


def _file_identity(path: str) -> tuple[int, int] | None:
    try:
        stat = Path(path).stat()
        return stat.st_size, stat.st_mtime_ns
    except OSError:
        return None


def file_sha256(path: str, max_bytes: int | None = None) -> str | None:
    try:
        file_path = Path(path)
        if max_bytes is not None and file_path.stat().st_size > max_bytes:
            return None
        digest = hashlib.sha256()
        with file_path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
    except OSError:
        return None


def find_duplicate_media_groups(
    tree: dict[str, Any],
    *,
    method: str = "auto",
    compute_hash: bool = False,
    max_hash_file_size_bytes: int | None = None,
):
    allowed = {"path", "normalized_path", "file_identity", "sha256", "auto"}
    if method not in allowed:
        raise ValueError("method must be path, normalized_path, file_identity, sha256, or auto")
    records = []
    warnings = []
    for item in tree.get("items") or []:
        path = item.get("mediaPath")
        if not looks_like_filesystem_path(path):
            continue
        record = {
            "identity": item.get("identity"), "name": item.get("name"), "treePath": item.get("treePath"),
            "mediaPath": path, "normalizedPath": normalized_media_path(path),
            "fileIdentity": _file_identity(path), "sha256": None,
        }
        if compute_hash or method == "sha256":
            record["sha256"] = file_sha256(path, max_hash_file_size_bytes)
            if record["sha256"] is None:
                warnings.append(f"Hash unavailable: {path}")
        records.append(record)

    def key_for(record):
        if method == "path":
            return ("path", record["mediaPath"])
        if method == "normalized_path":
            return ("normalized_path", record["normalizedPath"])
        if method == "file_identity":
            return ("file_identity", record["fileIdentity"])
        if method == "sha256":
            return ("sha256", record["sha256"])
        if record["normalizedPath"]:
            return ("normalized_path", record["normalizedPath"])
        return ("file_identity", record["fileIdentity"])

    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for record in records:
        key = key_for(record)
        if key[1] is not None:
            grouped.setdefault(key, []).append(record)
    duplicate_groups = [
        {"method": key[0], "key": key[1], "count": len(values), "items": values}
        for key, values in grouped.items() if len(values) > 1
    ]
    filename_groups: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        filename_groups.setdefault(Path(record["mediaPath"]).name.casefold(), []).append(record)
    same_name_different = [
        {"filename": key, "items": values}
        for key, values in filename_groups.items()
        if len(values) > 1 and len({value["normalizedPath"] for value in values}) > 1
    ]
    return {
        "scannedItemCount": len(records),
        "duplicateGroups": duplicate_groups,
        "duplicateGroupCount": len(duplicate_groups),
        "sameFilenameDifferentPathGroups": same_name_different,
        "warnings": warnings,
    }


def sequence_fingerprint(sequence: dict[str, Any]) -> str:
    """Structural hash: sequenceId/name/track counts/duration/markerCount
    plus each track's own name/itemCount and (if `items` was populated on
    this dict) each item's startTicks/endTicks/projectItemId.

    This function trusts whatever `sequence` dict it is given - it never
    fetches anything itself. Feeding it a sequence dict fetched with
    track items *not* populated (e.g. `list_sequences_detailed`'s default
    `include_items=False`) silently produces a hash that is blind to every
    item-level change (timing, projectItemId, in-track item count) while
    still looking like a normal hex digest - confirmed live as a real
    footgun: the exact same sequence, fingerprinted via this same function
    through two different call sites, produced two different values purely
    because one path had items populated and the other did not. This is
    NOT a second fingerprint scheme; it is this one function fed
    structurally incomplete input. Callers that need the authoritative,
    item-aware value (anything comparing against a "production invariant"
    or a staleness/expected_fingerprint check) must use
    `production_sequence_fingerprint` instead, which validates completeness
    before hashing rather than silently degrading."""
    tracks = []
    for key in ("videoTracks", "audioTracks"):
        for track in sequence.get(key) or []:
            tracks.append({
                "mediaType": track.get("mediaType"), "trackIndex": track.get("trackIndex"),
                "name": track.get("name"), "itemCount": track.get("itemCount"),
                "items": [
                    {
                        "startTicks": item.get("startTicks"), "endTicks": item.get("endTicks"),
                        "projectItemId": item.get("projectItemId"),
                    }
                    for item in track.get("items") or []
                ],
            })
    value = {
        "sequenceId": sequence.get("sequenceId"), "name": sequence.get("name"),
        "videoTrackCount": sequence.get("videoTrackCount"), "audioTrackCount": sequence.get("audioTrackCount"),
        "captionTrackCount": sequence.get("captionTrackCount"), "duration": sequence.get("duration"),
        "markerCount": sequence.get("markerCount"), "tracks": tracks,
    }
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


PRODUCTION_FINGERPRINT_SCHEMA_VERSION = "production-sequence-fingerprint/1"


def sequence_fingerprint_completeness(sequence: dict[str, Any]) -> list[str]:
    """Pure check: which tracks (if any) report a nonzero itemCount but have
    no populated items list, making sequence_fingerprint's result on this
    dict item-blind for that track. Empty list means the dict is complete
    enough for production_sequence_fingerprint to trust."""
    missing = []
    for key in ("videoTracks", "audioTracks"):
        for track in sequence.get(key) or []:
            item_count = track.get("itemCount") or 0
            items = track.get("items")
            if item_count > 0 and not items:
                missing.append(f"{track.get('mediaType')}:{track.get('trackIndex')}")
    return missing


def production_sequence_fingerprint(sequence: dict[str, Any]) -> str:
    """The single authoritative fingerprint for production-sequence-
    invariance and staleness checks (schema `production-sequence-fingerprint/1`).
    Reuses sequence_fingerprint's exact hash computation - this is not a
    second hashing scheme - but first validates every track with a nonzero
    itemCount actually has its items populated, raising ValueError instead
    of silently returning an incomplete/misleading hash. Every call site in
    this codebase that already fetches sequences via
    `_list_sequences_detailed_impl(True)` (i.e. `_edit_sequence_record`) is
    already complete and will never raise here; this guard exists for any
    future or external caller that might pass an `include_items=False`-shaped
    dict instead."""
    incomplete = sequence_fingerprint_completeness(sequence)
    if incomplete:
        raise ValueError(
            f"PRODUCTION_FINGERPRINT_UNAVAILABLE: track(s) {incomplete} report items but none are populated on "
            "this sequence dict; fetch with include_items=True before computing the authoritative fingerprint."
        )
    return sequence_fingerprint(sequence)


def protected_sequence_ids(sequence_list: dict[str, Any]) -> dict[str, str]:
    # Active-sequence and caption-track (V7, 36-item) delete/mutation
    # protection has been removed: no sequence is reported as protected.
    return {}


def sequence_is_empty(sequence: dict[str, Any]) -> bool:
    return (
        all((track.get("itemCount") or 0) == 0 for track in sequence.get("videoTracks") or [])
        and all((track.get("itemCount") or 0) == 0 for track in sequence.get("audioTracks") or [])
        and (sequence.get("captionTrackCount") or 0) == 0
        and (sequence.get("markerCount") or 0) == 0
    )


def track_is_empty(track: dict[str, Any]) -> bool:
    return (track.get("itemCount") or 0) == 0


def plan_track_index_shift(track_count: int, deleted_index: int) -> list[dict[str, int]]:
    if deleted_index < 0 or deleted_index >= track_count:
        raise ValueError("TRACK_NOT_FOUND")
    return [
        {"beforeIndex": index, "afterIndex": index - 1}
        for index in range(deleted_index + 1, track_count)
    ]


def marker_identity(marker: dict[str, Any]) -> tuple[Any, ...]:
    return (
        marker.get("guid"), marker.get("startTicks"), marker.get("type"), marker.get("name")
    )


def marker_patch_plan(marker: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
    allowed = {"startTicks", "durationTicks", "name", "comments", "type", "colorIndex"}
    applied = {key: value for key, value in changes.items() if key in allowed and value is not None}
    after = dict(marker)
    after.update(applied)
    return {
        "before": dict(marker),
        "after": after,
        "rollback": {key: marker.get(key) for key in applied},
        "changedFields": sorted(applied),
    }


def stale_fingerprint(expected: str | None, actual: str) -> bool:
    return expected is not None and expected != actual
