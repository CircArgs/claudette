"""Parse identity signatures from GitHub comments."""

from __future__ import annotations

import re
from enum import Enum


class Author(Enum):
    HUMAN = "human"
    MANAGER = "manager"
    WORKER = "worker"


MANAGER_SIGNATURE = "<!--agent:manager-->"
WORKER_SIGNATURE_PATTERN = re.compile(r"<!--agent:worker:(\d+)-->")


def parse_author(comment_body: str) -> tuple[Author, int | None]:
    """Determine who wrote a comment based on hidden HTML signatures.

    Returns (Author, issue_number) where issue_number is set only for workers.
    """
    if MANAGER_SIGNATURE in comment_body:
        return Author.MANAGER, None

    match = WORKER_SIGNATURE_PATTERN.search(comment_body)
    if match:
        return Author.WORKER, int(match.group(1))

    return Author.HUMAN, None


def stamp_manager(body: str) -> str:
    """Append the manager signature to a comment body."""
    return f"{body}\n\n{MANAGER_SIGNATURE}"


def stamp_worker(body: str, issue_number: int) -> str:
    """Append a worker signature to a comment body."""
    return f"{body}\n\n<!--agent:worker:{issue_number}-->"
