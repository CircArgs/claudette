"""LLM client that uses a configurable CLI command for inference."""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from jinja2 import Environment, FileSystemLoader, Template

from claudette.protocols.llm import LLMResponse

if TYPE_CHECKING:
    from claudette.core.config import LLMConfig


def _build_cmd(template: str, prompt: str) -> list[str]:
    """Build a command list from a template string, substituting {prompt}."""
    # Replace {prompt} with a placeholder, split, then put prompt back
    # This ensures the prompt is a single argument even if it has spaces
    placeholder = "\x00PROMPT\x00"
    filled = template.replace("{prompt}", placeholder)
    parts = shlex.split(filled)
    return [prompt if p == placeholder else p for p in parts]


class ClaudeCLIClient:
    """LLMClient that shells out to a CLI command for all inference.

    Command templates are configurable via LLMConfig:
      cmd_one_shot: for summarizer (one-shot, capture output)
      cmd_session:  for manager (long-running, autonomous)
      cmd_subagent: for worker agents in worktrees
    """

    def __init__(
        self, llm_config: LLMConfig | None = None, prompts_dir: Path | None = None
    ) -> None:
        self._summarizer_template = None
        self._cmd_one_shot = "claude -p {prompt}"
        self._cmd_session = "claude -p --dangerously-skip-permissions {prompt}"
        self._cmd_subagent = "claude -p --dangerously-skip-permissions {prompt}"

        if llm_config:
            self._cmd_one_shot = llm_config.cmd_one_shot
            self._cmd_session = llm_config.cmd_session
            self._cmd_subagent = llm_config.cmd_subagent

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

    def _run_one_shot(self, prompt: str) -> str:
        """Run CLI in one-shot mode and return the output."""
        cmd = _build_cmd(self._cmd_one_shot, prompt)

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"CLI command failed (exit {result.returncode}): {result.stderr[:500]}"
            )

        return result.stdout

    def summarize(self, thread: str) -> LLMResponse:
        """Compress a GitHub thread using CLI (one-shot)."""
        text = self._run_one_shot(thread)
        return LLMResponse(text=text, input_tokens=0, output_tokens=0)

    def launch_manager_session(
        self,
        prompt: str,
        cwd: str,
        log_path: str | None = None,
    ) -> subprocess.Popen:
        """Launch a manager session in the background.

        Args:
            prompt: The manager prompt describing all ready issues.
            cwd: Working directory (workspace root).
            log_path: If provided, stdout/stderr are written to this file.

        Returns the Popen handle for tracking.
        """
        cmd = _build_cmd(self._cmd_session, prompt)

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

    @property
    def subagent_cmd_template(self) -> str:
        """The command template for spawning subagents (used by relay)."""
        return self._cmd_subagent

    def render_summarizer_prompt(self, **kwargs: object) -> str:
        if self._summarizer_template is None:
            raise RuntimeError("No summarizer template loaded")
        return self._summarizer_template.render(**kwargs)
