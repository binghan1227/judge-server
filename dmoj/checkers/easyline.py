import re
from re import split as resplit
from typing import List, Union

from dmoj.error import InternalError
from dmoj.result import CheckerResult
from dmoj.utils.unicode import utf8bytes


def _normalize_line(b: bytes) -> bytes:
    """
    Case- and whitespace-insensitive:
    - Remove ALL whitespace
    - Lowercase
    """
    # remove all whitespace (spaces, tabs, etc.)
    b = re.sub(rb'\s+', b'', b)
    return b.lower()


def _split_nonblank_lines(raw: bytes) -> list[bytes]:
    """
    Split by CR/LF and drop lines that are empty or whitespace-only.
    """
    lines = resplit(b'[\r\n]', utf8bytes(raw))
    return [ln for ln in lines if ln is not None and ln.strip() != b'']


def check(
    process_output: bytes,
    judge_output: bytes,
    point_value: float = 1.0,
    point_distribution: List[int] = [1],
    filler_lines_required: bool = True,
    **kwargs
) -> Union[CheckerResult, bool]:
    # Split and drop whitespace-only lines
    judge_lines_raw = _split_nonblank_lines(judge_output)
    process_lines_raw = _split_nonblank_lines(process_output)

    if len(judge_lines_raw) != len(point_distribution):
        raise InternalError('point distribution length must equal to judge output (non-blank) length')

    if sum(point_distribution) == 0:
        raise InternalError('sum of point distribution must be positive')

    # Enforce same count of non-blank lines if required
    if filler_lines_required and len(process_lines_raw) != len(judge_lines_raw):
        return False

    # Normalize for tolerant comparison
    judge_lines = [_normalize_line(l) for l in judge_lines_raw]
    process_lines = [_normalize_line(l) for l in process_lines_raw]

    compare_count = min(len(process_lines), len(judge_lines))

    points = 0
    for i in range(compare_count):
        if process_lines[i] == judge_lines[i]:
            points += point_distribution[i]

    score = point_value * (points / sum(point_distribution))
    return CheckerResult(points > 0, score)

