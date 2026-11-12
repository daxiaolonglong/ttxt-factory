"""64 位 SimHash(中文双字 + 英文/数字词,TF 加权)。"""

from __future__ import annotations

import hashlib
import re
from collections import Counter

CJK_RUN = re.compile(r"[\u4e00-\u9fff]+|[A-Za-z0-9]+")


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for match in CJK_RUN.finditer(text or ""):
        chunk = match.group(0)
        if re.fullmatch(r"[\u4e00-\u9fff]+", chunk):
            if len(chunk) == 1:
                tokens.append(chunk)
            else:
                tokens.extend(chunk[i : i + 2] for i in range(len(chunk) - 1))
        else:
            chunk = chunk.lower()
            if len(chunk) >= 2:
                tokens.append(chunk)
    return tokens


def simhash64(text: str) -> int:
    tokens = tokenize(text)
    if not tokens:
        return 0
    weights = Counter(tokens)
    vector = [0] * 64
    for token, weight in weights.items():
        digest = hashlib.md5(token.encode("utf-8")).digest()[:8]
        value = int.from_bytes(digest, "big")
        for bit in range(64):
            vector[bit] += weight if (value >> bit) & 1 else -weight
    result = 0
    for bit in range(64):
        if vector[bit] > 0:
            result |= 1 << bit
    return result


def to_hex(value: int) -> str:
    return f"{value:016x}"


def from_hex(value: str | None) -> int:
    return int(value, 16) if value else 0


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()
