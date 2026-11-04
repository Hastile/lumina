"""Deterministic caption finishing on acoustic timestamps, never estimated timing."""
import copy
import re
import uuid

TPS = 254016000000


def clean_text(text):
    text = text.rstrip()
    # Preserve ellipses, abbreviations and URLs. Closing quotes stay in place.
    match = re.search(r'([,，.。．]+)([”’"\')\]]*)$', text)
    if not match:
        return text
    mark = match.group(1)
    if '..' in mark or re.search(r'(?:https?://|www\.)\S+$', text) or re.search(r'(?:[A-Za-z]\.){2,}[”’"\')\]]*$', text):
        return text
    return (text[:match.start()] + match.group(2)).rstrip()


def finish_rows(rows, *, shorts=False, frame_ticks, first_start=0):
    """Keep existing boundaries; split only at punctuation/clause/phrase boundaries.

    Unresolvable long cues are explicitly warned, rather than fabricating timing.
    """
    output, warnings = [], []
    target = 2.5 if shorts else 3.5
    limit = 14 if shorts else 24
    for original in rows:
        row = copy.deepcopy(original)
        text = row['text']
        chars = sorted(row.get('chars', []), key=lambda c: c['offset'])
        acoustic = {c['offset']: c['start'] for c in chars}
        candidates = []
        for m in re.finditer(r'\s+', text):
            pos = m.end()
            prefix = text[:m.start()]
            # Protect incomplete predicates and names by requiring an explicit
            # sentence/clause ending; punctuation is considered before removal.
            if pos in acoustic and re.search(r'(?:[,.!?，。]|습니다|입니다|합니다|했죠|하죠|까요|는데|지만|으며|면서|이고|하고|해서|하면|되면|되자|으며)$', prefix):
                candidates.append(pos)
        starts = [0]
        base = 0
        while True:
            remaining = text[base:]
            at = row['start'] if base == 0 else acoustic[base]
            long = (row['end'] - at) / TPS > target or len(re.sub(r'\s', '', remaining)) > limit
            possible = [p for p in candidates if p > base and
                        acoustic[p] - at >= frame_ticks and row['end'] - acoustic[p] >= frame_ticks and
                        len(re.sub(r'\s', '', text[base:p])) >= 4]
            if not long or not possible:
                break
            pos = min(possible, key=lambda p: abs((acoustic[p] - at) / TPS - target) +
                      abs(len(re.sub(r'\s', '', text[base:p])) - limit) / limit)
            starts.append(pos)
            base = pos
        for i, (a, b) in enumerate(zip(starts, starts[1:] + [len(text)])):
            value = clean_text(text[a:b])
            if not value:
                raise ValueError('문장부호 정리 후 빈 자막입니다. 원문을 확인하세요.')
            # Removing terminal punctuation may shift a closing quote's offset.
            from ..commands.caption_timing import remap_timing
            timing = remap_timing({'text': text[a:b], 'chars': [{**c, 'offset': c['offset']-a} for c in chars if a <= c['offset'] < b]}, text[a:b], value)
            part = {**row, 'id': row['id'] if i == 0 else uuid.uuid4().hex,
                    'text': value, 'start': row['start'] if i == 0 else acoustic[a],
                    'end': row['end'] if b == len(text) else acoustic[b],
                    'chars': timing['chars'] if timing else []}
            if row.get('track_id'):
                part.update(source_id=row['id'], original_text=text, slice_start=a, slice_end=b)
            output.append(part)
    # Different overlapping caption tracks are finished independently by caller.
    for row in output:
        row['start'] = round(row['start'] / frame_ticks) * frame_ticks
        row['end'] = round(row['end'] / frame_ticks) * frame_ticks
    for left, right in zip(output, output[1:]):
        left['end'] = right['start']
    if output:
        output[0]['start'] = first_start
    for row in output:
        if row['end'] <= row['start']:
            raise ValueError('음성 경계가 같은 프레임에 겹칩니다. 정렬 결과를 확인하세요.')
        if row['end'] - row['start'] > target * TPS or len(re.sub(r'\s', '', row['text'])) > limit:
            warnings.append({'id': row['id'], 'reason': '긴 자막: 안전한 의미/음성 경계 부족 또는 발화 전후 무음. 미리보기 검수 필요'})
    return output, warnings
