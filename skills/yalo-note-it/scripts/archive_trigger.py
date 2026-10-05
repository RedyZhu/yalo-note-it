"""Recognize explicit user authorization to archive a conversation."""
import re


TRIGGERS = (
    re.compile(r'亚楼.*?记一下', re.DOTALL),
    re.compile(r'\byalo\b.*?\bnote\s+it\b', re.DOTALL | re.IGNORECASE),
)


def is_trigger(text):
    return isinstance(text, str) and any(pattern.search(text) for pattern in TRIGGERS)
