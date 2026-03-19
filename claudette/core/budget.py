"""Token budget tracking per issue and per repo."""

from __future__ import annotations

import json
from datetime import date
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path


class BudgetTracker:
    """Track token usage per repo per day, persisted to JSON files."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, repo: str) -> Path:
        safe_name = repo.replace("/", "_")
        return self.state_dir / f"budget_{safe_name}.json"

    def _load(self, repo: str) -> dict[str, Any]:
        path = self._path(repo)
        if not path.exists():
            return {"date": str(date.today()), "total_tokens": 0, "by_issue": {}}
        with open(path) as f:
            data = json.load(f)
        # Roll over if it's a new day
        if data.get("date") != str(date.today()):
            return {"date": str(date.today()), "total_tokens": 0, "by_issue": {}}
        return data

    def _save(self, repo: str, data: dict[str, Any]) -> None:
        with open(self._path(repo), "w") as f:
            json.dump(data, f, indent=2)

    def record(self, repo: str, issue_number: int, tokens: int) -> None:
        data = self._load(repo)
        data["total_tokens"] += tokens
        issue_key = str(issue_number)
        data["by_issue"][issue_key] = data["by_issue"].get(issue_key, 0) + tokens
        self._save(repo, data)

    def total_today(self, repo: str) -> int:
        return self._load(repo).get("total_tokens", 0)

    def issue_total(self, repo: str, issue_number: int) -> int:
        data = self._load(repo)
        return data.get("by_issue", {}).get(str(issue_number), 0)

    def is_exceeded(self, repo: str, daily_limit: int) -> bool:
        return self.total_today(repo) >= daily_limit

    def summary(self, repo: str) -> dict[str, Any]:
        return self._load(repo)
