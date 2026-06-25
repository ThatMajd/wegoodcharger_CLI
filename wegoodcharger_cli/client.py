from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import requests

from .debug import DebugLogger
from .domain import body_data, summarize_status_payload


DEFAULT_BASE_URL = "https://ev.weguyun.com"
USER_AGENT = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_6_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Html5Plus/1.0 (Immersed/20) uni-app"


class ApiError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, body: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.body = body


class TokenExpired(ApiError):
    pass


@dataclass(frozen=True)
class ApiResponse:
    status: int
    headers: dict[str, str]
    body: Any


class CloudClient:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 15.0,
        debug: DebugLogger | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.debug = debug or DebugLogger(False)
        self.session = session or requests.Session()

    def login(self, email: str, password: str) -> str:
        response = self.request(
            "POST",
            "/mailLogin",
            payload={"username": email, "password": password},
            token=None,
            auth_required=False,
        )
        if not isinstance(response.body, dict) or not response.body.get("token"):
            raise ApiError("login succeeded but no token was returned", status=response.status, body=response.body)
        return str(response.body["token"])

    def get_info(self, token: str) -> Any:
        return self.request("GET", "/getInfo", token=token).body

    def device_list(self, token: str) -> Any:
        return self.request("POST", "/device/deviceList", payload={}, token=token).body

    def charge_records(self, token: str, page_num: int = 1, page_size: int = 6) -> Any:
        path = f"/chargeRecord/list?&pageNum={page_num}&pageSize={page_size}"
        payload = {
            "pageNum": page_num,
            "pageSize": page_size,
            "reasonable": True,
        }
        return self.request("POST", path, payload=payload, token=token).body

    def start_charge(self, token: str, device_payload: dict[str, Any], port: int = 1) -> Any:
        payload = self._status_payload(device_payload)
        payload["port"] = port
        payload['deviceId'] = payload["ccid"]
        return self.request("POST", "/device/startCharge", payload=payload, token=token).body

    def stop_charge(self, token: str, device_payload: dict[str, Any], port: int = 1) -> Any:
        payload = self._status_payload(device_payload)
        payload["port"] = port
        payload["deviceId"] = payload["ccid"]
        return self.request("POST", "/device/stopCharge", payload=payload, token=token).body
    
    def poll_status(
        self,
        token: str,
        device_payload: dict[str, Any],
        poll_count: int = 8,
        poll_interval: float = 1.0,
    ) -> dict[str, Any]:
        payload = self._status_payload(device_payload)
        self.debug.log("status payload prepared", payload=payload)

        self.debug.log("requesting status visit time")
        command_response = self._sendPortDetailCmd(token, payload)
        visit_time = self._visit_time_from_response(command_response)
        self.debug.log("status visit time received", visit_time=visit_time)

        self.debug.log("polling status detail", poll_count=poll_count, poll_interval=poll_interval)
        latest_telemetry: Any = None
        latest_empty_response: ApiResponse | None = None
        last_error: ApiError | None = None

        for attempt in range(1, poll_count + 1):
            self.debug.log("poll attempt", operation="get port detail", attempt=attempt)
            try:
                detail_response = self._getPortDetail(token, payload, visit_time)
            except TokenExpired:
                self.debug.log("poll auth expired", operation="get port detail", attempt=attempt)
                raise
            except ApiError as exc:
                last_error = exc
                self.debug.log(
                    "poll response not nominal",
                    operation="get port detail",
                    attempt=attempt,
                    status=exc.status,
                    body_shape=_body_shape(exc.body),
                )
                if attempt < poll_count:
                    time.sleep(poll_interval)
                continue

            status_data = body_data(detail_response.body)
            if status_data in (None, ""):
                latest_empty_response = detail_response
                self.debug.log(
                    "poll response without telemetry",
                    operation="get port detail",
                    attempt=attempt,
                    body_shape=_body_shape(detail_response.body),
                )
            else:
                latest_telemetry = status_data
                last_error = None
                self.debug.log(
                    "poll telemetry received",
                    operation="get port detail",
                    attempt=attempt,
                    body_shape=_body_shape(status_data),
                )

            if attempt < poll_count:
                time.sleep(poll_interval)

        if latest_telemetry is None:
            if latest_empty_response is None:
                self.debug.log(
                    "poll exhausted",
                    operation="get port detail",
                    attempts=poll_count,
                    last_status=last_error.status if last_error else None,
                    last_body_shape=_body_shape(last_error.body) if last_error else None,
                )
                raise ApiError(
                    f"get port detail did not become nominal after {poll_count} attempts",
                    status=last_error.status if last_error else None,
                    body=last_error.body if last_error else None,
                )

            message = (
                "Status command partially succeeded: charger accepted the status request "
                "but did not return telemetry data. The charger may be disconnected from "
                "power, offline, or the cloud server may not have status data yet."
            )
            self.debug.log(
                "status partially succeeded without telemetry",
                response_body=latest_empty_response.body,
            )
            return {
                "raw": latest_empty_response.body,
                "summary": {},
                "visit_time": visit_time,
                "partial_success": True,
                "message": message,
            }

        summary = summarize_status_payload(latest_telemetry)
        self.debug.log(
            "status data decoded",
            summary_keys=sorted(summary.keys()) if summary else [],
        )
        return {
            "raw": latest_telemetry,
            "summary": summary or {},
            "visit_time": visit_time,
            "partial_success": False,
            "message": None,
        }

    def _status_payload(self, device_payload: dict[str, Any]) -> dict[str, Any]:
        missing = [
            key
            for key in ("deviceId", "ccid", "qrcode")
            if device_payload.get(key) in (None, "")
        ]
        if missing:
            raise ApiError(f"device payload missing required status fields: {', '.join(missing)}")

        return {
            "deviceId": device_payload["deviceId"],
            "ccid": device_payload["ccid"],
            "qrcode": device_payload["qrcode"],
        }

    def _sendPortDetailCmd(self, token: str, payload: dict[str, Any]) -> ApiResponse:
        return self.request(
            "POST",
            "/device/sendPortDetailCmd",
            payload=payload,
            token=token,
        )

    def _visit_time_from_response(self, response: ApiResponse) -> str:
        if not isinstance(response.body, dict) or response.body.get("msg") in (None, ""):
            raise ApiError(
                "status visit-time response did not include msg",
                status=response.status,
                body=response.body,
            )
        return str(response.body["msg"])

    def _getPortDetail(self, token: str, payload: dict[str, Any], visit_time: str) -> ApiResponse:
        detail_payload = dict(payload)
        detail_payload["time"] = visit_time
        detail_payload['deviceId'] = detail_payload["ccid"]
        return self.request(
            "POST",
            "/device/getPortDetail",
            payload=detail_payload,
            token=token,
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        token: str | None = None,
        auth_required: bool = True,
    ) -> ApiResponse:
        url = f"{self.base_url}{path}"
        headers = self._headers(token)

        self.debug.log("http request", method=method, url=url, headers=headers, payload=payload)
        start = time.monotonic()

        try:
            response = self.session.request(
                method,
                url,
                json=payload,
                headers=headers,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise ApiError(f"cloud API request failed: {exc}") from exc

        elapsed_ms = round((time.monotonic() - start) * 1000, 1)
        body = _response_body(response)
        api_response = ApiResponse(response.status_code, dict(response.headers), body)
        self.debug.log(
            "http response",
            method=method,
            url=url,
            status=api_response.status,
            elapsed_ms=elapsed_ms,
            body_shape=_body_shape(body),
        )

        _raise_for_auth_expiry(api_response, auth_required=auth_required)
        if response.status_code >= 400:
            raise ApiError("cloud API request failed", status=api_response.status, body=api_response.body)
        return api_response

    def _headers(self, token: str | None) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "*/*",
            "User-Agent": USER_AGENT,
            "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers


def _response_body(response: requests.Response) -> Any:
    if not response.content:
        return None
    try:
        return response.json()
    except ValueError:
        return {"_raw": response.text}


def _body_shape(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            "type": "object",
            "keys": sorted(str(key) for key in value.keys()),
        }
    if isinstance(value, list):
        return {
            "type": "array",
            "length": len(value),
        }
    return {"type": type(value).__name__}


def _raise_for_auth_expiry(response: ApiResponse, *, auth_required: bool) -> None:
    if not auth_required:
        return
    if response.status in (401, 403):
        raise TokenExpired("stored session expired or is no longer valid", status=response.status, body=response.body)

    if isinstance(response.body, dict):
        text = str(response.body).lower()
        code = str(response.body.get("code", "")).lower()
        if code in {"401", "403", "unauthorized", "token_expired"}:
            raise TokenExpired("stored session expired or is no longer valid", status=response.status, body=response.body)
        if "token" in text and any(marker in text for marker in ("expired", "invalid", "unauthorized", "失效")):
            raise TokenExpired("stored session expired or is no longer valid", status=response.status, body=response.body)
