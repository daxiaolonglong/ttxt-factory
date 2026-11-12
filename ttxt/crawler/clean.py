"""URL 规范化、文本清洗、内容哈希与摘要。"""

from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "spm", "from", "ref", "share_token",
}


def normalize_text(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\u3000", " ").replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def excerpt(text: str, length: int = 160) -> str:
    clean = normalize_text(text).replace("\n", " ")
    return clean if len(clean) <= length else clean[:length].rstrip() + "…"


def normalize_url(url: str, base: str | None = None) -> str:
    if base:
        url = urljoin(base, url)
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower() or "https"
    host = parts.hostname.lower() if parts.hostname else ""
    port = parts.port
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{host}:{port}"
    else:
        netloc = host
    path = parts.path or "/"
    path = re.sub(r"/{2,}", "/", path)
    # 保留尾斜杠:部分站点对 /page/2 与 /page/2/ 做不同处理,去除会触发多余重定向
    query_pairs = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in TRACKING_PARAMS
    ]
    query = urlencode(sorted(query_pairs))
    return urlunsplit((scheme, netloc, path, query, ""))


def content_hash(title: str, content: str) -> str:
    payload = f"{normalize_text(title)}\n{normalize_text(content)}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
