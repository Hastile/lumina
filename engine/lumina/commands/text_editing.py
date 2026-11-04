"""Text-panel edits: atomic, shared by the GUI and MCP, never duplicate the whole caption text."""
import copy
import hashlib
import json
from typing import Literal
from pydantic import Field
from .. import timebase
from ..model import new_id
from .registry import Params, Context, CommandError, TimeValue, command, resolve_time
from .project import seq_of
from .timeline import find_clip, require_unlocked, clip_path, write_clip, split_clip, clips_sorted
from .captions import first_line, caption_clip, new_caption_track


def text_fingerprint(seq, media=None):
    # Playhead/selection are not edits. Audio, text, effects and layout are.
    data = {k: seq.get(k) for k in ('tracks', 'video_tracks', 'audio_tracks', 'settings', 'transcript', 'master_volume_db')}
    data['media'] = media
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class SplitCaption(Params):
    sequence_id: str | None = None
    clip_id: str
    offset: int = Field(gt=0, description="Unicode character offset; left text ends here, right starts here")
    at: TimeValue | None = Field(default=None, description="Explicit split time, or acoustic character boundary when available. Never estimates timing from character count.")
    expected_text: str


@command('caption.split_text', SplitCaption)
def split_caption(ctx: Context, p: SplitCaption):
    """Split caption text at the cursor and its clip at a supplied/acoustic time, preserving styling in one Undo."""
    seq = seq_of(ctx.doc, p.sequence_id)
    tid, clip = find_clip(seq, p.clip_id)
    require_unlocked(seq, tid)
    cap = clip.get('caption')
    if not cap or cap['text'] != p.expected_text:
        raise CommandError('STALE_TEXT', '자막이 변경됐습니다. 최신 문구를 다시 읽어주세요.')
    text = cap['text']
    left, right = text[:p.offset].rstrip(), text[p.offset:].lstrip()
    if p.offset >= len(text) or not left or not right:
        raise CommandError('BAD_SPLIT', '문구 중간에 커서를 놓아주세요.')
    timing = cap.get('timing') or {}
    valid = timing.get('text') == text and timing.get('start') == clip['start'] and timing.get('end') == clip['end']
    at = resolve_time(p.at, seq['settings']) if p.at is not None else None
    if at is None and valid:
        following = [c for c in timing.get('chars', []) if c['offset'] >= p.offset]
        if following:
            at = resolve_time(int(following[0]['start']), seq['settings'])
    if at is None:
        raise CommandError('NEEDS_SPLIT_TIME', '음성 정렬 정보가 없습니다. 자막 안에 재생헤드를 놓거나 분할 시간을 지정하세요.')
    if not clip['start'] < at < clip['end']:
        raise CommandError('BAD_SPLIT', '분할 시간은 자막 시작과 끝 사이여야 합니다.')
    # Use the timeline slicing primitive so source-time effects remain continuous.
    original_cap = copy.deepcopy(cap)
    rid = split_clip(ctx, seq, tid, clip, at)
    right_offset = p.offset + len(text[p.offset:]) - len(text[p.offset:].lstrip())
    for cid, value, base, start, end in ((clip['id'], left, 0, clip['start'], at), (rid, right, right_offset, at, clip['end'])):
        updated = copy.deepcopy(original_cap)
        updated.update(text=value, timing=None)
        if valid:
            chars = [{**c, 'offset': c['offset'] - base} for c in timing.get('chars', []) if base <= c['offset'] < base + len(value)]
            updated['timing'] = {'text': value, 'start': start, 'end': end, 'chars': chars}
        path = clip_path(seq, tid, cid)
        ctx.m.set(path + ['caption'], updated)
        ctx.m.set(path + ['name'], first_line(value))
    return {'clip_ids': [clip['id'], rid], 'at': at, 'timing_basis': 'explicit' if p.at is not None else 'acoustic'}


class MergeCaption(Params):
    sequence_id: str | None = None
    clip_id: str
    direction: Literal['next', 'previous'] = 'next'


@command('caption.merge_next', MergeCaption)
def merge_caption(ctx: Context, p: MergeCaption):
    """Merge the next touching caption on the same track. Refuses differing styles/effects instead of discarding them."""
    seq = seq_of(ctx.doc, p.sequence_id)
    tid, left = find_clip(seq, p.clip_id)
    require_unlocked(seq, tid)
    ordered = clips_sorted(seq['tracks'][tid])
    i = next(i for i, c in enumerate(ordered) if c['id'] == left['id'])
    if p.direction == 'previous':
        if i == 0:
            raise CommandError('BAD_MERGE', '이전 자막이 없습니다.')
        i -= 1
        left = ordered[i]
    right = ordered[i + 1] if i + 1 < len(ordered) else None
    if not left.get('caption') or not right or not right.get('caption') or left['end'] != right['start']:
        raise CommandError('BAD_MERGE', '같은 트랙에서 바로 이어지는 두 자막만 합칠 수 있습니다.')
    if left['caption'].get('style') != right['caption'].get('style') or any(left.get(k) != right.get(k) for k in ('effects', 'keyframes', 'motion', 'opacity', 'enabled')):
        raise CommandError('STYLE_MISMATCH', '두 자막의 스타일 또는 효과가 달라 자동 병합하지 않았습니다.')
    cap = {**left['caption'], 'text': left['caption']['text'].rstrip() + ' ' + right['caption']['text'].lstrip(), 'timing': None}
    lt, rt = left['caption'].get('timing'), right['caption'].get('timing')
    if all(t and t.get('text') == c['caption']['text'] and t.get('start') == c['start'] and t.get('end') == c['end'] for t, c in ((lt, left), (rt, right))):
        prefix = len(left['caption']['text'].rstrip()) + 1
        trim = len(right['caption']['text']) - len(right['caption']['text'].lstrip())
        chars = [c for c in lt['chars'] if c['offset'] < prefix - 1]
        chars += [{**c, 'offset': c['offset'] - trim + prefix} for c in rt['chars'] if c['offset'] >= trim]
        cap['timing'] = {'text': cap['text'], 'start': left['start'], 'end': right['end'], 'chars': chars}
    write_clip(ctx, seq, tid, {**left, 'end': right['end'], 'caption': cap, 'name': first_line(cap['text'])})
    ctx.m.delete(clip_path(seq, tid, right['id']))
    return {'clip_id': left['id'], 'cursor_text': left['caption']['text'].rstrip()}


class TranscriptSet(Params):
    sequence_id: str | None = None
    text: str = Field(max_length=500000)


@command('text.set_script', TranscriptSet)
def set_script(ctx: Context, p: TranscriptSet):
    """Save the editable script without changing rendered captions or graphics."""
    seq = seq_of(ctx.doc, p.sequence_id)
    old = seq.get('transcript') or {}
    ctx.m.set(['sequences', seq['id'], 'transcript'], {**old, 'text': p.text})


class ApplySpeech(Params):
    sequence_id: str
    expected_fingerprint: str
    mode: str
    rows: list[dict]
    audio_track_id: str
    transcript_only: bool = False


@command('text.apply_speech', ApplySpeech)
def apply_speech(ctx: Context, p: ApplySpeech):
    """Apply a reviewed speech result atomically. Stale results cannot overwrite subsequent timeline edits."""
    seq = seq_of(ctx.doc, p.sequence_id)
    if text_fingerprint(seq, ctx.doc.get('media')) != p.expected_fingerprint:
        raise CommandError('STALE_RESULT', '분석 이후 타임라인 또는 대본이 바뀌었습니다. 다시 분석하세요.')
    if p.mode not in ('transcribe', 'script', 'retime') or not p.rows:
        raise CommandError('BAD_RESULT', '적용할 음성 분석 결과가 없습니다.')
    st = seq['settings']
    tpf = timebase.ticks_per_frame(st['fps_num'], st['fps_den'])
    new_tid = None
    ends = {}
    seen = set()
    original_clips = {}
    slices = {}
    for row in p.rows:
        start, end = resolve_time(row['start'], st), resolve_time(row['end'], st)
        if start < 0 or end - start < tpf or not row['text'].strip():
            raise CommandError('BAD_RESULT', '잘못된 자막 시간 또는 빈 문구가 있습니다.')
        if p.mode == 'retime':
            if row['id'] in seen:
                raise CommandError('BAD_RESULT', '중복된 자막 ID입니다.')
            seen.add(row['id'])
            source_id = row.get('source_id', row['id'])
            if source_id not in original_clips:
                original_clips[source_id] = copy.deepcopy(find_clip(seq, source_id))
            tid, old = original_clips[source_id]
            require_unlocked(seq, tid)
            if row.get('source_id'):
                from ..speech.caption_quality import clean_text
                a, b = row.get('slice_start'), row.get('slice_end')
                if (not old.get('caption') or old['caption']['text'] != row.get('original_text') or
                    type(a) is not int or type(b) is not int or not 0 <= a < b <= len(old['caption']['text']) or
                    clean_text(old['caption']['text'][a:b]) != row['text'] or
                    a != slices.get(source_id, 0) or (a == 0) != (row['id'] == source_id)):
                    raise CommandError('BAD_RESULT', '자막 분할 원문 또는 범위가 일치하지 않습니다.')
                slices[source_id] = b
                if row['id'] != source_id and any(row['id'] in t['clips'] for t in seq['tracks'].values()):
                    raise CommandError('BAD_RESULT', '새 자막 ID가 기존 클립과 충돌합니다.')
            elif not old.get('caption') or old['caption']['text'] != row['text']:
                raise CommandError('STALE_TEXT', '재정렬 결과와 기존 문구가 다릅니다.')
            clip = copy.deepcopy(old)
            clip.update(id=row['id'], name=first_line(row['text']))
            clip['caption']['text'] = row['text']
        else:
            if not new_tid and not p.transcript_only:
                new_tid = new_caption_track(ctx, seq, '음성 자막')
            tid = new_tid or 'transcript'
            clip = caption_clip(row['text'], start, end)
        if start < ends.get(tid, 0):
            raise CommandError('BAD_RESULT', '분석 결과 자막이 겹칩니다. 미리보기를 확인하세요.')
        ends[tid] = end
        clip.update(start=start, end=end)
        clip['caption']['timing'] = {'text': row['text'], 'start': start, 'end': end, 'chars': row.get('chars', [])}
        if not p.transcript_only:
            write_clip(ctx, seq, tid, clip)
    if p.mode == 'retime':
        if any(end != len(original_clips[cid][1]['caption']['text']) for cid, end in slices.items()):
            raise CommandError('BAD_RESULT', '분할 결과에 원문이 누락됐습니다.')
        # Also check against untouched cues on each affected track.
        for tid in ends:
            ordered = clips_sorted(seq['tracks'][tid])
            if any(a['end'] > b['start'] for a, b in zip(ordered, ordered[1:])):
                raise CommandError('OVERLAP', '기존 자막과 충돌해 적용하지 않았습니다.')
    else:
        ctx.m.set(['sequences', seq['id'], 'transcript'], {
            'text': '\n'.join(r['text'] for r in p.rows), 'rows': p.rows, 'audio_track_id': p.audio_track_id,
        })
    return {'count': len(p.rows), 'track_id': new_tid}
