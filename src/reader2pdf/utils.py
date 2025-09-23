# src/reader2pdf/utils.py
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import List


def sha256_hex(s: str) -> str:
    """
    Return the SHA-256 hash of the given string as a hexadecimal string.
    """
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def read_url_lines(path: Path) -> List[str]:
    """
    Read a text file and return a list of non-empty, non-comment lines.
    Lines starting with '#' are considered comments and ignored.
    """
    lines: List[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        lines.append(line)
    return lines
