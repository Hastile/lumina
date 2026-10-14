"""Strict, side-effect-free SubRip parsing helpers.

This module deliberately has no Premiere or MCP dependency so parser behavior
can be verified with temporary fixtures without opening or mutating a project.
"""

from __future__ import annotations

import codecs
import hashlib
import math
import re
import unicodedata
from pathlib import Path
from typing import Optional


MAX_CUE_DURATION_MILLISECONDS = 10 * 60 * 1000

_TIMING_RE = re.compile(
    r"^\s*"
    r"(?P<sh>\d+):(?P<sm>[0-5]\d):(?P<ss>[0-5]\d)[,.](?P<sms>\d{3})"
    r"\s*-->\s*"
    r"(?P<eh>\d+):(?P<em>[0-5]\d):(?P<es>[0-5]\d)[,.](?P<ems>\d{3})"
    r"(?:\s+(?P<settings>.*?))?\s*$"
)


def _error(code: str, message: str, details: Optional[dict] = None) -> dict:
    return {"code": code, "message": message, "details": details}


def _empty_parse_result(error: dict, *, encoding: Optional[str] = None) -> dict:
    return {
        "status": "FAILURE",
        "encoding": encoding,
        "hadBom": False,
        "newlineStyle": None,
        "sourceBlockCount": 0,
        "cueCount": 0,
        "validCueCount": 0,
        "skippedCueCount": 0,
        "firstStartSeconds": None,
        "lastEndSeconds": None,
        "totalSpanSeconds": None,
        "fingerprintSha256": hashlib.sha256(b"").hexdigest(),
        "cues": [],
        "overlaps": [],
        "skippedCues": [],
        "warnings": [],
        "error": error,
        "mutationOccurred": False,
    }


def _encoding_name(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    aliases = {
        "utf8": "utf-8",
        "utf-8-sig": "utf-8",
        "utf16": "utf-16",
        "utf16-le": "utf-16-le",
        "utf16-be": "utf-16-be",
        "sjis": "shift_jis",
        "shift-jis": "shift_jis",
        "shift-jisx0213": "shift_jis",
        "ms949": "cp949",
        "uhc": "cp949",
    }
    return aliases.get(normalized, normalized)


def _decode_srt(data: bytes, requested_encoding: str) -> tuple[str, str, bool, list[str]]:
    warnings: list[str] = []
    requested = _encoding_name(requested_encoding)
    if requested != "auto":
        try:
            codecs.lookup(requested)
        except LookupError as exc:
            raise ValueError(f"Unknown encoding: {requested_encoding}") from exc

    had_bom = False
    detected: Optional[str] = None
    codec_name: Optional[str] = None

    if data.startswith(codecs.BOM_UTF8):
        had_bom, detected, codec_name = True, "utf-8", "utf-8-sig"
    elif data.startswith(codecs.BOM_UTF16_LE):
        had_bom, detected, codec_name = True, "utf-16-le", "utf-16"
    elif data.startswith(codecs.BOM_UTF16_BE):
        had_bom, detected, codec_name = True, "utf-16-be", "utf-16"

    if requested != "auto":
        detected = requested
        codec_name = requested
        if had_bom and requested == "utf-8":
            codec_name = "utf-8-sig"
        elif had_bom and requested in {"utf-16", "utf-16-le", "utf-16-be"}:
            codec_name = "utf-16"
        try:
            text = data.decode(codec_name, errors="strict")
        except UnicodeDecodeError as exc:
            raise UnicodeError(f"Could not decode SRT as {requested}: {exc}") from exc
    elif codec_name is not None:
        text = data.decode(codec_name, errors="strict")
    else:
        # BOM-less UTF-16 is common enough to identify by alternating NULs.
        if len(data) >= 4 and data[1] == 0 and data[3] == 0:
            detected, codec_name = "utf-16-le", "utf-16-le"
        elif len(data) >= 4 and data[0] == 0 and data[2] == 0:
            detected, codec_name = "utf-16-be", "utf-16-be"

        if codec_name is not None:
            text = data.decode(codec_name, errors="strict")
        else:
            decode_errors = []
            text = None
            for candidate in ("utf-8", "cp949", "shift_jis"):
                try:
                    text = data.decode(candidate, errors="strict")
                    detected = candidate
                    if candidate != "utf-8":
                        warnings.append(
                            f"Encoding auto-detection used the explicit fallback {candidate}."
                        )
                    break
                except UnicodeDecodeError as exc:
                    decode_errors.append(f"{candidate}: {exc}")
            if text is None:
                raise UnicodeError("; ".join(decode_errors))

    if text.startswith("\ufeff"):
        text = text[1:]
        had_bom = True
    if "\ufffd" in text:
        raise UnicodeError("Decoded text contains the Unicode replacement character U+FFFD.")
    return text, detected or requested, had_bom, warnings


def _newline_style(text: str) -> str:
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    cr = text.count("\r") - crlf
    kinds = sum(count > 0 for count in (crlf, lf, cr))
    if kinds > 1:
        return "MIXED"
    if crlf:
        return "CRLF"
    if lf:
        return "LF"
    if cr:
        return "CR"
    return "NONE"


def _blocks(text: str) -> list[list[tuple[int, str]]]:
    blocks: list[list[tuple[int, str]]] = []
    current: list[tuple[int, str]] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            if current:
                blocks.append(current)
                current = []
        else:
            current.append((line_number, line))
    if current:
        blocks.append(current)
    return blocks


def _time_milliseconds(hours: str, minutes: str, seconds: str, milliseconds: str) -> int:
    return (
        int(hours) * 3_600_000
        + int(minutes) * 60_000
        + int(seconds) * 1_000
        + int(milliseconds)
    )


def _canonical_text(text: str) -> str:
    normalized = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    return "\n".join(line.rstrip() for line in normalized.strip().split("\n"))


def srt_fingerprint(cues: list[dict]) -> str:
    digest = hashlib.sha256()
    for cue in cues:
        record = (
            f"{int(cue['startMilliseconds'])}\t{int(cue['endMilliseconds'])}\t"
            f"{_canonical_text(cue['text'])}\n"
        )
        digest.update(record.encode("utf-8"))
    return digest.hexdigest()


def offset_cues(cues: list[dict], offset_seconds: float) -> tuple[list[dict], int]:
    if isinstance(offset_seconds, bool) or not isinstance(offset_seconds, (int, float)):
        raise ValueError("start_offset_seconds must be a finite number, not bool.")
    if not math.isfinite(float(offset_seconds)) or offset_seconds < 0:
        raise ValueError("start_offset_seconds must be finite and >= 0.")
    offset_ms = int(round(float(offset_seconds) * 1000.0))
    shifted = []
    for cue in cues:
        out = dict(cue)
        out["startMilliseconds"] = cue["startMilliseconds"] + offset_ms
        out["endMilliseconds"] = cue["endMilliseconds"] + offset_ms
        out["startSeconds"] = out["startMilliseconds"] / 1000.0
        out["endSeconds"] = out["endMilliseconds"] / 1000.0
        out["durationSeconds"] = (out["endMilliseconds"] - out["startMilliseconds"]) / 1000.0
        shifted.append(out)
    return shifted, offset_ms


def parse_srt_bytes(data: bytes, *, encoding: str = "auto", strict: bool = False) -> dict:
    if not isinstance(encoding, str) or not encoding.strip():
        return _empty_parse_result(
            _error("SRT_ENCODING_ERROR", "encoding must be a non-empty string.")
        )
    if type(strict) is not bool:
        return _empty_parse_result(_error("INVALID_ARGUMENT", "strict must be bool."))

    try:
        text, detected_encoding, had_bom, warnings = _decode_srt(data, encoding)
    except (UnicodeError, ValueError) as exc:
        return _empty_parse_result(_error("SRT_ENCODING_ERROR", str(exc)))

    newline_style = _newline_style(text)
    source_blocks = _blocks(text)
    cues: list[dict] = []
    skipped: list[dict] = []
    fatal_issues: list[dict] = []
    seen_indices: set[int] = set()
    previous_start: Optional[int] = None

    for block_index, block in enumerate(source_blocks):
        source_line_start = block[0][0]
        source_line_end = block[-1][0]
        raw_lines = [line for _, line in block]
        source_index = None
        timing_position = 0
        if re.fullmatch(r"\d+", raw_lines[0].strip()):
            source_index = int(raw_lines[0].strip())
            timing_position = 1

        if timing_position >= len(raw_lines):
            issue = {
                "blockIndex": block_index,
                "sourceIndex": source_index,
                "sourceLineStart": source_line_start,
                "sourceLineEnd": source_line_end,
                "code": "MISSING_TIMECODE",
                "message": "Cue block has no timing line.",
            }
            skipped.append(issue)
            fatal_issues.append(issue)
            continue

        match = _TIMING_RE.fullmatch(raw_lines[timing_position])
        if not match:
            issue = {
                "blockIndex": block_index,
                "sourceIndex": source_index,
                "sourceLineStart": source_line_start,
                "sourceLineEnd": source_line_end,
                "code": "INVALID_TIMECODE",
                "message": "Cue timing line is not valid SubRip timecode.",
            }
            skipped.append(issue)
            fatal_issues.append(issue)
            continue

        start_ms = _time_milliseconds(match["sh"], match["sm"], match["ss"], match["sms"])
        end_ms = _time_milliseconds(match["eh"], match["em"], match["es"], match["ems"])
        if end_ms <= start_ms:
            issue = {
                "blockIndex": block_index,
                "sourceIndex": source_index,
                "sourceLineStart": source_line_start,
                "sourceLineEnd": source_line_end,
                "code": "INVALID_CUE_RANGE",
                "message": "Cue end time must be greater than start time.",
                "startMilliseconds": start_ms,
                "endMilliseconds": end_ms,
            }
            skipped.append(issue)
            fatal_issues.append(issue)
            continue

        text_lines = [line.rstrip() for line in raw_lines[timing_position + 1 :]]
        cue_text = "\n".join(text_lines).strip()
        if not cue_text:
            issue = {
                "blockIndex": block_index,
                "sourceIndex": source_index,
                "sourceLineStart": source_line_start,
                "sourceLineEnd": source_line_end,
                "code": "EMPTY_CUE_TEXT",
                "message": "Cue text is empty.",
            }
            skipped.append(issue)
            fatal_issues.append(issue)
            continue

        if source_index is not None:
            if source_index in seen_indices:
                issue = {
                    "code": "DUPLICATE_CUE_INDEX",
                    "sourceIndex": source_index,
                    "sourceLineStart": source_line_start,
                    "message": f"Cue index {source_index} is duplicated.",
                }
                warnings.append(issue["message"])
                fatal_issues.append(issue)
            seen_indices.add(source_index)

        if previous_start is not None and start_ms < previous_start:
            issue = {
                "code": "CUE_ORDER_REVERSED",
                "sourceIndex": source_index,
                "sourceLineStart": source_line_start,
                "message": "Cue starts earlier than the preceding valid cue.",
            }
            warnings.append(issue["message"])
            fatal_issues.append(issue)
        previous_start = start_ms

        duration_ms = end_ms - start_ms
        if duration_ms > MAX_CUE_DURATION_MILLISECONDS:
            warnings.append(
                f"Cue at source line {source_line_start} is unusually long ({duration_ms / 1000.0:.3f}s)."
            )

        cue = {
            "sourceIndex": source_index,
            "normalizedIndex": len(cues),
            "startMilliseconds": start_ms,
            "endMilliseconds": end_ms,
            "startSeconds": start_ms / 1000.0,
            "endSeconds": end_ms / 1000.0,
            "durationSeconds": duration_ms / 1000.0,
            "text": cue_text,
            "lines": cue_text.split("\n"),
            "settings": match["settings"] or None,
            "sourceLineStart": source_line_start,
            "sourceLineEnd": source_line_end,
        }
        cues.append(cue)

    overlaps = []
    ordered = sorted(cues, key=lambda cue: (cue["startMilliseconds"], cue["endMilliseconds"]))
    for left_index, left in enumerate(ordered):
        for right in ordered[left_index + 1 :]:
            if right["startMilliseconds"] >= left["endMilliseconds"]:
                break
            overlap_ms = min(left["endMilliseconds"], right["endMilliseconds"]) - right["startMilliseconds"]
            if overlap_ms > 0:
                overlaps.append({
                    "leftNormalizedIndex": left["normalizedIndex"],
                    "rightNormalizedIndex": right["normalizedIndex"],
                    "startMilliseconds": right["startMilliseconds"],
                    "endMilliseconds": min(left["endMilliseconds"], right["endMilliseconds"]),
                    "durationMilliseconds": overlap_ms,
                    "durationSeconds": overlap_ms / 1000.0,
                })
    if overlaps:
        warnings.append(f"Detected {len(overlaps)} overlapping cue pair(s).")

    first_start = min((cue["startMilliseconds"] for cue in cues), default=None)
    last_end = max((cue["endMilliseconds"] for cue in cues), default=None)
    result = {
        "status": "SUCCESS",
        "encoding": detected_encoding,
        "hadBom": had_bom,
        "newlineStyle": newline_style,
        "sourceBlockCount": len(source_blocks),
        "cueCount": len(cues),
        "validCueCount": len(cues),
        "skippedCueCount": len(skipped),
        "firstStartSeconds": None if first_start is None else first_start / 1000.0,
        "lastEndSeconds": None if last_end is None else last_end / 1000.0,
        "totalSpanSeconds": None if first_start is None else (last_end - first_start) / 1000.0,
        "fingerprintSha256": srt_fingerprint(cues),
        "cues": cues,
        "overlaps": overlaps,
        "skippedCues": skipped,
        "warnings": warnings,
        "error": None,
        "mutationOccurred": False,
    }

    if not data or not text.strip() or not cues:
        result["status"] = "FAILURE"
        result["error"] = _error("SRT_NO_VALID_CUES", "The SRT contains no valid cues.")
    elif strict and fatal_issues:
        result["status"] = "FAILURE"
        result["error"] = _error(
            "SRT_PARSE_ERROR",
            f"Strict parsing rejected {len(fatal_issues)} issue(s).",
            {"issues": fatal_issues},
        )
    return result


def parse_srt_file(file_path: str, *, encoding: str = "auto", strict: bool = False) -> dict:
    path = Path(file_path)
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        result = _empty_parse_result(
            _error("SRT_FILE_NOT_FOUND", f"SRT file does not exist: {path}")
        )
        result.update({"filePath": str(path), "fileName": path.name, "fileSizeBytes": None})
        return result
    except OSError as exc:
        result = _empty_parse_result(
            _error("SRT_FILE_NOT_FOUND", f"Could not read SRT file: {exc}")
        )
        result.update({"filePath": str(path), "fileName": path.name, "fileSizeBytes": None})
        return result

    result = parse_srt_bytes(data, encoding=encoding, strict=strict)
    result["filePath"] = str(path.resolve())
    result["fileName"] = path.name
    result["fileSizeBytes"] = len(data)
    return result
