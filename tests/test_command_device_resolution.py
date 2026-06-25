from __future__ import annotations

import json
from typing import Any

from typer.testing import CliRunner

from wegoodcharger_cli.cli import app


runner = CliRunner()


class FakeAuthStore:
    def get_token(self, email: str) -> str | None:
        return "stored-token"


class CommandDeviceClient:
    devices: list[dict[str, Any]] = []
    status_payload: dict[str, Any] | None = None
    start_payload: dict[str, Any] | None = None
    stop_payload: dict[str, Any] | None = None

    def __init__(self, base_url: str, timeout: float, debug: object) -> None:
        pass

    def get_info(self, token: str) -> dict[str, object]:
        return {"email": "me@example.com"}

    def device_list(self, token: str) -> dict[str, object]:
        return {"data": self.devices}

    def poll_status(
        self,
        token: str,
        payload: dict[str, Any],
        poll_count: int,
        poll_interval: float,
    ) -> dict[str, object]:
        type(self).status_payload = dict(payload)
        return {
            "raw": {"voltage": "230"},
            "summary": {"voltage_v": 230.0},
            "visit_time": "123",
        }

    def start_charge(self, token: str, device_payload: dict[str, Any], port: int = 1) -> dict[str, object]:
        type(self).start_payload = dict(device_payload)
        return {"code": 200, "msg": "ok"}

    def stop_charge(self, token: str, device_payload: dict[str, Any], port: int = 1) -> dict[str, object]:
        type(self).stop_payload = dict(device_payload)
        return {"code": 200, "msg": "ok"}


def write_config(tmp_path, default_device: dict[str, Any] | None = None) -> None:
    config: dict[str, Any] = {"email": "me@example.com", "base_url": "https://example.test"}
    if default_device is not None:
        config["default_device"] = default_device
    (tmp_path / "config.json").write_text(json.dumps(config), encoding="utf-8")


def setup_command_client(monkeypatch, tmp_path, devices: list[dict[str, Any]]) -> None:
    CommandDeviceClient.devices = devices
    CommandDeviceClient.status_payload = None
    CommandDeviceClient.start_payload = None
    CommandDeviceClient.stop_payload = None
    monkeypatch.setenv("WEGOODCHARGER_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr("wegoodcharger_cli.cli.AuthStore", FakeAuthStore)
    monkeypatch.setattr("wegoodcharger_cli.cli.CloudClient", CommandDeviceClient)


def test_status_explicit_device_uses_command_capable_payload(monkeypatch, tmp_path) -> None:
    setup_command_client(
        monkeypatch,
        tmp_path,
        [
            {"imei": "device-1", "qrCode": "qr-1", "ccid": "ccid-1"},
            {"imei": "device-2", "qrCode": "qr-2", "ccid": "ccid-2"},
        ],
    )
    write_config(tmp_path)

    result = runner.invoke(app, ["status", "--device", "device-2"])

    assert result.exit_code == 0
    assert CommandDeviceClient.status_payload == {
        "deviceId": "ccid-2",
        "ccid": "ccid-2",
        "qrcode": "qr-2",
    }


def test_start_uses_saved_default_command_payload(monkeypatch, tmp_path) -> None:
    setup_command_client(monkeypatch, tmp_path, [])
    write_config(
        tmp_path,
        {
            "device_id": "original-device-id",
            "qrcode": "qr-default",
            "ccid": "ccid-default",
            "raw": {},
        },
    )

    result = runner.invoke(app, ["start"])

    assert result.exit_code == 0
    assert CommandDeviceClient.start_payload == {
        "deviceId": "ccid-default",
        "ccid": "ccid-default",
        "qrcode": "qr-default",
    }


def test_stop_auto_selects_only_command_capable_device(monkeypatch, tmp_path) -> None:
    setup_command_client(
        monkeypatch,
        tmp_path,
        [{"imei": "only-device", "qrCode": "qr-only", "ccid": "ccid-only"}],
    )
    write_config(tmp_path)

    result = runner.invoke(app, ["stop"])
    saved = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))

    assert result.exit_code == 0
    assert saved["default_device"]["device_id"] == "only-device"
    assert CommandDeviceClient.stop_payload == {
        "deviceId": "ccid-only",
        "ccid": "ccid-only",
        "qrcode": "qr-only",
    }


def test_use_device_rejects_device_missing_command_fields(monkeypatch, tmp_path) -> None:
    setup_command_client(monkeypatch, tmp_path, [{"imei": "device-1", "qrCode": "qr-1"}])
    write_config(tmp_path)

    result = runner.invoke(app, ["use-device", "0"])
    saved = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))

    assert result.exit_code != 0
    assert "ccid" in result.output
    assert "default_device" not in saved


def test_status_reports_missing_fields_when_no_command_capable_device(monkeypatch, tmp_path) -> None:
    setup_command_client(
        monkeypatch,
        tmp_path,
        [
            {"imei": "device-1", "qrCode": "qr-1"},
            {"imei": "device-2", "ccid": "ccid-2"},
        ],
    )
    write_config(tmp_path)

    result = runner.invoke(app, ["status"])

    assert result.exit_code != 0
    assert "No command-capable chargers found" in result.stderr
    assert "device-1 missing ccid" in result.stderr
    assert "device-2 missing qrcode" in result.stderr
