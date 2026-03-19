# Command relay

For sandboxed Claude environments where direct shell access is restricted.

The relay watchdog runs outside the sandbox, watches for JSON request files, executes allowed commands, and writes JSON response files.

## CLI

```bash
claudette relay start       # start (backgrounds by default)
claudette relay start -f    # foreground
claudette relay status      # check if running
claudette relay stop        # stop
```

## How it works

1. Manager session writes a JSON request to `.claudette/relay/requests/<id>.json`
2. Relay watchdog picks it up, validates against allowlist/blocklist, executes
3. Response written to `.claudette/relay/responses/<id>.json`
4. Manager polls for the response

Sub-agents can also be spawned via the relay when `subagents_enabled` is true.

## Config

```yaml
relay:
  enabled: false
  subagents_enabled: true
  command_timeout: 30
  max_pending: 5
  poll_interval: 0.3
  allowed_commands:
    - "git "
    - "gh "
    - "npm "
    - "pytest "
    - "python "
  blocked_patterns:
    - "rm\\s+-rf\\s+/"
    - "sudo\\s+"
```

Commands are validated against the allowlist first, then checked against blocked patterns. The relay docs are injected into the generated AGENTS.md.
