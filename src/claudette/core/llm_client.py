"""LLM client that uses the claude CLI for inference."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from jinja2 import Environment, FileSystemLoader, Template

from claudette.protocols.llm import LLMResponse

if TYPE_CHECKING:
    from claudette.core.config import LLMConfig


class ClaudeCLIClient:
    """LLMClient that shells out to `claude` CLI for all inference.

    No API keys needed — just a configured `claude` CLI installation.
    Uses `claude --print` for non-interactive one-shot prompts (summarizer).
    Uses `claude` (interactive session) for the manager session with sub-agents.
    """

    CLAUDE_CMD = "claude"

    def __init__(
        self, llm_config: LLMConfig | None = None, prompts_dir: Path | None = None
    ) -> None:
        self._summarizer_template = None
        if llm_config:
            template_name = llm_config.summarizer_prompt
            # Try project-local prompts first, then package defaults
            pkg_prompts = Path(__file__).parent.parent / "prompts"
            for search_dir in [prompts_dir, pkg_prompts] if prompts_dir else [pkg_prompts]:
                if search_dir and (search_dir / template_name).exists():
                    self._summarizer_template = self._load_template(search_dir / template_name)
                    break

    @staticmethod
    def _load_template(path: Path) -> Template:
        resolved = Path(path).resolve()
        env = Environment(
            loader=FileSystemLoader(str(resolved.parent)),
            keep_trailing_newline=True,
        )
        return env.get_template(resolved.name)

    def _run_claude(self, prompt: str) -> str:
        """Run claude CLI in one-shot mode and return the output."""
        cmd = [self.CLAUDE_CMD, "--print", "--prompt", prompt]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"claude CLI failed (exit {result.returncode}): {result.stderr[:500]}"
            )

        return result.stdout

    def summarize(self, thread: str) -> LLMResponse:
        """Compress a GitHub thread using claude CLI (one-shot --print)."""
        text = self._run_claude(thread)
        # claude CLI doesn't report token counts
        return LLMResponse(text=text, input_tokens=0, output_tokens=0)

    def launch_manager_session(
        self,
        prompt: str,
        cwd: str,
        log_path: str | None = None,
    ) -> subprocess.Popen:
        """Launch a full claude code + sub-agents manager session in the background.

        The session runs as an interactive claude process (not --print) so it
        can use sub-agents to fan out work to sub-agents.

        Args:
            prompt: The manager prompt describing all ready issues.
            cwd: Working directory (workspace root).
            log_path: If provided, stdout/stderr are written to this file.

        Returns the Popen handle for tracking.
        """
        cmd = [self.CLAUDE_CMD, "--prompt", prompt]

        if log_path:
            log_file = open(log_path, "w")  # noqa: SIM115 — outlives this scope
            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        else:
            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        return proc

    def render_summarizer_prompt(self, **kwargs: object) -> str:
        if self._summarizer_template is None:
            raise RuntimeError("No summarizer template loaded")
        return self._summarizer_template.render(**kwargs)
