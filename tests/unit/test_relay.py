"""Tests for command relay."""

import json
from pathlib import Path

from claudette.core.config import Config, RelayConfig
from claudette.core.relay import RelayWatchdog, validate_command


class TestValidateCommand:
    def test_allowed_command(self):
        config = RelayConfig()
        assert validate_command("git status", config) is None
        assert validate_command("pytest tests/", config) is None
        assert validate_command("ls -la", config) is None

    def test_blocked_by_allowlist(self):
        config = RelayConfig()
        result = validate_command("wget http://example.com", config)
        assert result is not None
        assert "allowlist" in result.lower()

    def test_blocked_by_pattern(self):
        config = RelayConfig()
        result = validate_command("sudo rm -rf /", config)
        assert result is not None
        assert "Blocked by pattern" in result

    def test_empty_allowlist_allows_everything(self):
        config = RelayConfig(allowed_commands=[])
        assert validate_command("anything goes", config) is None

    def test_deny_takes_priority(self):
        config = RelayConfig(
            allowed_commands=["sudo "],
            blocked_patterns=[r"sudo\s+"],
        )
        result = validate_command("sudo ls", config)
        assert result is not None
        assert "Blocked by pattern" in result

    def test_custom_allowlist(self):
        config = RelayConfig(allowed_commands=["echo ", "date"])
        assert validate_command("echo hello", config) is None
        assert validate_command("date", config) is None
        assert validate_command("git status", config) is not None


class TestRelayWatchdog:
    def _make_config(self, tmp_path: Path) -> Config:
        return Config(project_dir=tmp_path)

    def test_setup_creates_dirs(self, tmp_path: Path):
        config = self._make_config(tmp_path)
        watchdog = RelayWatchdog(config)
        watchdog.setup()
        assert (config.relay_dir / "requests").is_dir()
        assert (config.relay_dir / "responses").is_dir()

    def test_status_not_running(self, tmp_path: Path):
        config = self._make_config(tmp_path)
        watchdog = RelayWatchdog(config)
        info = watchdog.status()
        assert not info["running"]
        assert info["pid"] is None

    def test_process_request(self, tmp_path: Path):
        config = self._make_config(tmp_path)
        watchdog = RelayWatchdog(config)
        watchdog.setup()

        # Write a request
        config.relay.allowed_commands.append("echo ")
        req = {"id": "test123", "command": "echo hello", "created_at": "2026-01-01T00:00:00Z"}
        req_path = config.relay_dir / "requests" / "test123.json"
        req_path.write_text(json.dumps(req))

        # Process it
        watchdog._poll_once()

        # Check response
        resp_path = config.relay_dir / "responses" / "test123.json"
        assert resp_path.exists()
        resp = json.loads(resp_path.read_text())
        assert resp["id"] == "test123"
        assert resp["returncode"] == 0
        assert "hello" in resp["stdout"]

        # Request should be moved to .done
        assert not req_path.exists()
        assert req_path.with_suffix(".json.done").exists()

    def test_blocked_command_returns_error(self, tmp_path: Path):
        config = self._make_config(tmp_path)
        watchdog = RelayWatchdog(config)
        watchdog.setup()

        req = {"id": "bad1", "command": "sudo rm -rf /", "created_at": "2026-01-01T00:00:00Z"}
        (config.relay_dir / "requests" / "bad1.json").write_text(json.dumps(req))

        watchdog._poll_once()

        resp = json.loads((config.relay_dir / "responses" / "bad1.json").read_text())
        assert resp["error"] is not None
        assert "Blocked" in resp["error"]

    def test_invalid_json_returns_error(self, tmp_path: Path):
        config = self._make_config(tmp_path)
        watchdog = RelayWatchdog(config)
        watchdog.setup()

        (config.relay_dir / "requests" / "bad2.json").write_text("not json {{{")

        watchdog._poll_once()

        resp_path = config.relay_dir / "responses" / "bad2.json"
        assert resp_path.exists()
        resp = json.loads(resp_path.read_text())
        assert resp["error"] is not None

    def test_command_with_cwd(self, tmp_path: Path):
        config = self._make_config(tmp_path)
        watchdog = RelayWatchdog(config)
        watchdog.setup()

        sub = tmp_path / "subdir"
        sub.mkdir()

        req = {"id": "cwd1", "command": "ls", "cwd": str(sub), "created_at": ""}
        (config.relay_dir / "requests" / "cwd1.json").write_text(json.dumps(req))

        watchdog._poll_once()

        resp = json.loads((config.relay_dir / "responses" / "cwd1.json").read_text())
        assert resp["returncode"] == 0

    def test_max_pending_overflow(self, tmp_path: Path):
        config = self._make_config(tmp_path)
        config.relay.max_pending = 2
        watchdog = RelayWatchdog(config)
        watchdog.setup()

        # Write 3 requests
        for i in range(3):
            req = {"id": f"req{i}", "command": "echo hi", "created_at": ""}
            (config.relay_dir / "requests" / f"req{i}.json").write_text(json.dumps(req))

        watchdog._poll_once()

        # All 3 should have responses
        for i in range(3):
            resp_path = config.relay_dir / "responses" / f"req{i}.json"
            assert resp_path.exists()

        # The overflow one should have an error
        resp2 = json.loads((config.relay_dir / "responses" / "req2.json").read_text())
        assert resp2["error"] is not None
        assert "pending" in resp2["error"].lower()
