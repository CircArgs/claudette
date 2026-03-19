"""Metrics tracking for claudette operations."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


class MetricsStore:
    """Persists event metrics to <state_dir>/metrics.json."""

    def __init__(self, state_dir: Path) -> None:
        self._state_dir = state_dir
        self._file = state_dir / "metrics.json"
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        if self._file.exists():
            try:
                return json.loads(self._file.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        return {
            "events": [],
            "counters": {},
        }

    def _save(self) -> None:
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._file.write_text(json.dumps(self._data, indent=2))

    def record(self, event: str, repo: str = "", **extra: Any) -> None:
        """Record a metrics event with a timestamp.

        Parameters
        ----------
        event:
            One of: session_launched, pr_opened, pr_merged, pr_approved,
            pr_rejected, issue_completed, issue_escalated, stale_requeued,
            error, tick.
        repo:
            Repository name (owner/repo) when applicable.
        **extra:
            Arbitrary extra data to store with the event.
        """
        entry: dict[str, Any] = {
            "event": event,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        if repo:
            entry["repo"] = repo
        if extra:
            entry.update(extra)

        self._data["events"].append(entry)

        # Update counters
        counters = self._data["counters"]
        counters[event] = counters.get(event, 0) + 1
        if repo:
            repo_key = f"{event}:{repo}"
            counters[repo_key] = counters.get(repo_key, 0) + 1

        self._save()

    def _events_since(self, since: datetime) -> list[dict[str, Any]]:
        """Return events with timestamps after *since*."""
        result = []
        for ev in self._data.get("events", []):
            try:
                ts = datetime.fromisoformat(ev["timestamp"])
                if ts >= since:
                    result.append(ev)
            except (KeyError, ValueError):
                continue
        return result

    def _count_event(self, event: str) -> int:
        return self._data.get("counters", {}).get(event, 0)

    def _count_event_since(self, event: str, since: datetime) -> int:
        return sum(1 for ev in self._events_since(since) if ev.get("event") == event)

    def _first_tick_time(self) -> datetime | None:
        for ev in self._data.get("events", []):
            if ev.get("event") == "tick":
                try:
                    return datetime.fromisoformat(ev["timestamp"])
                except (KeyError, ValueError):
                    continue
        return None

    def _last_timestamp(self, event: str) -> str:
        for ev in reversed(self._data.get("events", [])):
            if ev.get("event") == event:
                return ev.get("timestamp", "")
        return ""

    def _per_repo_counts(self, event: str) -> dict[str, int]:
        """Get per-repo breakdown for an event type."""
        counts: dict[str, int] = {}
        prefix = f"{event}:"
        for key, val in self._data.get("counters", {}).items():
            if key.startswith(prefix):
                repo = key[len(prefix):]
                counts[repo] = val
        return counts

    def summary(self) -> dict[str, Any]:
        """Return a summary dict of all tracked metrics."""
        now = datetime.now(UTC)
        day_ago = now - timedelta(hours=24)

        total_opened = self._count_event("pr_opened")
        total_merged = self._count_event("pr_merged")
        approval_rate = (total_merged / total_opened * 100) if total_opened > 0 else 0.0

        first_tick = self._first_tick_time()
        if first_tick:
            uptime = now - first_tick
            uptime_str = _format_duration(uptime)
        else:
            uptime_str = "n/a"

        return {
            "total_prs_opened": total_opened,
            "total_prs_merged": total_merged,
            "approval_rate": round(approval_rate, 1),
            "total_issues_completed": self._count_event("issue_completed"),
            "total_sessions": self._count_event("session_launched"),
            "total_ticks": self._count_event("tick"),
            "uptime": uptime_str,
            "prs_today": self._count_event_since("pr_opened", day_ago),
            "errors_today": self._count_event_since("error", day_ago),
            "total_errors": self._count_event("error"),
            "total_escalated": self._count_event("issue_escalated"),
            "total_stale_requeued": self._count_event("stale_requeued"),
            "total_pr_approved": self._count_event("pr_approved"),
            "total_pr_rejected": self._count_event("pr_rejected"),
            "last_tick": self._last_timestamp("tick"),
            "prs_opened_by_repo": self._per_repo_counts("pr_opened"),
            "prs_merged_by_repo": self._per_repo_counts("pr_merged"),
            "issues_completed_by_repo": self._per_repo_counts("issue_completed"),
        }

    def daily_stats(self, days: int = 7) -> list[dict[str, Any]]:
        """Return per-day breakdown for the last *days* days."""
        now = datetime.now(UTC)
        result = []
        for i in range(days):
            day_start = (now - timedelta(days=i)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            day_end = day_start + timedelta(days=1)

            day_events = [
                ev
                for ev in self._data.get("events", [])
                if _ts_in_range(ev.get("timestamp", ""), day_start, day_end)
            ]

            def _count(event: str, _events: list[dict[str, Any]] = day_events) -> int:
                return sum(1 for ev in _events if ev.get("event") == event)

            result.append(
                {
                    "date": day_start.strftime("%Y-%m-%d"),
                    "ticks": _count("tick"),
                    "sessions": _count("session_launched"),
                    "prs_opened": _count("pr_opened"),
                    "prs_merged": _count("pr_merged"),
                    "issues_completed": _count("issue_completed"),
                    "errors": _count("error"),
                    "escalated": _count("issue_escalated"),
                    "stale_requeued": _count("stale_requeued"),
                }
            )
        return result


def _ts_in_range(ts_str: str, start: datetime, end: datetime) -> bool:
    """Check whether an ISO timestamp string falls within [start, end)."""
    try:
        ts = datetime.fromisoformat(ts_str)
        return start <= ts < end
    except (ValueError, TypeError):
        return False


def _format_duration(td: timedelta) -> str:
    """Format a timedelta as a human-readable string."""
    total_seconds = int(td.total_seconds())
    if total_seconds < 0:
        return "0s"
    days = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600
    minutes = (total_seconds % 3600) // 60
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if not parts:
        return f"{total_seconds}s"
    return " ".join(parts)
