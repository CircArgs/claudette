"""Tests for LiveGitHubClient using httpx MockTransport."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from claudette.core.github_client import (
    LiveGitHubClient,
    _parse_next_link,
    split_repo,
)

# ------------------------------------------------------------------
# split_repo helper
# ------------------------------------------------------------------


class TestSplitRepo:
    def test_valid(self):
        assert split_repo("octocat/hello-world") == ("octocat", "hello-world")

    def test_valid_with_dashes(self):
        assert split_repo("my-org/my-repo") == ("my-org", "my-repo")

    def test_missing_slash(self):
        with pytest.raises(ValueError, match="Invalid repo format"):
            split_repo("noslash")

    def test_empty_owner(self):
        with pytest.raises(ValueError, match="Invalid repo format"):
            split_repo("/repo")

    def test_empty_repo(self):
        with pytest.raises(ValueError, match="Invalid repo format"):
            split_repo("owner/")


# ------------------------------------------------------------------
# Link header parser
# ------------------------------------------------------------------


class TestParseNextLink:
    def test_with_next(self):
        header = (
            '<https://api.github.com/repos/o/r/issues?page=2>; rel="next", '
            '<https://api.github.com/repos/o/r/issues?page=5>; rel="last"'
        )
        assert _parse_next_link(header) == "https://api.github.com/repos/o/r/issues?page=2"

    def test_without_next(self):
        header = '<https://api.github.com/repos/o/r/issues?page=5>; rel="last"'
        assert _parse_next_link(header) is None

    def test_none(self):
        assert _parse_next_link(None) is None


# ------------------------------------------------------------------
# Helpers to build mock transport
# ------------------------------------------------------------------


def _json_response(data, status_code=200, headers=None):
    return httpx.Response(
        status_code,
        content=json.dumps(data).encode(),
        headers={**(headers or {}), "content-type": "application/json"},
    )


def _make_client(handler) -> LiveGitHubClient:
    """Create a LiveGitHubClient with a mock transport."""
    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(
        transport=transport,
        base_url="https://api.github.com",
        headers={
            "Authorization": "Bearer fake-token",
            "Accept": "application/vnd.github+json",
        },
    )
    return LiveGitHubClient(token="fake-token", client=http_client)


# ------------------------------------------------------------------
# fetch_issues
# ------------------------------------------------------------------


class TestFetchIssues:
    def test_url_and_params(self):
        captured = {}

        def handler(request: httpx.Request):
            captured["url"] = str(request.url)
            captured["method"] = request.method
            return _json_response(
                [
                    {
                        "number": 1,
                        "title": "Bug",
                        "body": "desc",
                        "state": "open",
                        "labels": [{"name": "bug"}],
                        "created_at": "2025-01-01T00:00:00Z",
                        "updated_at": "2025-01-02T00:00:00Z",
                    },
                ]
            )

        client = _make_client(handler)
        since = datetime(2025, 1, 1, tzinfo=UTC)
        issues = client.fetch_issues("octocat/hello", since)

        assert captured["method"] == "GET"
        assert "/repos/octocat/hello/issues" in captured["url"]
        assert (
            "since=2025-01-01T00%3A00%3A00Z" in captured["url"]
            or "since=2025-01-01T00:00:00Z" in captured["url"]
        )
        assert "state=all" in captured["url"]
        assert len(issues) == 1
        assert issues[0].number == 1
        assert issues[0].title == "Bug"
        assert issues[0].labels == ["bug"]
        assert issues[0].repo == "octocat/hello"

    def test_pagination(self):
        call_count = 0

        def handler(request: httpx.Request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _json_response(
                    [{"number": 1, "title": "A", "body": "", "state": "open", "labels": []}],
                    headers={
                        "Link": '<https://api.github.com/repos/o/r/issues?page=2>; rel="next"'
                    },
                )
            return _json_response(
                [{"number": 2, "title": "B", "body": "", "state": "closed", "labels": []}],
            )

        client = _make_client(handler)
        issues = client.fetch_issues("o/r", datetime(2025, 1, 1, tzinfo=UTC))
        assert len(issues) == 2
        assert call_count == 2


# ------------------------------------------------------------------
# get_issue
# ------------------------------------------------------------------


class TestGetIssue:
    def test_url(self):
        captured = {}

        def handler(request: httpx.Request):
            captured.setdefault("urls", []).append(str(request.url))
            if "/comments" in str(request.url):
                return _json_response(
                    [
                        {
                            "body": "hi",
                            "user": {"login": "alice"},
                            "created_at": "2025-01-01T00:00:00Z",
                        }
                    ]
                )
            return _json_response(
                {
                    "number": 42,
                    "title": "Title",
                    "body": "Body",
                    "state": "open",
                    "labels": [],
                    "created_at": "2025-01-01T00:00:00Z",
                    "updated_at": "2025-01-01T00:00:00Z",
                }
            )

        client = _make_client(handler)
        issue = client.get_issue("octocat/repo", 42)

        assert any("/repos/octocat/repo/issues/42" in u for u in captured["urls"])
        assert any("/repos/octocat/repo/issues/42/comments" in u for u in captured["urls"])
        assert issue.number == 42
        assert len(issue.comments) == 1
        assert issue.comments[0].author == "alice"


# ------------------------------------------------------------------
# post_comment
# ------------------------------------------------------------------


class TestPostComment:
    def test_url_and_body(self):
        captured = {}

        def handler(request: httpx.Request):
            captured["url"] = str(request.url)
            captured["method"] = request.method
            captured["body"] = json.loads(request.content)
            return _json_response({"id": 1})

        client = _make_client(handler)
        client.post_comment("octocat/repo", 7, "Hello!")

        assert captured["method"] == "POST"
        assert "/repos/octocat/repo/issues/7/comments" in captured["url"]
        assert captured["body"] == {"body": "Hello!"}


# ------------------------------------------------------------------
# apply_label
# ------------------------------------------------------------------


class TestApplyLabel:
    def test_url_and_body(self):
        captured = {}

        def handler(request: httpx.Request):
            captured["url"] = str(request.url)
            captured["method"] = request.method
            captured["body"] = json.loads(request.content)
            return _json_response([{"name": "bug"}])

        client = _make_client(handler)
        client.apply_label("octocat/repo", 3, "bug")

        assert captured["method"] == "POST"
        assert "/repos/octocat/repo/issues/3/labels" in captured["url"]
        assert captured["body"] == {"labels": ["bug"]}


# ------------------------------------------------------------------
# remove_label
# ------------------------------------------------------------------


class TestRemoveLabel:
    def test_url(self):
        captured = {}

        def handler(request: httpx.Request):
            captured["url"] = str(request.url)
            captured["method"] = request.method
            return _json_response([])

        client = _make_client(handler)
        client.remove_label("octocat/repo", 3, "bug")

        assert captured["method"] == "DELETE"
        assert "/repos/octocat/repo/issues/3/labels/bug" in captured["url"]


# ------------------------------------------------------------------
# get_labels
# ------------------------------------------------------------------


class TestGetLabels:
    def test_url_and_result(self):
        captured = {}

        def handler(request: httpx.Request):
            captured["url"] = str(request.url)
            return _json_response([{"name": "bug"}, {"name": "help wanted"}])

        client = _make_client(handler)
        labels = client.get_labels("octocat/repo", 5)

        assert "/repos/octocat/repo/issues/5/labels" in captured["url"]
        assert labels == ["bug", "help wanted"]


# ------------------------------------------------------------------
# ensure_label_exists
# ------------------------------------------------------------------


class TestEnsureLabelExists:
    def test_url_and_body(self):
        captured = {}

        def handler(request: httpx.Request):
            captured["url"] = str(request.url)
            captured["method"] = request.method
            captured["body"] = json.loads(request.content)
            return _json_response({"name": "triage"}, status_code=201)

        client = _make_client(handler)
        client.ensure_label_exists("octocat/repo", "triage")

        assert captured["method"] == "POST"
        assert "/repos/octocat/repo/labels" in captured["url"]
        assert captured["body"] == {"name": "triage"}

    def test_already_exists_422_ignored(self):
        def handler(request: httpx.Request):
            return _json_response({"message": "already exists"}, status_code=422)

        client = _make_client(handler)
        # Should not raise
        client.ensure_label_exists("octocat/repo", "triage")


# ------------------------------------------------------------------
# has_label
# ------------------------------------------------------------------


class TestHasLabel:
    def test_url_and_found(self):
        captured = {}

        def handler(request: httpx.Request):
            captured["url"] = str(request.url)
            return _json_response([{"number": 1, "title": "x"}])

        client = _make_client(handler)
        result = client.has_label("octocat/repo", "in-progress")

        assert "/repos/octocat/repo/issues" in captured["url"]
        assert "labels=in-progress" in captured["url"]
        assert "state=open" in captured["url"]
        assert result is True

    def test_not_found(self):
        def handler(request: httpx.Request):
            return _json_response([])

        client = _make_client(handler)
        assert client.has_label("octocat/repo", "in-progress") is False


# ------------------------------------------------------------------
# HTTP error handling
# ------------------------------------------------------------------


class TestErrorHandling:
    def test_non_429_error_raises(self):
        def handler(request: httpx.Request):
            return _json_response({"message": "Not Found"}, status_code=404)

        client = _make_client(handler)
        with pytest.raises(httpx.HTTPStatusError):
            client.get_labels("octocat/repo", 1)

    def test_pull_request_detection(self):
        def handler(request: httpx.Request):
            url = str(request.url)
            if "/comments" in url or "/reviews" in url:
                return _json_response([])
            return _json_response(
                {
                    "number": 10,
                    "title": "PR",
                    "body": "",
                    "state": "open",
                    "labels": [],
                    "pull_request": {"url": "..."},
                    "created_at": "2025-01-01T00:00:00Z",
                    "updated_at": "2025-01-01T00:00:00Z",
                }
            )

        client = _make_client(handler)
        issue = client.get_issue("o/r", 10)
        assert issue.is_pull_request is True
