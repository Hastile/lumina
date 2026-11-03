---
name: shorts-marker-workflow
description: "How the user uses markers in Shorts: clip markers on footage put on caption starts, often by changing clip speed"
metadata:
  node_type: memory
  type: project
  originSessionId: 8b3fa121-2318-4ff0-bc75-804fc8bd03ba
  modified: 2026-10-21T05:43:18.999Z
---

User, 2026-10-21: markers mark cut timing; they line captions up with them, and they change a clip's speed so a marker lands in the right place.

The past Premiere projects confirm it (read-only):
- Shorts sequences hold no sequence markers. The markers are clip markers on the footage (master clip markers, source time).
- 263 footage clip markers fall inside clips of Shorts timelines. 87% of them land within 0.1 s of a caption start; the marked moment sits a median 1.5 s into the clip.
- 24% of those clips have speed ≠ 100% (86–174%), stretched so the marked moment hits the caption start.

Lumina has this since commit 7983dd4 (2026-10-21):
- Clip markers on media: `marker.add` with clip_id / media_id. M with a clip selected marks the clip; M in the Source monitor marks the media.
- They show as flags on the clips and snap.
- `clip.align_marker` with mode speed keeps the clip's start, first frame and end; mode move slides the clip.
- Premiere import brings the master clip markers along. Projects imported before that need re-importing to get them.

**Why:** the edit step should put the action moment of a footage clip on the narration's caption start.
**How to apply:** when placing footage, mark (or reuse) the moment, then `clip.align_marker` it onto the caption start. Speed is usually 85–130%; beyond that, move or re-cut instead. Related: [[autonomous-shorts-editing-goal]], [[shorts-sfx-grammar]].
