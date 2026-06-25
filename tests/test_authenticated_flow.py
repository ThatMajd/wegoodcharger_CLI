from __future__ import annotations

import json

from typer.testing import CliRunner

from wegoodcharger_cli.client import TokenExpired
from wegoodcharger_cli.cli import app


runner = CliRunner()


class FakeAuthStore:
    def __init__(self, token: str | None = "stored-token") -> None:
        self.token = token
        self.cleared = False

    def get_token(self, email: str) -> str | None:
        return self.token

    def clear_token(self, email: str) -> None:
        self.cleared = True
        self.token = None


class FreshClient:
    validated_tokens: list[str] = []

    def __init__(self, base_url: str, timeout: float, debug: object) -> None:
        pass

    def get_info(self, token: str) -> dict[str, object]:
        self.validated_tokens.append(token)
        return {"email": "me@example.com"}

    def device_list(self, token: str) -> dict[str, object]:
        return {"data": [{"imei": "charger-1", "qrCode": "qr-1", "ccid": "ccid-1"}]}


class ExpiredValidationClient:
    def __init__(self, base_url: str, timeout: float, debug: object) -> None:
        pass

    def get_info(self, token: str) -> dict[str, object]:
        raise TokenExpired("expired", status=401)


def test_authenticated_flow_requires_saved_email(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WEGOODCHARGER_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr("wegoodcharger_cli.cli.AuthStore", FakeAuthStore)
    monkeypatch.setattr("wegoodcharger_cli.cli.CloudClient", FreshClient)

    result = runner.invoke(app, ["devices"])

    assert result.exit_code != 0
    assert "No account email saved" in result.output


def test_authenticated_flow_requires_stored_token(monkeypatch, tmp_path) -> None:
    fake_store = FakeAuthStore(token=None)
    monkeypatch.setenv("WEGOODCHARGER_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr("wegoodcharger_cli.cli.AuthStore", lambda: fake_store)
    monkeypatch.setattr("wegoodcharger_cli.cli.CloudClient", FreshClient)

    config = tmp_path / "config.json"
    config.write_text(json.dumps({"email": "me@example.com"}), encoding="utf-8")

    result = runner.invoke(app, ["devices"])

    assert result.exit_code != 0
    assert "No stored token for me@example.com" in result.output


def test_authenticated_flow_validates_fresh_token(monkeypatch, tmp_path) -> None:
    FreshClient.validated_tokens = []
    monkeypatch.setenv("WEGOODCHARGER_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr("wegoodcharger_cli.cli.AuthStore", FakeAuthStore)
    monkeypatch.setattr("wegoodcharger_cli.cli.CloudClient", FreshClient)

    config = tmp_path / "config.json"
    config.write_text(json.dumps({"email": "me@example.com"}), encoding="utf-8")

    result = runner.invoke(app, ["devices", "--json"])

    assert result.exit_code == 0
    assert FreshClient.validated_tokens == ["stored-token"]


def test_authenticated_flow_clears_expired_token(monkeypatch, tmp_path) -> None:
    fake_store = FakeAuthStore()
    monkeypatch.setenv("WEGOODCHARGER_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr("wegoodcharger_cli.cli.AuthStore", lambda: fake_store)
    monkeypatch.setattr("wegoodcharger_cli.cli.CloudClient", ExpiredValidationClient)

    config = tmp_path / "config.json"
    config.write_text(json.dumps({"email": "me@example.com"}), encoding="utf-8")

    result = runner.invoke(app, ["devices"])

    assert result.exit_code == 3
    assert fake_store.cleared is True
    assert fake_store.token is None
    assert "wegoodcharger-cli login --email me@example.com" in result.stderr
