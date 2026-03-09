"""Command relay — file-based IPC for sandboxed Claude environments.

Claude writes JSON request files; a watchdog process executes the commands
and writes JSON response files. This lets Claude run commands even when
direct shell access is blocked by sandbox restrictions.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import signal
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from claudette.core.config import Config, RelayConfig

logger = logging.getLogger("claudette.relay")


class RelayRequest(BaseModel):
    id: str
    command: str
    cwd: str | None = None
    timeout: int | None = None
    created_at: str = ""


class RelayResponse(BaseModel):
    id: str
    returncode: int = -1
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    error: str | None = None
    completed_at: str = ""


class SubagentRequest(BaseModel):
    id: str
    prompt: str
    cwd: str
    timeout: int = 1800  # 30 min default
    print_mode: bool = False  # True = claude --print (one-shot), False = full session
    created_at: str = ""


class SubagentResponse(BaseModel):
    id: str
    status: str = "pending"  # pending, running, completed, failed, timed_out
    pid: int | None = None
    output: str = ""
    error: str | None = None
    completed_at: str = ""


def validate_command(cmd: str, relay_config: RelayConfig) -> str | None:
    """Returns error message if command is blocked, None if allowed."""
    # Deny patterns first
    for pattern in relay_config.blocked_patterns:
        if re.search(pattern, cmd):
            return f"Blocked by pattern: {pattern}"

    # Allowlist check — each entry is a prefix (e.g. "git " matches "git status")
    # or an exact command name (e.g. "claudette" matches "claudette tick --dry-run")
    if relay_config.allowed_commands:
        cmd_stripped = cmd.strip()
        cmd_name = cmd_stripped.split()[0] if cmd_stripped else ""
        allowed = False
        for prefix in relay_config.allowed_commands:
            p = prefix.strip()
            if not p:
                continue
            # If the prefix ends with a space, it's a prefix match
            # Otherwise match exact command name or as a prefix
            if cmd_stripped.startswith(p) or cmd_name == p:
                allowed = True
                break
        if not allowed:
            names = [p.strip() for p in relay_config.allowed_commands if p.strip()]
            return f"Command not in allowlist. Allowed: {', '.join(names)}"

    return None


class RelayWatchdog:
    """Watches for request files and executes commands."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.relay_config = config.relay
        self.relay_dir = config.relay_dir
        self.requests_dir = self.relay_dir / "requests"
        self.responses_dir = self.relay_dir / "responses"
        self.subagents_dir = self.relay_dir / "subagents"
        self.pid_file = self.relay_dir / "relay.pid"
        self._running = False
        self._gc_counter = 0
        self._active_subagents: dict[str, subprocess.Popen] = {}

    def setup(self) -> None:
        """Create directories."""
        self.requests_dir.mkdir(parents=True, exist_ok=True)
        self.responses_dir.mkdir(parents=True, exist_ok=True)
        (self.subagents_dir / "requests").mkdir(parents=True, exist_ok=True)
        (self.subagents_dir / "responses").mkdir(parents=True, exist_ok=True)

    def start(self) -> None:
        """Enter the poll loop. Blocks until stopped."""
        self.setup()

        # Check for existing instance
        if self._is_running():
            raise RuntimeError(f"Relay already running (PID {self._read_pid()})")

        # Write PID file
        self.pid_file.write_text(str(os.getpid()))
        self._running = True

        # Handle signals for clean shutdown
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

        logger.info("Relay watchdog started (PID %d)", os.getpid())
        logger.info("Requests:  %s", self.requests_dir)
        logger.info("Responses: %s", self.responses_dir)

        try:
            while self._running:
                self._poll_once()
                self._gc_counter += 1
                if self._gc_counter >= 30:
                    self._gc()
                    self._gc_counter = 0
                time.sleep(self.relay_config.poll_interval)
        finally:
            self._cleanup()

    def stop_remote(self) -> bool:
        """Stop a running watchdog by sending SIGTERM. Returns True if stopped."""
        pid = self._read_pid()
        if pid is None:
            return False

        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            self._cleanup()
            return False

        # Wait for it to die
        for _ in range(50):  # 5 seconds
            try:
                os.kill(pid, 0)
                time.sleep(0.1)
            except OSError:
                self._cleanup()
                return True

        # Force kill
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGKILL)
        self._cleanup()
        return True

    def status(self) -> dict:
        """Return current status info."""
        pid = self._read_pid()
        alive = False
        if pid is not None:
            try:
                os.kill(pid, 0)
                alive = True
            except OSError:
                pass

        pending = 0
        if self.requests_dir.exists():
            pending = len(list(self.requests_dir.glob("*.json")))

        sa_pending = 0
        sa_req_dir = self.subagents_dir / "requests"
        if sa_req_dir.exists():
            sa_pending = len(list(sa_req_dir.glob("*.json")))

        return {
            "running": alive,
            "pid": pid,
            "pending_requests": pending,
            "active_subagents": len(self._active_subagents),
            "pending_subagent_requests": sa_pending,
            "relay_dir": str(self.relay_dir),
        }

    def _poll_once(self) -> None:
        """Process all pending request files and check subagent status."""
        # Command requests
        if self.requests_dir.exists():
            requests = sorted(self.requests_dir.glob("*.json"))
            if requests:
                if len(requests) > self.relay_config.max_pending:
                    for overflow in requests[self.relay_config.max_pending :]:
                        self._write_error_response(
                            overflow, "Too many pending requests — try again later"
                        )
                for req_path in requests[: self.relay_config.max_pending]:
                    self._process_request(req_path)

        # Subagent requests
        sa_requests_dir = self.subagents_dir / "requests"
        if sa_requests_dir.exists():
            for req_path in sorted(sa_requests_dir.glob("*.json")):
                self._process_subagent_request(req_path)

        # Check running subagents
        self._check_subagents()

    def _process_request(self, req_path: Path) -> None:
        """Execute a single request and write the response."""
        try:
            data = json.loads(req_path.read_text())
            request = RelayRequest.model_validate(data)
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("Invalid request %s: %s", req_path.name, e)
            self._write_error_response(req_path, f"Invalid request: {e}")
            return

        # Mark as processing
        done_path = req_path.with_suffix(".json.done")
        req_path.rename(done_path)

        # Validate command
        error = validate_command(request.command, self.relay_config)
        if error:
            logger.warning("Blocked command '%s': %s", request.command, error)
            self._write_response(
                RelayResponse(
                    id=request.id,
                    error=error,
                    completed_at=datetime.now(UTC).isoformat(),
                )
            )
            return

        # Execute
        timeout = min(
            request.timeout or self.relay_config.command_timeout,
            300,  # hard cap at 5 minutes
        )
        cwd = request.cwd or str(self.config.project_dir)

        logger.info("Executing: %s (cwd=%s, timeout=%d)", request.command, cwd, timeout)

        try:
            result = subprocess.run(
                request.command,
                shell=True,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            response = RelayResponse(
                id=request.id,
                returncode=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                completed_at=datetime.now(UTC).isoformat(),
            )
        except subprocess.TimeoutExpired:
            response = RelayResponse(
                id=request.id,
                returncode=-1,
                stderr=f"Command timed out after {timeout}s",
                timed_out=True,
                completed_at=datetime.now(UTC).isoformat(),
            )
        except Exception as e:
            response = RelayResponse(
                id=request.id,
                error=f"Execution error: {e}",
                completed_at=datetime.now(UTC).isoformat(),
            )

        self._write_response(response)
        logger.info("Completed %s (exit=%d)", request.id, response.returncode)

    def _process_subagent_request(self, req_path: Path) -> None:
        """Launch a subagent from a request file."""
        try:
            data = json.loads(req_path.read_text())
            request = SubagentRequest.model_validate(data)
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("Invalid subagent request %s: %s", req_path.name, e)
            self._write_subagent_response(
                SubagentResponse(
                    id=req_path.stem,
                    status="failed",
                    error=f"Invalid request: {e}",
                    completed_at=datetime.now(UTC).isoformat(),
                )
            )
            req_path.rename(req_path.with_suffix(".json.done"))
            return

        # Move to done
        req_path.rename(req_path.with_suffix(".json.done"))

        # Check cwd exists
        cwd = Path(request.cwd)
        if not cwd.is_dir():
            self._write_subagent_response(
                SubagentResponse(
                    id=request.id,
                    status="failed",
                    error=f"Working directory does not exist: {request.cwd}",
                    completed_at=datetime.now(UTC).isoformat(),
                )
            )
            return

        # Build claude command
        if request.print_mode:
            cmd = ["claude", "--print", "--prompt", request.prompt]
        else:
            cmd = ["claude", "--prompt", request.prompt]

        logger.info(
            "Launching subagent %s (cwd=%s, print=%s)", request.id, request.cwd, request.print_mode
        )

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=request.cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self._active_subagents[request.id] = proc

            # Write initial "running" response
            self._write_subagent_response(
                SubagentResponse(
                    id=request.id,
                    status="running",
                    pid=proc.pid,
                )
            )
            logger.info("Subagent %s started (PID %d)", request.id, proc.pid)

        except Exception as e:
            logger.error("Failed to launch subagent %s: %s", request.id, e)
            self._write_subagent_response(
                SubagentResponse(
                    id=request.id,
                    status="failed",
                    error=f"Launch failed: {e}",
                    completed_at=datetime.now(UTC).isoformat(),
                )
            )

    def _check_subagents(self) -> None:
        """Poll active subagents for completion."""
        completed = []
        for sa_id, proc in self._active_subagents.items():
            retcode = proc.poll()
            if retcode is not None:
                stdout = proc.stdout.read() if proc.stdout else ""
                stderr = proc.stderr.read() if proc.stderr else ""
                status = "completed" if retcode == 0 else "failed"
                self._write_subagent_response(
                    SubagentResponse(
                        id=sa_id,
                        status=status,
                        pid=proc.pid,
                        output=stdout,
                        error=stderr if retcode != 0 else None,
                        completed_at=datetime.now(UTC).isoformat(),
                    )
                )
                logger.info("Subagent %s finished (exit=%d)", sa_id, retcode)
                completed.append(sa_id)

        for sa_id in completed:
            del self._active_subagents[sa_id]

    def _write_subagent_response(self, response: SubagentResponse) -> None:
        """Write subagent response atomically."""
        resp_dir = self.subagents_dir / "responses"
        resp_dir.mkdir(parents=True, exist_ok=True)
        resp_path = resp_dir / f"{response.id}.json"
        tmp_path = resp_path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(response.model_dump(), indent=2))
        tmp_path.rename(resp_path)

    def _write_response(self, response: RelayResponse) -> None:
        """Write response atomically (tmp + rename)."""
        resp_path = self.responses_dir / f"{response.id}.json"
        tmp_path = resp_path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(response.model_dump(), indent=2))
        tmp_path.rename(resp_path)

    def _write_error_response(self, req_path: Path, error: str) -> None:
        """Write an error response for a request file."""
        try:
            data = json.loads(req_path.read_text())
            req_id = data.get("id", req_path.stem)
        except (json.JSONDecodeError, OSError):
            req_id = req_path.stem

        # Move to done
        done_path = req_path.with_suffix(".json.done")
        if req_path.exists():
            req_path.rename(done_path)

        self._write_response(
            RelayResponse(
                id=req_id,
                error=error,
                completed_at=datetime.now(UTC).isoformat(),
            )
        )

    def _gc(self) -> None:
        """Remove old request/response files."""
        cutoff = time.time() - self.relay_config.gc_age_seconds
        gc_dirs = [
            self.requests_dir,
            self.responses_dir,
            self.subagents_dir / "requests",
            self.subagents_dir / "responses",
        ]
        for d in gc_dirs:
            if not d.exists():
                continue
            for f in d.iterdir():
                try:
                    if f.stat().st_mtime < cutoff:
                        f.unlink()
                except OSError:
                    pass

    def _is_running(self) -> bool:
        pid = self._read_pid()
        if pid is None:
            return False
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            # Stale PID file
            self._cleanup()
            return False

    def _read_pid(self) -> int | None:
        if not self.pid_file.exists():
            return None
        try:
            return int(self.pid_file.read_text().strip())
        except (ValueError, OSError):
            return None

    def _cleanup(self) -> None:
        if self.pid_file.exists():
            self.pid_file.unlink(missing_ok=True)

    def _handle_signal(self, signum: int, frame: object) -> None:
        logger.info("Received signal %d, stopping", signum)
        self._running = False
