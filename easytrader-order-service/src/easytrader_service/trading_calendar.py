"""Trading-session checks for the Shanghai/Shenzhen A-share market."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time

from .config import TradingConfig
from .timeutil import CHINA_TZ


@dataclass(frozen=True)
class TradingSession:
    start: time
    end: time


class TradingCalendar:
    """Weekday/session calendar with a configurable holiday list.

    Chinese exchange holidays change every year and are not built in; operators
    should keep ``trading.holidays`` up to date.
    """

    def __init__(self, config: TradingConfig):
        self._weekdays = set(config.weekdays)
        self._holidays = {date.fromisoformat(value) for value in config.holidays}
        self._sessions = [
            TradingSession(time.fromisoformat(start), time.fromisoformat(end))
            for start, end in config.sessions
        ]

    def is_open(self, when: datetime | None = None) -> tuple[bool, str]:
        local = (when or datetime.now(CHINA_TZ)).astimezone(CHINA_TZ)
        if local.isoweekday() not in self._weekdays:
            return False, "当前不是交易日（周末或非交易日）"
        if local.date() in self._holidays:
            return False, "当前是配置的节假日，休市"
        current = local.time()
        for session in self._sessions:
            if session.start <= current <= session.end:
                return True, ""
        return False, "当前不在交易时段（午休或已收盘）"

    @staticmethod
    def trading_date(when: datetime | None = None) -> str:
        return (when or datetime.now(CHINA_TZ)).astimezone(CHINA_TZ).date().isoformat()
