"""Clock protocol for testable time."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from datetime import datetime


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime:
        """Return the current UTC time."""
        ...

    def sleep(self, seconds: float) -> None:
        """Sleep for the given duration."""
        ...
