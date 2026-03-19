"""Tests for metrics tracking."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from claudette.core.metrics import MetricsStore, _format_duration


class TestMetricsStore:
    def test_record_and_count(self, tmp_path: Path):
        store = MetricsStore(tmp_path)
        store.record("tick")
        store.record("tick")
        store.record("tick")
        s = store.summary()
        assert s["total_ticks"] == 3

    def test_record_with_repo(self, tmp_path: Path):
        store = MetricsStore(tmp_path)
        store.record("pr_opened", repo="owner/repo-a")
        store.record("pr_opened", repo="owner/repo-a")
        store.record("pr_opened", repo="owner/repo-b")
        s = store.summary()
        assert s["total_prs_opened"] == 3
        assert s["prs_opened_by_repo"]["owner/repo-a"] == 2
        assert s["prs_opened_by_repo"]["owner/repo-b"] == 1

    def test_approval_rate(self, tmp_path: Path):
        store = MetricsStore(tmp_path)
        store.record("pr_opened", repo="owner/repo")
        store.record("pr_opened", repo="owner/repo")
        store.record("pr_opened", repo="owner/repo")
        store.record("pr_opened", repo="owner/repo")
        store.record("pr_merged", repo="owner/repo")
        store.record("pr_merged", repo="owner/repo")
        s = store.summary()
        assert s["approval_rate"] == 50.0

    def test_approval_rate_zero_opened(self, tmp_path: Path):
        store = MetricsStore(tmp_path)
        s = store.summary()
        assert s["approval_rate"] == 0.0

    def test_persists_to_disk(self, tmp_path: Path):
        store = MetricsStore(tmp_path)
        store.record("session_launched")
        store.record("error", detail="something broke")

        # Reload from disk
        store2 = MetricsStore(tmp_path)
        s = store2.summary()
        assert s["total_sessions"] == 1
        assert s["total_errors"] == 1

    def test_prs_today(self, tmp_path: Path):
        store = MetricsStore(tmp_path)
        store.record("pr_opened", repo="owner/repo")
        s = store.summary()
        assert s["prs_today"] == 1

    def test_errors_today(self, tmp_path: Path):
        store = MetricsStore(tmp_path)
        store.record("error")
        store.record("error")
        s = store.summary()
        assert s["errors_today"] == 2

    def test_uptime_with_ticks(self, tmp_path: Path):
        store = MetricsStore(tmp_path)
        store.record("tick")
        s = store.summary()
        assert s["uptime"] != "n/a"

    def test_uptime_without_ticks(self, tmp_path: Path):
        store = MetricsStore(tmp_path)
        s = store.summary()
        assert s["uptime"] == "n/a"

    def test_daily_stats_structure(self, tmp_path: Path):
        store = MetricsStore(tmp_path)
        store.record("tick")
        store.record("pr_opened", repo="owner/repo")
        store.record("error")
        daily = store.daily_stats(days=3)
        assert len(daily) == 3
        today = daily[0]
        assert today["ticks"] == 1
        assert today["prs_opened"] == 1
        assert today["errors"] == 1
        assert "date" in today

    def test_daily_stats_past_days_are_zero(self, tmp_path: Path):
        store = MetricsStore(tmp_path)
        store.record("tick")
        daily = store.daily_stats(days=3)
        # Yesterday should have 0 ticks
        assert daily[1]["ticks"] == 0

    def test_extra_kwargs_stored(self, tmp_path: Path):
        store = MetricsStore(tmp_path)
        store.record("pr_merged", repo="owner/repo", pr="owner/repo#5")
        events = store._data["events"]
        assert events[-1]["pr"] == "owner/repo#5"

    def test_escalated_and_stale(self, tmp_path: Path):
        store = MetricsStore(tmp_path)
        store.record("issue_escalated", repo="r")
        store.record("stale_requeued", repo="r")
        store.record("stale_requeued", repo="r")
        s = store.summary()
        assert s["total_escalated"] == 1
        assert s["total_stale_requeued"] == 2

    def test_corrupted_file_handled(self, tmp_path: Path):
        metrics_file = tmp_path / "metrics.json"
        metrics_file.write_text("not valid json{{{")
        store = MetricsStore(tmp_path)
        # Should start fresh
        s = store.summary()
        assert s["total_ticks"] == 0

    def test_summary_last_tick(self, tmp_path: Path):
        store = MetricsStore(tmp_path)
        store.record("tick")
        s = store.summary()
        assert s["last_tick"] != ""

    def test_issues_completed_by_repo(self, tmp_path: Path):
        store = MetricsStore(tmp_path)
        store.record("issue_completed", repo="a/b")
        store.record("issue_completed", repo="a/b")
        store.record("issue_completed", repo="c/d")
        s = store.summary()
        assert s["total_issues_completed"] == 3
        assert s["issues_completed_by_repo"]["a/b"] == 2
        assert s["issues_completed_by_repo"]["c/d"] == 1


class TestFormatDuration:
    def test_seconds(self):
        assert _format_duration(timedelta(seconds=30)) == "30s"

    def test_minutes(self):
        assert _format_duration(timedelta(minutes=5)) == "5m"

    def test_hours(self):
        assert _format_duration(timedelta(hours=2, minutes=15)) == "2h 15m"

    def test_days(self):
        assert _format_duration(timedelta(days=3, hours=4)) == "3d 4h"

    def test_zero(self):
        assert _format_duration(timedelta(seconds=0)) == "0s"

    def test_negative(self):
        assert _format_duration(timedelta(seconds=-10)) == "0s"
