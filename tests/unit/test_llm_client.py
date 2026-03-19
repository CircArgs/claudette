"""Tests for the claude CLI LLM client."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from claudette.core.config import LLMConfig
from claudette.core.llm_client import ClaudeCLIClient

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "src" / "claudette" / "prompts"


class TestRunClaude:
    def test_summarize_calls_claude_cli(self):
        client = ClaudeCLIClient()

        with patch("claudette.core.llm_client.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "This is a summary."
            mock_run.return_value.stderr = ""

            result = client.summarize("some thread content")

        assert result.text == "This is a summary."
        assert result.input_tokens == 0
        assert result.output_tokens == 0

        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "claude"
        assert "-p" in cmd
        assert "some thread content" in cmd

    def test_claude_cli_failure_raises(self):
        client = ClaudeCLIClient()

        with patch("claudette.core.llm_client.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stdout = ""
            mock_run.return_value.stderr = "Error: something went wrong"

            with pytest.raises(RuntimeError, match="CLI command failed"):
                client.summarize("some thread")


class TestLaunchManagerSession:
    def test_launch_session_starts_process(self):
        client = ClaudeCLIClient()

        with patch("claudette.core.llm_client.subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.pid = 12345
            mock_popen.return_value = mock_proc

            proc = client.launch_manager_session("work on issues", "/tmp/workspace")

        assert proc.pid == 12345
        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == "claude"
        assert "-p" in cmd
        assert "work on issues" in cmd
        assert mock_popen.call_args[1]["cwd"] == "/tmp/workspace"

    def test_launch_session_detached(self):
        client = ClaudeCLIClient()

        with patch("claudette.core.llm_client.subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.pid = 11111
            mock_popen.return_value = mock_proc

            client.launch_manager_session("prompt", "/tmp")

        # Should start in a new session (detached)
        assert mock_popen.call_args[1]["start_new_session"] is True

    def test_launch_session_with_log_path(self, tmp_path):
        client = ClaudeCLIClient()
        log_file = str(tmp_path / "session.log")

        with patch("claudette.core.llm_client.subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.pid = 22222
            mock_popen.return_value = mock_proc

            with patch("builtins.open", create=True) as mock_open:
                mock_open.return_value = MagicMock()
                client.launch_manager_session("prompt", "/tmp", log_path=log_file)

            # stdout should be the log file handle, not PIPE
            assert mock_popen.call_args[1].get("stdout") is not None


class TestTemplateRendering:
    def test_summarizer_template_renders(self):
        config = LLMConfig(summarizer_prompt="summarizer.jinja2")
        client = ClaudeCLIClient(llm_config=config, prompts_dir=PROMPTS_DIR)

        result = client.render_summarizer_prompt(
            repo="owner/repo",
            number=42,
            title="Fix the thing",
            comments=[{"author": "alice", "body": "Please fix this"}],
        )

        assert "owner/repo" in result
        assert "42" in result
        assert "Fix the thing" in result

    def test_no_template_raises(self):
        client = ClaudeCLIClient()
        with pytest.raises(RuntimeError, match="No summarizer template"):
            client.render_summarizer_prompt(repo="x", number=1, title="t", comments=[])
