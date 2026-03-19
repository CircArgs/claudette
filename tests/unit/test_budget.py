"""Tests for budget tracking."""

from pathlib import Path

from claudette.core.budget import BudgetTracker


class TestBudgetTracker:
    def test_record_and_read(self, tmp_path: Path):
        tracker = BudgetTracker(tmp_path)
        tracker.record("owner/repo", 42, 1000)
        assert tracker.total_today("owner/repo") == 1000
        assert tracker.issue_total("owner/repo", 42) == 1000

    def test_accumulates(self, tmp_path: Path):
        tracker = BudgetTracker(tmp_path)
        tracker.record("owner/repo", 1, 500)
        tracker.record("owner/repo", 2, 300)
        tracker.record("owner/repo", 1, 200)
        assert tracker.total_today("owner/repo") == 1000
        assert tracker.issue_total("owner/repo", 1) == 700
        assert tracker.issue_total("owner/repo", 2) == 300

    def test_is_exceeded(self, tmp_path: Path):
        tracker = BudgetTracker(tmp_path)
        tracker.record("owner/repo", 1, 5_000_000)
        assert tracker.is_exceeded("owner/repo", 5_000_000)
        assert not tracker.is_exceeded("owner/repo", 10_000_000)

    def test_fresh_repo(self, tmp_path: Path):
        tracker = BudgetTracker(tmp_path)
        assert tracker.total_today("owner/new") == 0
        assert not tracker.is_exceeded("owner/new", 1_000_000)
