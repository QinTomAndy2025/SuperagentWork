"""China-market clock helpers.

Mainland China does not observe daylight saving time, so a fixed UTC+8 timezone
is exact for A-share trading hours and avoids depending on the IANA tz database
on Windows.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

CHINA_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")


def now_china() -> datetime:
    return datetime.now(CHINA_TZ)


def iso_now() -> str:
    return now_china().isoformat(timespec="seconds")


def trading_date_of(when: datetime) -> str:
    return when.astimezone(CHINA_TZ).date().isoformat()
