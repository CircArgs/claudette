"""Production implementation of the GitHubClient protocol using httpx."""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

from claudette.protocols.github import Comment, Issue, Review

BASE_URL = "https://api.github.com"


def split_repo(repo: str) -> tuple[str, str]:
    """Split an 'owner/repo' string into (owner, repo)."""
    parts = repo.split("/", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(f"Invalid repo format {repo!r}, expected 'owner/repo'")
    return parts[0], parts[1]


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _parse_issue(repo: str, data: dict) -> Issue:
    """Parse a GitHub API issue JSON dict into an Issue dataclass."""
    return Issue(
        repo=repo,
        number=data["number"],
        title=data["title"],
        body=data.get("body") or "",
        state=data["state"],
        labels=[lbl["name"] for lbl in data.get("labels", [])],
        comments=[],
        is_pull_request="pull_request" in data,
        author=data.get("user", {}).get("login", ""),
        created_at=_parse_datetime(data.get("created_at")),
        updated_at=_parse_datetime(data.get("updated_at")),
    )


def _parse_comment(data: dict) -> Comment:
    """Parse a GitHub API comment JSON dict into a Comment dataclass."""
    return Comment(
        body=data.get("body") or "",
        author=data.get("user", {}).get("login", ""),
        created_at=_parse_datetime(data.get("created_at")),
    )


def _ssl_context() -> bool | str:
    """Return SSL verification setting, respecting corporate CA bundles."""
    for env_var in ("REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "SSL_CERT_FILE"):
        ca_path = os.environ.get(env_var)
        if ca_path and Path(ca_path).exists():
            return ca_path
    return True


class LiveGitHubClient:
    """Production GitHubClient backed by httpx."""

    def __init__(self, token: str, *, client: httpx.Client | None = None) -> None:
        self._token = token
        self._client = client or httpx.Client(
            base_url=BASE_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30.0,
            verify=_ssl_context(),
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """Issue an HTTP request with rate-limit retry on 429."""
        resp = self._client.request(method, url, **kwargs)
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", "5"))
            time.sleep(retry_after)
            resp = self._client.request(method, url, **kwargs)
        if resp.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"GitHub API error {resp.status_code}: {resp.text}",
                request=resp.request,
                response=resp,
            )
        return resp

    def _get_paginated(self, url: str, params: dict | None = None) -> list[dict]:
        """GET with pagination via Link headers. Returns all items."""
        results: list[dict] = []
        next_url: str | None = url
        current_params = params

        while next_url is not None:
            resp = self._request("GET", next_url, params=current_params)
            results.extend(resp.json())
            # After the first request, params are baked into the Link URL
            current_params = None
            next_url = _parse_next_link(resp.headers.get("Link"))
        return results

    # ------------------------------------------------------------------
    # Protocol methods
    # ------------------------------------------------------------------

    def fetch_issues(self, repo: str, since: datetime) -> list[Issue]:
        owner, name = split_repo(repo)
        timestamp = since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        url = f"/repos/{owner}/{name}/issues"
        params = {"since": timestamp, "state": "all"}
        items = self._get_paginated(url, params=params)
        return [_parse_issue(repo, item) for item in items]

    def get_issue(self, repo: str, number: int) -> Issue:
        owner, name = split_repo(repo)
        url = f"/repos/{owner}/{name}/issues/{number}"
        resp = self._request("GET", url)
        issue = _parse_issue(repo, resp.json())

        # Fetch issue/PR comments (conversation thread)
        comments_url = f"/repos/{owner}/{name}/issues/{number}/comments"
        comment_items = self._get_paginated(comments_url)
        issue.comments = [_parse_comment(c) for c in comment_items]

        # For PRs, also fetch reviews and inline review comments
        if issue.is_pull_request:
            issue.reviews = self._fetch_pr_reviews(owner, name, number)

        return issue

    def _fetch_pr_reviews(self, owner: str, name: str, number: int) -> list[Review]:
        """Fetch PR reviews with their inline comments."""
        reviews_url = f"/repos/{owner}/{name}/pulls/{number}/reviews"
        review_items = self._get_paginated(reviews_url)

        # Fetch all inline review comments for this PR
        inline_url = f"/repos/{owner}/{name}/pulls/{number}/comments"
        inline_items = self._get_paginated(inline_url)

        # Group inline comments by review ID
        comments_by_review: dict[int, list[Comment]] = {}
        for item in inline_items:
            review_id = item.get("pull_request_review_id")
            if review_id is not None:
                comments_by_review.setdefault(review_id, []).append(
                    Comment(
                        body=item.get("body") or "",
                        author=item.get("user", {}).get("login", ""),
                        created_at=_parse_datetime(item.get("created_at")),
                        path=item.get("path"),
                    )
                )

        reviews = []
        for item in review_items:
            review_id = item.get("id", 0)
            reviews.append(
                Review(
                    author=item.get("user", {}).get("login", ""),
                    state=item.get("state", ""),
                    body=item.get("body") or "",
                    comments=comments_by_review.get(review_id, []),
                    submitted_at=_parse_datetime(item.get("submitted_at")),
                )
            )

        return reviews

    def post_comment(self, repo: str, number: int, body: str) -> None:
        owner, name = split_repo(repo)
        url = f"/repos/{owner}/{name}/issues/{number}/comments"
        self._request("POST", url, json={"body": body})

    def apply_label(self, repo: str, number: int, label: str) -> None:
        owner, name = split_repo(repo)
        url = f"/repos/{owner}/{name}/issues/{number}/labels"
        self._request("POST", url, json={"labels": [label]})

    def remove_label(self, repo: str, number: int, label: str) -> None:
        owner, name = split_repo(repo)
        url = f"/repos/{owner}/{name}/issues/{number}/labels/{label}"
        self._request("DELETE", url)

    def get_labels(self, repo: str, number: int) -> list[str]:
        owner, name = split_repo(repo)
        url = f"/repos/{owner}/{name}/issues/{number}/labels"
        items = self._get_paginated(url)
        return [lbl["name"] for lbl in items]

    def ensure_label_exists(self, repo: str, label: str) -> None:
        owner, name = split_repo(repo)
        url = f"/repos/{owner}/{name}/labels"
        resp = self._client.post(url, json={"name": label})
        if resp.status_code == 422:
            # Already exists -- ignore
            return
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", "5"))
            time.sleep(retry_after)
            resp = self._client.post(url, json={"name": label})
            if resp.status_code == 422:
                return
        if resp.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"GitHub API error {resp.status_code}: {resp.text}",
                request=resp.request,
                response=resp,
            )

    def has_label(self, repo: str, label: str) -> bool:
        owner, name = split_repo(repo)
        url = f"/repos/{owner}/{name}/issues"
        params = {"labels": label, "state": "open"}
        resp = self._request("GET", url, params=params)
        return len(resp.json()) > 0

    def create_issue(
        self,
        repo: str,
        title: str,
        body: str = "",
        labels: list[str] | None = None,
    ) -> Issue:
        owner, name = split_repo(repo)
        url = f"/repos/{owner}/{name}/issues"
        payload: dict = {"title": title, "body": body}
        if labels:
            payload["labels"] = labels
        resp = self._request("POST", url, json=payload)
        return _parse_issue(repo, resp.json())

    def update_issue_body(self, repo: str, number: int, body: str) -> None:
        owner, name = split_repo(repo)
        url = f"/repos/{owner}/{name}/issues/{number}"
        self._request("PATCH", url, json={"body": body})


def _parse_next_link(link_header: str | None) -> str | None:
    """Extract the 'next' URL from a GitHub Link header."""
    if not link_header:
        return None
    for part in link_header.split(","):
        if 'rel="next"' in part:
            url = part.split(";")[0].strip().strip("<>")
            return url
    return None
