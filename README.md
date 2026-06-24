# WeGood Charger CLI

<p align="center">
  <img src="assets/wegoodcharger-app-icon.png" alt="WeGood Charger app icon" width="104" />
</p>

<p align="center">
  An unofficial command-line client for WeGood EV chargers, built by tracing the cloud API used by the mobile app.
</p>

<p align="center">
  Login. Inspect chargers. Read status. View charge history. Start and stop charging.
</p>

## What It Does

This project wraps the WeGood cloud API in a small CLI intended for practical day-to-day use and further reverse-engineering work.

Current commands:

- `login`
- `devices`
- `use-device`
- `status`
- `charge-records`
- `start`
- `stop`

## Quick Start

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[test]"
```

Log in:

```bash
.venv/bin/wegoodcharger-cli login --email you@example.com
```

List chargers:

```bash
.venv/bin/wegoodcharger-cli devices
```

Choose a default charger:

```bash
.venv/bin/wegoodcharger-cli use-device 0
```

Read status:

```bash
.venv/bin/wegoodcharger-cli status
```

View charge history:

```bash
.venv/bin/wegoodcharger-cli charge-records
```

Start charging:

```bash
.venv/bin/wegoodcharger-cli start --port 1
```

Stop charging:

```bash
.venv/bin/wegoodcharger-cli stop --port 1
```

## Command Reference

```bash
wegoodcharger-cli login --email you@example.com
wegoodcharger-cli devices
wegoodcharger-cli use-device 0
wegoodcharger-cli status
wegoodcharger-cli status --device your-device-id
wegoodcharger-cli charge-records --page 1 --page-size 10
wegoodcharger-cli start --port 1
wegoodcharger-cli stop --port 1
```

## Notes

- This is an unofficial client. The vendor does not publish this API.
- The implementation is based on observed app behavior and reverse-engineering of the APK.
- Cloud behavior may change without notice.
- `start` and `stop` are real control commands. Use them carefully against live hardware.
- Bluetooth and local-only control are not implemented here.

## Development

Run tests:

```bash
.venv/bin/python -m pytest
```

Useful files:

- `wegoodcharger_cli/cli.py` - Typer command surface
- `wegoodcharger_cli/client.py` - cloud API client
- `wegoodcharger_cli/domain.py` - device normalization and status decoding
- `wegood_cloud_probe.py` - earlier probe script kept for reference
- `TASK1_CONTROL_PATH_REPORT.md` - APK tracing notes
