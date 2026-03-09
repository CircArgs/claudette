"""Tests for the dependency graph builder."""

import pytest

from claudette.core.dag import (
    build_dag,
    find_cycles,
    get_blocked_issues,
    get_ready_issues,
    topological_sort,
)
from claudette.protocols.github import Issue

PATTERN = r"Depends on\s+(?:([\w-]+/[\w-]+))?#(\d+)"


def _issue(repo: str, number: int, body: str = "", state: str = "open") -> Issue:
    return Issue(repo=repo, number=number, title=f"Issue {number}", body=body, state=state)


class TestBuildDag:
    def test_no_deps(self):
        issues = [_issue("owner/repo", 1), _issue("owner/repo", 2)]
        graph = build_dag(issues, PATTERN)
        assert len(graph.nodes) == 2
        assert graph.edges["owner/repo#1"] == set()
        assert graph.edges["owner/repo#2"] == set()

    def test_intra_repo_dep(self):
        issues = [
            _issue("owner/repo", 1),
            _issue("owner/repo", 2, body="Depends on #1"),
        ]
        graph = build_dag(issues, PATTERN)
        assert graph.edges["owner/repo#2"] == {"owner/repo#1"}

    def test_cross_repo_dep(self):
        issues = [
            _issue("owner/backend", 8),
            _issue("owner/frontend", 20, body="Depends on owner/backend#8"),
        ]
        graph = build_dag(issues, PATTERN)
        assert graph.edges["owner/frontend#20"] == {"owner/backend#8"}

    def test_multiple_deps(self):
        issues = [
            _issue("owner/repo", 3, body="Depends on #1\nDepends on #2"),
        ]
        graph = build_dag(issues, PATTERN)
        assert graph.edges["owner/repo#3"] == {"owner/repo#1", "owner/repo#2"}


class TestCycleDetection:
    def test_no_cycles(self):
        issues = [
            _issue("owner/repo", 1),
            _issue("owner/repo", 2, body="Depends on #1"),
        ]
        graph = build_dag(issues, PATTERN)
        assert find_cycles(graph) == []

    def test_simple_cycle(self):
        issues = [
            _issue("owner/repo", 1, body="Depends on #2"),
            _issue("owner/repo", 2, body="Depends on #1"),
        ]
        graph = build_dag(issues, PATTERN)
        cycles = find_cycles(graph)
        assert len(cycles) >= 1

    def test_topological_sort_raises_on_cycle(self):
        issues = [
            _issue("owner/repo", 1, body="Depends on #2"),
            _issue("owner/repo", 2, body="Depends on #1"),
        ]
        graph = build_dag(issues, PATTERN)
        with pytest.raises(ValueError, match="Circular"):
            topological_sort(graph)


class TestBlockedAndReady:
    def test_blocked_by_open_dep(self):
        issues = [
            _issue("owner/repo", 1, state="open"),
            _issue("owner/repo", 2, body="Depends on #1", state="open"),
        ]
        graph = build_dag(issues, PATTERN)
        blocked = get_blocked_issues(graph)
        assert "owner/repo#2" in blocked

    def test_unblocked_when_dep_closed(self):
        issues = [
            _issue("owner/repo", 1, state="closed"),
            _issue("owner/repo", 2, body="Depends on #1", state="open"),
        ]
        graph = build_dag(issues, PATTERN)
        blocked = get_blocked_issues(graph)
        assert "owner/repo#2" not in blocked

    def test_get_ready_issues(self):
        issues = [
            _issue("owner/repo", 1, state="open"),
            _issue("owner/repo", 2, body="Depends on #1", state="open"),
            _issue("owner/repo", 3, state="open"),
        ]
        graph = build_dag(issues, PATTERN)
        ready = get_ready_issues(graph)
        assert "owner/repo#1" in ready
        assert "owner/repo#3" in ready
        assert "owner/repo#2" not in ready

    def test_cross_repo_blocking(self):
        issues = [
            _issue("owner/backend", 8, state="open"),
            _issue("owner/frontend", 20, body="Depends on owner/backend#8", state="open"),
        ]
        graph = build_dag(issues, PATTERN)
        blocked = get_blocked_issues(graph)
        assert "owner/frontend#20" in blocked
        assert "owner/backend#8" in blocked["owner/frontend#20"]
