"""Words spelled the way a child might spell them.

The kid's search of their own videos falls back to words close to what
they typed ("dinasor" finds "Dinosaurs") when nothing matches exactly.
The parent's blocked words are matched the same forgiving way in Find
new ("skibidy" is caught like "skibidi"), so a search that forgives
spelling never makes the parent's filter easier to get round.
"""

from __future__ import annotations

import re

_VOWELS = frozenset("aeiou")
# Letters a child writes for the same sound, made the same before comparing.
_SOUNDS = (("ph", "f"), ("ck", "k"), ("qu", "kw"), ("c", "k"), ("y", "i"), ("z", "s"))
_DOUBLE = re.compile(r"(.)\1+")


def sound_key(word: str) -> str:
    """A folded word written as it sounds: "Phone" and "fone" are both
    "fon". A silent final e goes ("trane" is "tran")."""
    for written, sound in _SOUNDS:
        word = word.replace(written, sound)
    word = _DOUBLE.sub(r"\1", word)
    if len(word) > 3 and word.endswith("e"):
        word = word[:-1]
    return word


def distance(a: str, b: str, vowels_close: bool = True) -> float:
    """Edits from a to b (a letter added, dropped, changed, or two next to
    each other swapped). With vowels_close, one vowel for another counts
    half: children mix vowels up most."""
    prev2: list[float] = []
    prev = [float(j) for j in range(len(b) + 1)]
    for i in range(1, len(a) + 1):
        cur = [float(i)] + [0.0] * len(b)
        for j in range(1, len(b) + 1):
            x, y = a[i - 1], b[j - 1]
            change = 0.0 if x == y else 0.5 if vowels_close and x in _VOWELS and y in _VOWELS else 1.0
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + change)
            if i > 1 and j > 1 and x == b[j - 2] and a[i - 2] == y:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        prev2, prev = prev, cur
    return prev[-1]


def _allowance(length: int) -> float:
    return 0 if length < 4 else 1 if length < 7 else 2


def closeness(typed: str, word: str) -> float | None:
    """How far a folded word is from what was typed, also counting the
    start of a longer word ("dinasor" is close to "dinosaurs"); None if
    too far. Words under 4 letters must match exactly."""
    t, w = sound_key(typed), sound_key(word)
    if w.startswith(t):
        return 0.0
    allowed = _allowance(len(t))
    if not allowed:
        return None
    lo, hi = max(1, len(t) - int(allowed)), len(t) + int(allowed)
    best = min(distance(t, w[:n]) for n in range(lo, min(hi, len(w)) + 1)) if len(w) >= lo else None
    return best if best is not None and best <= allowed else None


# A near spelling of a blocked word is caught only for words this long,
# one plain edit away and starting with the same letter, so short
# everyday words ("score" for "scary") are not blocked by mistake.
NEAR_BLOCK_MIN = 6


def near_blocked(word: str, blocked: str) -> bool:
    """Whether a folded word is a near spelling of a folded blocked word."""
    if len(blocked) < NEAR_BLOCK_MIN or not word or word[0] != blocked[0]:
        return False
    a, b = sound_key(word), sound_key(blocked)
    return abs(len(a) - len(b)) <= 1 and distance(a, b, vowels_close=False) <= 1
