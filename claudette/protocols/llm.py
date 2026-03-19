"""LLM client protocol."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    import subprocess


@dataclass
class LLMResponse:
    text: str
    input_tokens: int
    output_tokens: int


@runtime_checkable
class LLMClient(Protocol):
    def summarize(self, thread: str) -> LLMResponse:
        """Compress a GitHub thread into a concise technical summary."""
        ...

    def launch_manager_session(
        self,
        prompt: str,
        cwd: str,
        log_path: str | None = None,
    ) -> subprocess.Popen:
        """Launch a full claude code + claude code manager session.

        Returns the Popen handle for the background session.
        """
        ...
