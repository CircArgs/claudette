"""Factory for creating forge clients based on config."""

from __future__ import annotations

from claudette.core.config import Config, ForgeType
from claudette.protocols.forge import ForgeClient


def create_forge_client(config: Config) -> ForgeClient:
    """Create the appropriate forge client based on config.github.forge_type."""
    forge_type = config.github.forge_type

    if forge_type == ForgeType.GITHUB:
        return _create_github_client(config)
    elif forge_type == ForgeType.GITLAB:
        return _create_gitlab_client(config)
    elif forge_type == ForgeType.GITEA:
        return _create_gitea_client(config)
    else:
        raise ValueError(f"Unsupported forge type: {forge_type}")


def _create_github_client(config: Config) -> ForgeClient:
    """Create a GitHub client (httpx or gh CLI)."""
    import os

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN", "")

    # Try httpx client first, fall back to gh CLI
    if token:
        from claudette.core.github_client import LiveGitHubClient

        return LiveGitHubClient(token)

    # No token — try gh CLI (uses its own auth)
    from claudette.core.gh_cli_client import GhCliGitHubClient

    return GhCliGitHubClient()


def _create_gitlab_client(config: Config) -> ForgeClient:
    """Create a GitLab client."""
    raise NotImplementedError(
        "GitLab support is not yet implemented. "
        "Contributions welcome! See claudette/core/forge_factory.py"
    )


def _create_gitea_client(config: Config) -> ForgeClient:
    """Create a Gitea client."""
    raise NotImplementedError(
        "Gitea support is not yet implemented. "
        "Contributions welcome! See claudette/core/forge_factory.py"
    )
