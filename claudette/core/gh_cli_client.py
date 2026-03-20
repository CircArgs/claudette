"""GitHubClient implementation backed by the `gh` CLI.

Uses `gh api` for all HTTP calls, completely bypassing Python SSL/TLS.
This is the preferred client for corporate environments where httpx
hits certificate issues but `gh` works fine.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime

from claudette.protocols.github import Comment, Issue, Review


def _gh_api(
    endpoint: str,
    method: str = "GET",
    params: dict | None = None,
    body: dict | None = None,
    paginate: bool = False,
) -> list | dict:
    """Call `gh api` and return parsed JSON."""
    cmd = ["gh", "api"]

    if method != "GET":
        cmd.extend(["--method", method])

    if paginate:
        cmd.append("--paginate")

    # Query params go in the URL (NOT -f, which sends body fields)
    url = endpoint
    if params:
        from urllib.parse import urlencode

        url = f"{endpoint}?{urlencode(params)}"

    # Add JSON body for POST/PATCH/PUT
    if body:
        cmd.extend(["--input", "-"])

    cmd.append(url)

    input_data = json.dumps(body) if body else None
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=60,
        input=input_data,
    )

    if result.returncode != 0:
        raise RuntimeError(f"gh api error: {result.stderr.strip()}")

    if not result.stdout.strip():
        return {}

    # gh --paginate can return concatenated JSON arrays
    text = result.stdout.strip()
    if paginate and text.startswith("[") and "][" in text:
        # Merge multiple JSON arrays: [a,b][c,d] -> [a,b,c,d]
        text = text.replace("][", ",")

    return json.loads(text)


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _parse_issue(repo: str, data: dict) -> Issue:
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
    return Comment(
        body=data.get("body") or "",
        author=data.get("user", {}).get("login", ""),
        created_at=_parse_datetime(data.get("created_at")),
    )


class GhCliGitHubClient:
    """GitHubClient backed by `gh api` — no Python SSL involved."""

    def fetch_issues(self, repo: str, since: datetime) -> list[Issue]:
        timestamp = since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        endpoint = f"/repos/{repo}/issues"
        items = _gh_api(
            endpoint,
            params={"since": timestamp, "state": "all"},
            paginate=True,
        )
        return [_parse_issue(repo, item) for item in items]

    def get_issue(self, repo: str, number: int) -> Issue:
        endpoint = f"/repos/{repo}/issues/{number}"
        data = _gh_api(endpoint)
        issue = _parse_issue(repo, data)

        # Fetch issue/PR comments
        comments_data = _gh_api(f"/repos/{repo}/issues/{number}/comments", paginate=True)
        issue.comments = [_parse_comment(c) for c in comments_data]

        # For PRs, also fetch reviews and inline review comments
        if issue.is_pull_request:
            issue.reviews = self._fetch_pr_reviews(repo, number)

        return issue

    def _fetch_pr_reviews(self, repo: str, number: int) -> list[Review]:
        review_items = _gh_api(f"/repos/{repo}/pulls/{number}/reviews", paginate=True)
        inline_items = _gh_api(f"/repos/{repo}/pulls/{number}/comments", paginate=True)

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
        _gh_api(
            f"/repos/{repo}/issues/{number}/comments",
            method="POST",
            body={"body": body},
        )

    def apply_label(self, repo: str, number: int, label: str) -> None:
        _gh_api(
            f"/repos/{repo}/issues/{number}/labels",
            method="POST",
            body={"labels": [label]},
        )

    def remove_label(self, repo: str, number: int, label: str) -> None:
        _gh_api(f"/repos/{repo}/issues/{number}/labels/{label}", method="DELETE")

    def get_labels(self, repo: str, number: int) -> list[str]:
        items = _gh_api(f"/repos/{repo}/issues/{number}/labels", paginate=True)
        return [lbl["name"] for lbl in items]

    def ensure_label_exists(self, repo: str, label: str) -> None:
        try:
            _gh_api(f"/repos/{repo}/labels", method="POST", body={"name": label})
        except RuntimeError as e:
            if "422" in str(e) or "already_exists" in str(e):
                return  # Already exists
            raise

    def has_label(self, repo: str, label: str) -> bool:
        items = _gh_api(
            f"/repos/{repo}/issues",
            params={"labels": label, "state": "open"},
        )
        return len(items) > 0

    def create_issue(
        self,
        repo: str,
        title: str,
        body: str = "",
        labels: list[str] | None = None,
    ) -> Issue:
        payload: dict = {"title": title, "body": body}
        if labels:
            payload["labels"] = labels
        data = _gh_api(f"/repos/{repo}/issues", method="POST", body=payload)
        return _parse_issue(repo, data)

    def update_issue_body(self, repo: str, number: int, body: str) -> None:
        _gh_api(
            f"/repos/{repo}/issues/{number}",
            method="PATCH",
            body={"body": body},
        )

    def check_ci_status(self, repo: str, number: int) -> bool:
        # Get the head SHA from the PR
        pr_data = _gh_api(f"/repos/{repo}/pulls/{number}")
        head_sha = pr_data.get("head", {}).get("sha", "")
        if not head_sha:
            return False
        # Check combined commit status
        status_data = _gh_api(f"/repos/{repo}/commits/{head_sha}/status")
        state = status_data.get("state", "pending")
        if state == "failure":
            return False
        # Also check check-runs (GitHub Actions)
        checks_data = _gh_api(f"/repos/{repo}/commits/{head_sha}/check-runs")
        runs = checks_data.get("check_runs", [])
        for run in runs:
            conclusion = run.get("conclusion")
            if conclusion and conclusion not in ("success", "skipped", "neutral"):
                return False
            if run.get("status") != "completed":
                return False
        return True

    def merge_pr(self, repo: str, number: int, method: str = "squash") -> bool:
        try:
            _gh_api(
                f"/repos/{repo}/pulls/{number}/merge",
                method="PUT",
                body={"merge_method": method},
            )
            return True
        except Exception:
            return False
