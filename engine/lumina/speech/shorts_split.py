"""Shorts caption splitting: short 1–3 어절 captions in the user's own style.

Every gap between two 어절 gets a break probability from a small logistic
model over Korean morphology (Kiwi tags at the end of the left 어절 and the
start of the right one) and the pause between them. Dynamic programming then
picks the caption split that best agrees with those probabilities under a
caption-length prior. The model is learned from hand-split projects
(`tests/shorts/train.py`) and stored in `shorts_model.json`, so it follows
the user's edits rather than fixed rules.
"""
import json
import math
import re
from functools import lru_cache
from pathlib import Path

MODEL_PATH = Path(__file__).with_name('shorts_model.json')
PUNCT_TAGS = {'SF', 'SP', 'SS', 'SSO', 'SSC', 'SE', 'SO', 'SW'}
OPEN_QUOTES, CLOSE_QUOTES = '「『‘“〈《(\'"', '」』’”〉》)\'"'
MAX_CAPTION_TOKENS = 5
# Script lines up to this size count as captions the user split by hand.
KEEP_LINE_TOKENS, KEEP_LINE_CHARS = 4, 14
# Adverbs, conjunctions and determiners are few; learn them word by word.
LEXICAL_TAGS = {'MAG', 'MAJ', 'MM', 'IC'}
# Sentence punctuation the user removes from Shorts captions; ? and ! stay.
TRAILING_PUNCT = re.compile(r'(?<!\d)[.,。]+$')


@lru_cache(maxsize=1)
def kiwi():
    from kiwipiepy import Kiwi
    return Kiwi()


def compact(text):
    return ''.join(text.split())


def token_tags(words):
    """(first tag, last tag) of each 어절, tagging the whole text for context."""
    texts = [w['word'].strip() for w in words]
    offsets, at = [], 0
    for t in texts:
        offsets.append(at)
        at += len(t) + 1
    tags = [[] for _ in texts]
    index = 0
    for m in kiwi().tokenize(' '.join(texts)):
        while index + 1 < len(texts) and m.start >= offsets[index + 1]:
            index += 1
        if m.tag not in PUNCT_TAGS:
            tags[index].append(m.tag)
    return [(t[0], t[-1]) if t else ('PUNCT', 'PUNCT') for t in tags]


def pause_bucket(pause):
    for limit, name in ((.08, 'p0'), (.16, 'p1'), (.24, 'p2'), (.32, 'p3'), (.45, 'p4')):
        if pause < limit:
            return name
    return 'p5'


def gap_features(words, tags, i):
    left, right = words[i], words[i + 1]
    pause = float(right['start']) - float(left['end'])
    lt, rt = tags[i][1], tags[i + 1][0]
    text = left['word'].strip()
    return ['bias', 'L:' + lt, 'R:' + rt, 'LR:' + lt + '|' + rt, pause_bucket(pause),
            'Lq' if text[-1:] in '?!' else 'Lnq',
            'Ln%d' % min(len(compact(text)), 6), 'Rn%d' % min(len(compact(right['word'])), 6)] + [
            side + word['word'].strip() for side, word, tag in (('Lw:', left, tags[i]), ('Rw:', right, tags[i + 1]))
            if tag[0] == tag[1] and tag[0] in LEXICAL_TAGS]


def forbidden_gaps(words, tags):
    """Gaps a caption must never break: inside quotes and inside a run of foreign words."""
    blocked, depth = set(), 0
    for i, w in enumerate(words[:-1]):
        text = w['word'].strip()
        depth += sum(text.count(c) for c in '「『‘“〈《') - sum(text.count(c) for c in '」』’”〉》')
        if depth > 0 or (tags[i][1] == 'SL' and tags[i + 1][0] == 'SL'):
            blocked.add(i)
    return blocked


def required_gaps(words, tags):
    """Sentence ends always close a caption (never violated in the hand-split set)."""
    out = set()
    for i, w in enumerate(words[:-1]):
        text = w['word'].strip()
        if tags[i][1] == 'EF' or text.endswith(('?', '!', '.')) or (tags[i][1] == 'JX' and text.endswith('요')):
            out.add(i)
    return out


def script_lines(words, script):
    """Script line index of each word, or None when the words don't spell the script."""
    lines = [compact(line) for line in script.splitlines() if compact(line)]
    if ''.join(lines) != ''.join(compact(w['word']) for w in words):
        return None
    ends, total = [], 0
    for line in lines:
        total += len(line)
        ends.append(total)
    out, at, line = [], 0, 0
    for w in words:
        while line < len(ends) - 1 and at >= ends[line]:
            line += 1
        if at + len(compact(w['word'])) > ends[line]:
            return None  # A word spans a line break; the lines can't be kept.
        out.append(line)
        at += len(compact(w['word']))
    return out


def line_constraints(words, lines):
    """(gaps that must break, gaps that must not) from the user's script lines.

    Ending a caption-sized line is a deliberate break. A long line may just be
    pasted text that wrapped mid-phrase, so the break after it is left to the
    model, and so is the inside of a short line that follows it.
    """
    spans, start = [], 0
    for i in range(1, len(words) + 1):
        if i == len(words) or lines[i] != lines[i - 1]:
            spans.append((start, i))
            start = i
    fits = [b - a <= KEEP_LINE_TOKENS and sum(len(compact(w['word'])) for w in words[a:b]) <= KEEP_LINE_CHARS
            for a, b in spans]
    breaks, keep = set(), set()
    for k, (a, b) in enumerate(spans):
        if fits[k] and b < len(words):
            breaks.add(b - 1)
        if fits[k] and (k == 0 or fits[k - 1]):
            keep.update(range(a, b - 1))
    return breaks, keep


def load_model(path=MODEL_PATH):
    return json.loads(Path(path).read_text('utf-8'))


def break_logprobs(words, tags, model):
    weights, shift = model['weights'], model['prior'].get('break_bias', 0.0)
    out = []
    for i in range(len(words) - 1):
        z = shift + sum(weights.get(f, 0.0) for f in gap_features(words, tags, i))
        p = 1 / (1 + math.exp(-z))
        out.append((math.log(max(p, 1e-9)), math.log(max(1 - p, 1e-9))))
    return out


def caption_cost(words, a, b, model):
    """Log prior of words[a:b] being one caption."""
    prior = model['prior']
    n = b - a
    chars = sum(len(compact(w['word'])) for w in words[a:b])
    duration = float(words[b - 1]['end']) - float(words[a]['start'])
    score = prior['tokens'].get(str(n), prior['tokens_floor'])
    if n > 1:
        score -= prior['char_penalty'] * max(0, chars - prior['max_chars'])
        score -= prior['duration_penalty'] * max(0, duration - prior['max_duration'])
    return prior['weight'] * score


def split_indices(words, model=None, lines=None):
    """Caption start indices for words (each a dict with word/start/end).

    lines: the script line of each word; see line_constraints.
    """
    if not words:
        return []
    model = model or load_model()
    tags = token_tags(words)
    logp = break_logprobs(words, tags, model)
    blocked = forbidden_gaps(words, tags)
    required = required_gaps(words, tags) - blocked
    if lines:
        line_breaks, keep = line_constraints(words, lines)
        blocked = (blocked - line_breaks) | keep
        required = (required - keep) | line_breaks
    n = len(words)
    best = [-math.inf] * (n + 1)
    back = [0] * (n + 1)
    best[0] = 0.0
    for b in range(1, n + 1):
        for a in range(max(0, b - MAX_CAPTION_TOKENS), b):
            if best[a] == -math.inf or (b < n and b - 1 in blocked) or any(g in required for g in range(a, b - 1)):
                continue
            inside = sum(logp[g][1] for g in range(a, b - 1))
            edge = logp[b - 1][0] if b < n else 0.0
            score = best[a] + inside + edge + caption_cost(words, a, b, model)
            if score > best[b]:
                best[b], back[b] = score, a
    if best[n] == -math.inf:  # Constraints left no path (e.g. a very long quote).
        return [0]
    starts, b = [], n
    while b > 0:
        starts.append(back[b])
        b = back[b]
    return starts[::-1]


def strip_caption_punct(word):
    """Drop sentence punctuation from a word and its character timings."""
    text = word['word']
    stripped = TRAILING_PUNCT.sub('', text.rstrip())
    if stripped == text.rstrip() or not compact(stripped):
        return word
    out = dict(word, word=stripped)
    if word.get('chars'):
        out['chars'] = word['chars'][:len(compact(stripped))]
    return out


def split_segments(words, duration, model=None, lines=None):
    """Phrase Studio segments (start/end/text/words) cut for Shorts. lines: see split_indices."""
    import uuid
    keep = [i for i, w in enumerate(words) if compact(w['word'])]
    lines = [lines[i] for i in keep] if lines else None
    words = [dict(words[i], start=max(0.0, min(float(words[i]['start']), duration)), end=max(0.0, min(float(words[i]['end']), duration)))
             for i in keep]
    starts = split_indices(words, model, lines)
    result, previous = [], 0.0
    for a, b in zip(starts, starts[1:] + [len(words)]):
        group = [strip_caption_punct(w) for w in words[a:b]]
        start = max(previous, group[0]['start'])
        end = min(duration, max(start + .02, group[-1]['end']))
        if end <= start:
            continue
        text = ' '.join(w['word'].strip() for w in group)
        result.append({'id': uuid.uuid4().hex, 'start': start, 'end': end, 'text': text,
                       'words': [dict(w, word=(' ' if i else '') + w['word'].strip()) for i, w in enumerate(group)]})
        previous = end
    return result
