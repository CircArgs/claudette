"""Backward-compatible re-exports from forge protocol."""

from claudette.protocols.forge import Comment, ForgeClient, Issue, Review

# Alias for backward compatibility — all existing imports continue to work
GitHubClient = ForgeClient

__all__ = ["Comment", "GitHubClient", "Issue", "Review"]
