"""Retain acoustic positions across edits that do not change spoken characters."""
import unicodedata


def spoken(text):
    return [(i, c) for i, c in enumerate(text) if not c.isspace() and not unicodedata.category(c).startswith('P')]


def remap_timing(timing, old, new):
    if not timing or timing.get('text') != old:
        return None
    before, after = spoken(old), spoken(new)
    if [c for _, c in before] != [c for _, c in after]:
        return None
    offsets = {a[0]: b[0] for a, b in zip(before, after)}
    return {**timing, 'text': new, 'chars': [{**c, 'offset': offsets[c['offset']]} for c in timing.get('chars', []) if c['offset'] in offsets]}
