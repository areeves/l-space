"""A heuristic check that model-written text only mentions things present in the data.

It is deliberately simple and conservative, not a proof:

* every capitalized word (names, titles) must appear somewhere in the evidence, and
* every number must appear in the evidence (small counting numbers up to the row count are
  allowed, so a numbered list does not trip it).

Evidence is the rows that were returned plus the user's own question. Sentence-initial
function words ("The", "You", "Here") are exempt. A reworded sentence that stays within the
data passes; an invented title, author, or figure does not.
"""

import re

_WORD = re.compile(r"[A-Za-z][A-Za-z'’]*")
_NUM = re.compile(r"\d+(?:\.\d+)?")

_SENTENCE_END = ".!?:\n"
_DECORATION = " \t*_#>-•("

_EXEMPT_AT_START = {
    "a", "an", "the", "i", "you", "your", "we", "my", "it", "its", "they", "there", "here",
    "this", "that", "these", "those", "based", "according", "in", "on", "of", "for", "and",
    "or", "but", "no", "yes", "none", "all", "some", "only", "also", "currently", "overall",
    "total", "showing", "sorry", "unfortunately", "one", "two", "three", "four", "five", "six",
    "seven", "eight", "nine", "ten", "both", "each", "every", "most", "least", "among", "from",
    "with", "by", "to", "is", "are", "was", "were", "has", "have", "had", "can", "which",
    "what", "who", "how", "many", "other", "another", "if", "at", "as", "so", "then", "books", "book", "series", "narrators", "narrator", "authors", "author", "library", "me",
}  # fmt: skip


def _norm(word: str) -> str:
    w = word.lower().replace("’", "'")
    return w.removesuffix("'s")


def _at_sentence_start(text: str, pos: int) -> bool:
    before = text[:pos].rstrip(_DECORATION)
    if not before:
        return True
    # "1. Title" and "- Title" list markers also count as a fresh sentence.
    before = before.rstrip("0123456789")
    return not before or before[-1] in _SENTENCE_END


def ungrounded_terms(answer: str, evidence: str, n_rows: int) -> list[str]:
    """Words and numbers in ``answer`` that the evidence does not support (empty = grounded)."""
    evidence_words = {_norm(w) for w in _WORD.findall(evidence)}
    evidence_numbers = set(_NUM.findall(evidence))

    flagged: list[str] = []
    for match in _WORD.finditer(answer):
        word = match.group()
        if not word[0].isupper() or word == "I":
            continue
        key = _norm(word)
        if _at_sentence_start(answer, match.start()) and key in _EXEMPT_AT_START:
            continue
        if key not in evidence_words and word not in flagged:
            flagged.append(word)

    for number in _NUM.findall(answer):
        allowed = number in evidence_numbers or (number.isdigit() and int(number) <= n_rows + 1)
        if not allowed and number not in flagged:
            flagged.append(number)
    return flagged
