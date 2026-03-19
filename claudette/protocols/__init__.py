"""Dependency injection protocols for testability."""

from claudette.protocols.clock import Clock
from claudette.protocols.github import GitHubClient
from claudette.protocols.llm import LLMClient

__all__ = ["Clock", "GitHubClient", "LLMClient"]
