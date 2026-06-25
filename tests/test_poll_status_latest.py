from __future__ import annotations

from typing import Any

import requests

from wegoodcharger_cli.client import CloudClient


class FakeSession:
    def __init__(self, responses: list[requests.Response]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self.responses:
            raise AssertionError(f"unexpected request to {url}")
        return self.responses.pop(0)


def response(status: int, body: bytes) -> requests.Response:
    item = requests.Response()
    item.status_code = status
    item._content = body
    item.headers["Content-Type"] = "application/json;charset=UTF-8"
    return item


def device_payload() -> dict[str, str]:
    return {
        "deviceId": "ECE334ACEF7C",
        "ccid": "ECE334ACEF7C",
        "qrcode": "GD1B501165",
    }


def telemetry(voltage: int, power: int) -> bytes:
    return (
        b'{"msg":"ok","code":200,"data":{'
        + f'"voltage":{voltage},"port_first_status":2,"power":{power},"elec":1307'.encode("utf-8")
        + b"}}"
    )


def test_poll_status_returns_latest_telemetry_after_all_attempts() -> None:
    session = FakeSession(
        [
            response(200, b'{"msg":"2026.06.21 15:55:16","code":200}'),
            response(200, telemetry(220, 1000)),
            response(200, telemetry(230, 2000)),
            response(200, telemetry(240, 3000)),
        ]
    )

    result = CloudClient("https://example.test", session=session).poll_status(
        "token",
        device_payload(),
        poll_count=3,
        poll_interval=0,
    )

    detail_calls = [call for call in session.calls if call["url"].endswith("/device/getPortDetail")]
    assert len(detail_calls) == 3
    assert result["raw"]["voltage"] == 240
    assert result["summary"]["voltage_v"] == 240.0
    assert result["summary"]["port_1_power_w"] == 3000.0
    assert result["partial_success"] is False


def test_poll_status_empty_200_does_not_erase_latest_telemetry() -> None:
    session = FakeSession(
        [
            response(200, b'{"msg":"2026.06.21 15:55:16","code":200}'),
            response(200, telemetry(230, 2000)),
            response(200, b'{"msg":"ok","code":200}'),
            response(200, telemetry(235, 2500)),
            response(200, b'{"msg":"still ok","code":200}'),
        ]
    )

    result = CloudClient("https://example.test", session=session).poll_status(
        "token",
        device_payload(),
        poll_count=4,
        poll_interval=0,
    )

    detail_calls = [call for call in session.calls if call["url"].endswith("/device/getPortDetail")]
    assert len(detail_calls) == 4
    assert result["raw"]["voltage"] == 235
    assert result["summary"]["voltage_v"] == 235.0
    assert result["summary"]["port_1_power_w"] == 2500.0
    assert result["partial_success"] is False
