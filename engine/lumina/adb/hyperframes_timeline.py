"""Turns the bridge payload into one flat, absolutely-timed beat list.

Pure: takes the dict `hyperframes_bridge.read_composition` returns and gives
back beats, so every rule below is testable from fixture JSON with no node
and no Premiere.

Three things here are not obvious and are the reason this is its own module:

  * **Sub-compositions are instantiated, not merged.** The bridge returns
    each composition file once, but a file used by two hosts appears twice on
    the timeline at two different offsets. Walking hosts (rather than
    documents) is what makes a reused sub-composition come out as two sets of
    beats instead of one.
  * **Track order is a path, not a number.** A child's `zIndex` numbering is
    local to its own document and collides with the parent's. Carrying the
    whole chain of host indices as a tuple and dense-ranking those tuples
    keeps a sub-composition's layers adjacent to each other exactly where the
    host sat, instead of interleaving them with the parent's other layers.
  * **Quantization happens here, not in the FCP7 normalizer.**
    `normalize_sequence_spec` rounds a clip's start and its source range
    independently and rejects the whole spec on any one-frame overlap, so two
    beats that abut in float seconds can kill an entire port. Snapping in one
    place, with a reported adjustment per beat, keeps that failure from ever
    reaching the user as a message about clip names.
"""

from __future__ import annotations

from typing import Any, Optional

DEFAULT_FPS = 30
MAX_INSTANTIATION_DEPTH = 20

# parseHtml types these as media; anything else is a graphic that Premiere
# cannot reconstruct and that the port carries as structure only.
MEDIA_TYPES = ("video", "image", "audio")


class TimelineError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _documents_by_path(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {doc["filePath"]: doc for doc in payload.get("documents") or []}


def _custom_index(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Map every id a custom-attribute record answers to onto that record."""
    index: dict[str, dict[str, Any]] = {}
    for record in document.get("customAttributes") or []:
        if record.get("hfId"):
            index[record["hfId"]] = record
        if record.get("id"):
            index[record["id"]] = record
    return index


def _animation_index(document: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Group statically-parsed tweens by the DOM id their selector names.

    Only `#id` selectors can be attributed to a clip. Class and descendant
    selectors usually target children of a clip - a headline span, a card -
    which have no Premiere counterpart at all, so they are left unattributed
    rather than guessed onto the nearest clip.
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    for animation in document.get("animations") or []:
        selector = (animation.get("targetSelector") or "").strip()
        if not selector.startswith("#") or len(selector) < 2:
            continue
        dom_id = selector[1:]
        if not dom_id.replace("-", "").replace("_", "").isalnum():
            continue
        grouped.setdefault(dom_id, []).append(animation)
    return grouped


def _classify(element: dict[str, Any], custom: Optional[dict[str, Any]]) -> str:
    if element.get("isSubComposition"):
        return "subcomposition"
    if element.get("type") in MEDIA_TYPES:
        return "media"
    return "graphic"


def flatten_composition(
    payload: dict[str, Any],
    *,
    max_depth: int = MAX_INSTANTIATION_DEPTH,
) -> dict[str, Any]:
    """Walk the composition tree into one absolutely-timed beat list."""
    documents = _documents_by_path(payload)
    entry = payload.get("entry")
    if entry not in documents:
        raise TimelineError("TIMELINE_ENTRY_MISSING", f"Bridge payload has no document for entry {entry!r}.")

    beats: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = list(payload.get("warnings") or [])
    animation_spans: list[dict[str, Any]] = []

    def walk(file_path: str, time_offset: float, layer_path: tuple, depth: int,
             boundary_end: Optional[float], chain: tuple) -> None:
        document = documents.get(file_path)
        if document is None:
            return
        if file_path in chain:
            # Guarded here as well as in the bridge: the bridge dedupes
            # documents, while this walk instantiates them, so a cycle would
            # recur through the same file at ever-deeper offsets.
            warnings.append({"code": "SUBCOMPOSITION_CYCLE", "filePath": file_path, "chain": list(chain)})
            return
        if depth > max_depth:
            warnings.append({"code": "INSTANTIATION_DEPTH_EXCEEDED", "filePath": file_path, "depth": depth})
            return

        custom_index = _custom_index(document)
        animations_by_id = _animation_index(document)

        # Every tween proves the frame is not inert, whatever it targets.
        # Only `#id` tweens can be *attributed* to a clip, but a composition
        # that animates `.card` is still moving - collecting spans only from
        # the attributable ones would make the dead-window rule fire on
        # compositions that are visibly in motion.
        for animation in document.get("animations") or []:
            span_start = time_offset + float(animation.get("resolvedStart") or 0)
            span_end = span_start + float(animation.get("duration") or 0)
            if boundary_end is not None:
                span_end = min(span_end, boundary_end)
            if span_end > span_start:
                animation_spans.append({
                    "start": span_start, "end": span_end,
                    "targetSelector": animation.get("targetSelector"),
                })

        for element in document.get("elements") or []:
            if element.get("isCompositionRoot"):
                continue  # the container carries data-start; it is not a beat
            start = element.get("startTime")
            duration = element.get("duration")
            if start is None or duration is None or duration <= 0:
                warnings.append({
                    "code": "ELEMENT_TIMING_UNRESOLVED", "filePath": file_path,
                    "elementId": element.get("id"), "startTime": start, "duration": duration,
                })
                continue

            absolute_start = time_offset + float(start)
            absolute_end = absolute_start + float(duration)
            if boundary_end is not None and absolute_start >= boundary_end:
                warnings.append({
                    "code": "SUBCOMPOSITION_CHILD_OUTSIDE_HOST", "filePath": file_path,
                    "elementId": element.get("id"),
                })
                continue
            clipped = False
            if boundary_end is not None and absolute_end > boundary_end:
                absolute_end = boundary_end
                clipped = True

            custom = custom_index.get(element.get("id") or "")
            dom_id = (custom or {}).get("id") or element.get("id")
            own_layer = layer_path + (int(element.get("zIndex") or 0),)
            kind = _classify(element, custom)

            if kind == "subcomposition":
                child_path = element.get("resolvedSrc")
                if child_path in documents:
                    walk(child_path, absolute_start, own_layer, depth + 1, absolute_end,
                         chain + (file_path,))
                    continue
                # Unresolvable child: keep the span so the rebuild band still
                # shows something of the right length in the right place.
                warnings.append({
                    "code": "SUBCOMPOSITION_UNRESOLVED", "filePath": file_path,
                    "elementId": element.get("id"), "resolvedSrc": child_path,
                })

            element_animations = animations_by_id.get(dom_id or "", [])
            beats.append({
                "beatId": f"{len(beats):03d}_{dom_id or element.get('id')}",
                "elementId": element.get("id"),
                "domId": dom_id,
                "sourceDocument": file_path,
                "depth": depth,
                "kind": kind,
                "mediaType": element.get("type"),
                "src": element.get("src"),
                "name": element.get("name"),
                "startSeconds": absolute_start,
                "endSeconds": absolute_end,
                "durationSeconds": absolute_end - absolute_start,
                "mediaStartSeconds": element.get("mediaStartTime"),
                "layerPath": own_layer,
                "clippedToHost": clipped,
                "treatmentRaw": (custom or {}).get("treatment"),
                "beatLabel": (custom or {}).get("beat"),
                "text": (custom or {}).get("textContent"),
                "animations": element_animations,
            })

    walk(entry, 0.0, (), 0, None, ())

    beats.sort(key=lambda b: (b["startSeconds"], b["layerPath"]))
    _assign_track_indices(beats)
    return {
        "entry": entry,
        "fps": _resolve_fps(payload),
        "dimensions": (documents[entry] or {}).get("dimensions"),
        "resolution": (documents[entry] or {}).get("resolution"),
        "beats": beats,
        "animationSpans": sorted(animation_spans, key=lambda s: s["start"]),
        "warnings": warnings,
    }


def _assign_track_indices(beats: list[dict[str, Any]]) -> None:
    """Dense-rank the layer paths so tracks are 0..n-1 with no gaps.

    Lexicographic order on the path is what keeps a sub-composition's layers
    together at the host's own position: a host at z=2 with children at z=0,1
    yields (2,0),(2,1), which sort between the parent's (1,) and (5,).
    """
    distinct = sorted({beat["layerPath"] for beat in beats})
    rank = {path: index for index, path in enumerate(distinct)}
    for beat in beats:
        beat["trackIndex"] = rank[beat["layerPath"]]


def _resolve_fps(payload: dict[str, Any]) -> Optional[int]:
    project = payload.get("project") or {}
    fps = project.get("fps")
    if isinstance(fps, (int, float)) and fps > 0:
        return int(fps)
    return None


def quantize_beats(
    beats: list[dict[str, Any]],
    *,
    fps: int,
) -> dict[str, Any]:
    """Snap every beat to whole frames and remove residual same-track overlap.

    Returns the adjusted beats plus one adjustment record per change, because
    a beat that moved or got shortened is something the editor should see
    rather than discover in the timeline.
    """
    if fps <= 0:
        raise TimelineError("TIMELINE_BAD_FPS", f"fps must be positive, got {fps}.")

    adjustments: list[dict[str, Any]] = []
    quantized = []
    for beat in beats:
        start_frame = round(beat["startSeconds"] * fps)
        end_frame = round(beat["endSeconds"] * fps)
        if end_frame <= start_frame:
            end_frame = start_frame + 1
            adjustments.append({
                "beatId": beat["beatId"], "reason": "SHORTER_THAN_ONE_FRAME",
                "originalDurationSeconds": beat["durationSeconds"],
                "durationFrames": 1,
            })
        updated = dict(beat)
        updated["startFrame"] = start_frame
        updated["endFrame"] = end_frame
        quantized.append(updated)

    by_track: dict[int, list[dict[str, Any]]] = {}
    for beat in quantized:
        by_track.setdefault(beat["trackIndex"], []).append(beat)

    for track_index, group in sorted(by_track.items()):
        group.sort(key=lambda b: (b["startFrame"], b["endFrame"]))
        for left, right in zip(group, group[1:]):
            if right["startFrame"] < left["endFrame"]:
                # Trim the earlier beat: the later one's start is the cut the
                # author placed, so moving it would shift the edit.
                trimmed_to = right["startFrame"]
                if trimmed_to <= left["startFrame"]:
                    adjustments.append({
                        "beatId": left["beatId"], "reason": "OVERLAP_UNRESOLVABLE",
                        "trackIndex": track_index, "collidesWith": right["beatId"],
                    })
                    continue
                adjustments.append({
                    "beatId": left["beatId"], "reason": "TRIMMED_FOR_OVERLAP",
                    "trackIndex": track_index, "collidesWith": right["beatId"],
                    "fromFrame": left["endFrame"], "toFrame": trimmed_to,
                })
                left["endFrame"] = trimmed_to

    for beat in quantized:
        beat["startSeconds"] = beat["startFrame"] / fps
        beat["endSeconds"] = beat["endFrame"] / fps
        beat["durationSeconds"] = beat["endSeconds"] - beat["startSeconds"]

    quantized.sort(key=lambda b: (b["startSeconds"], b["trackIndex"]))
    return {"beats": quantized, "adjustments": adjustments, "fps": fps}
