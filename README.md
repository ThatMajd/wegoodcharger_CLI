# WeGood Charger CLI

A small, read-only command-line client for WeGood EV chargers.

This project talks to the WeGood cloud API used by the mobile app. It currently focuses on the safest useful vertical slice: log in, list chargers, choose a default charger, and fetch charger status.

## Current Scope

Implemented:

- Account login with a securely prompted password
- Token storage in the system keyring
- Device discovery from the cloud account
- Saved default charger selection
- Read-only status lookup
- JSON output for scripting
- Redacted debug logging
- Expired-token handling with a clear re-login prompt

Not implemented yet:

- Start/stop charging
- Scheduling
- Bluetooth/local control
- Any write operation against the charger

## Install

For local development:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[test]"
```

Then run:

```bash
.venv/bin/wegoodcharger-cli --help
```

If you install the package into an active shell environment, the command is available as:

```bash
wegoodcharger-cli
```

## Basic Workflow

Log in:

```bash
wegoodcharger-cli login --email you@example.com
```

List chargers on the account:

```bash
wegoodcharger-cli devices
```

If the account has exactly one charger, it is selected as the default automatically and the CLI tells you.

For accounts with multiple chargers, select the default charger by index, device ID, QR code, or CCID:

```bash
wegoodcharger-cli use-device 0
```

Fetch status:

```bash
wegoodcharger-cli status
```

Use a one-off charger without changing the saved default:

```bash
wegoodcharger-cli status --device [device_id]
```

## Status Behavior

Status uses the cloud API sequence observed from the mobile app:

1. `POST /device/sendPortDetailCmd`
2. Read the returned `msg` value as the request time
3. `POST /device/getPortDetail` with `deviceId`, `ccid`, `qrcode`, and `time`

The command polls `getPortDetail` while responses are not nominal. A nominal response is HTTP `200`; the status command then checks whether telemetry data is present.

If the cloud returns success without telemetry, for example:

```json
{
  "msg": "操作成功",
  "code": 200
}
```

the CLI reports a partial success. This usually means the charger accepted the status request, but no live telemetry was available. Likely causes include the charger being disconnected from power, offline, or the cloud service not having status data yet.

Polling can be tuned:

```bash
wegoodcharger-cli status --poll-count 12 --poll-interval 1.5
```

## Debugging

Use `--debug` to print redacted request/response diagnostics:

```bash
wegoodcharger-cli --debug status
wegoodcharger-cli status --debug
```

Debug logs include request flow, response status, timing, payload shape, and polling decisions. Passwords, bearer tokens, and authorization headers are redacted.

## Auth And Local Storage

The CLI stores:

- Auth token: system keyring
- Non-secret preferences: local config file

The config file stores values like account email, API base URL, and the selected default charger. It does not store the password or bearer token.

For tests or isolated runs, override the config directory:

```bash
WEGOODCHARGER_CONFIG_DIR=/tmp/wegoodcharger-test wegoodcharger-cli devices
```

If the API returns `401` or `403`, the CLI clears the stored token and asks you to log in again.

## Development

Run tests:

```bash
.venv/bin/python -m pytest
```

Useful files:

- `wegoodcharger_cli/client.py`: cloud API client and status polling
- `wegoodcharger_cli/cli.py`: Typer command surface
- `wegoodcharger_cli/domain.py`: device normalization and status decoding
- `wegood_cloud_probe.py`: original reverse-engineering probe kept for reference
- `TASK1_CONTROL_PATH_REPORT.md`: notes from the APK control-path investigation

## Safety Notes

This is an unofficial tool built from observed app behavior. The API is not documented by the vendor and may change without notice.

The current CLI intentionally stays read-only. It does not start charging, stop charging, change schedules, or send charger-control commands beyond status retrieval.
