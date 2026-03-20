"""Dependency injection protocols for testability."""

from claudette.protocols.clock import Clock
from claudette.protocols.forge import ForgeClient
from claudette.protocols.github import GitHubClient  # backward compat alias
from claudette.protocols.llm import LLMClient

__all__ = ["Clock", "ForgeClient", "GitHubClient", "LLMClient"]
