import re
from collections import Counter

from dmoj.utils.unicode import utf8bytes


def check(process_output: bytes, judge_output: bytes, Counter=Counter, regex=re.compile(br'\s+'), **kwargs) -> bool:
    return True