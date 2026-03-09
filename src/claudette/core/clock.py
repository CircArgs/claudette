"""Production clock implementation."""

from __future__ import annotations

import time
from datetime import UTC, datetime


class SystemClock:
    """Real wall-clock time."""

    def now(self) -> datetime:
        return datetime.now(UTC)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)
