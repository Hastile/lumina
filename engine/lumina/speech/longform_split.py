"""Korean long-form captions: semantic groups, soft length/time targets.

Independent of the Shorts model/priors. Only the existing Kiwi tokenizer and
script-to-word mapping are shared. No timing is interpolated or punctuation
removed. Long indivisible groups are retained and reported for review.
"""
import math
import re
import uuid

from shorts_split import compact, token_tags

CONNECTORS = {'그런데', '그리고', '그러나', '즉', '하지만', '따라서', '그래서', '또한'}


def split_indices(words, lines=None):
    if not words:
        return []
    tags = token_tags(words)
    n = len(words)
    required, blocked, edges = set(), set(), {}
    for i in range(n - 1):
        left = words[i]['word'].strip()
        bare = left.rstrip('.,!?。？！”’」』"\'')
        lt, rt = tags[i][1], tags[i + 1][0]
        # Decimal points are not sentence endings. Korean EF also works when
        # transcripts omit full stops. A following quotation particle/auxiliary
        # still belongs to the same group.
        sentence = bool(re.search(r'[.!?。？！][”’」』"\']*$', left)) or lt == 'EF' or (lt == 'JX' and bare.endswith('요'))
        if sentence and rt not in {'VX', 'VCP', 'JX', 'JKQ'}:
            required.add(i)
        if (bare in CONNECTORS or lt in {'ETM', 'MM'} or rt in {'VX', 'NNB', 'JX', 'JKQ', 'JKO', 'JKS'}
                or (lt in {'NNP', 'SL'} and rt == lt)
                or (lt in {'SN', 'NR'} and rt in {'NNB', 'NNG', 'NR', 'SN'})):
            blocked.add(i)
        # Clause ends are preferable to noun/verb or modifier/head splits.
        edges[i] = (0 if lt == 'EC' or left.endswith((',', '，')) else
                    2 if lt in {'JX', 'JKB'} else 7)
    required -= blocked
    if lines is not None:
        if len(lines) != n:
            raise ValueError('대본 줄과 단어 수가 일치하지 않습니다.')
        # Explicit user-authored lines override automatic grouping, regardless
        # of their length. This is opt-in, not the Shorts short-line heuristic.
        return [0] + [i for i in range(1, n) if lines[i] != lines[i - 1]]

    boundaries = [0] + [i + 1 for i in sorted(required)] + [n]
    starts = []
    for lo, hi in zip(boundaries, boundaries[1:]):
        prefix = [0]
        for w in words[lo:hi]:
            prefix.append(prefix[-1] + len(compact(w['word'])))
        best = {lo: 0.0}
        back = {}
        # Limit the DP to recent legal boundaries, not a hard token cap: a
        # long protected phrase remains reachable as a single caption.
        legal = [lo]
        for b in range(lo + 1, hi + 1):
            if b < hi and b - 1 in blocked:
                continue
            best[b] = math.inf
            for a in legal[-80:]:
                chars = prefix[b - lo] - prefix[a - lo]
                seconds = float(words[b - 1]['end']) - float(words[a]['start'])
                cost = (4 + .025 * max(0, chars - 24) ** 2
                        + .12 * max(0, chars - 30) ** 2
                        + 1.5 * max(0, seconds - 3.5) ** 2
                        + 3 * max(0, seconds - 5) ** 2)
                if not (a == lo and b == hi):
                    cost += .10 * max(0, 12 - chars) ** 2 + 12 * max(0, 1 - seconds) ** 2
                if b < hi:
                    cost += edges[b - 1]
                if best[a] + cost < best[b]:
                    best[b], back[b] = best[a] + cost, a
            legal.append(b)
        b, part = hi, []
        while b > lo:
            part.append(back[b])
            b = back[b]
        starts.extend(reversed(part))
    return starts


def split_segments(words, duration, lines=None):
    keep = [i for i, w in enumerate(words) if compact(w['word'])]
    if lines is not None:
        lines = [lines[i] for i in keep]
    words = [words[i] for i in keep]
    if any(not (0 <= float(w['start']) <= float(w['end']) <= duration + .001)
           for w in words) or any(float(a['start']) > float(b['start']) for a, b in zip(words, words[1:])):
        raise ValueError('단어 시간이 오디오 범위를 벗어나거나 순서가 맞지 않습니다. 시간 재정렬 후 다시 시도해 주세요.')
    starts = split_indices(words, lines)
    result = []
    for a, b in zip(starts, starts[1:] + [len(words)]):
        group = [dict(w, word=(' ' if i else '') + w['word'].strip()) for i, w in enumerate(words[a:b])]
        # Cue boundary is the next word onset; word/character times stay exact.
        start = 0.0 if a == 0 else float(words[a]['start'])
        end = float(words[b]['start']) if b < len(words) else duration
        if end <= start:
            raise ValueError('자막 경계의 단어 시간이 겹칩니다. 시간 재정렬 후 다시 시도해 주세요.')
        result.append(dict(id=uuid.uuid4().hex, start=start, end=end,
                           text=''.join(w['word'] for w in group), words=group))
    return result


def review_ids(segments):
    """Soft review signals, measured on displayed cue durations."""
    return [s['id'] for s in segments if len(compact(s['text'])) > 30 or s['end'] - s['start'] > 5]
