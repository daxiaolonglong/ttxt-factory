"""发布时间归一化:ISO / RFC 2822 / 中文日期 / 时间戳 → 带时区 ISO 字符串。"""

from __future__ import annotations

import re
from datetime import datetime
from email.utils import parsedate_to_datetime


def _to_local_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return dt.astimezone().isoformat(timespec="seconds")


def normalize_datetime(value: str | None) -> str | None:
    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None

    if re.fullmatch(r"\d{10}", raw):
        return _to_local_iso(datetime.fromtimestamp(int(raw)))
    if re.fullmatch(r"\d{13}", raw):
        return _to_local_iso(datetime.fromtimestamp(int(raw) / 1000))

    try:
        return _to_local_iso(parsedate_to_datetime(raw))
    except Exception:  # noqa: BLE001 - 继续尝试其他格式
        pass

    try:
        return _to_local_iso(datetime.fromisoformat(raw.replace("Z", "+00:00")))
    except Exception:  # noqa: BLE001
        pass

    chinese = re.fullmatch(r"(\d{4})年(\d{1,2})月(\d{1,2})日(?:\s*(\d{1,2}):(\d{2}))?", raw)
    if chinese:
        year, month, day, hour, minute = chinese.groups()
        return _to_local_iso(
            datetime(int(year), int(month), int(day), int(hour or 0), int(minute or 0))
        )

    compact = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?", raw)
    if compact:
        year, month, day, hour, minute, second = compact.groups()
        return _to_local_iso(
            datetime(
                int(year),
                int(month),
                int(day),
                int(hour or 0),
                int(minute or 0),
                int(second or 0),
            )
        )
    # 缺年份的日期(如「11月12日」)拒绝猜测,返回 None 以保证数据真实性
    return None
