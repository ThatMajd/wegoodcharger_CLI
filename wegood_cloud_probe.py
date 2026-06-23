#!/usr/bin/env python3
"""
Small probe client for the WeGood charger cloud API discovered in the APK.

Endpoints recovered from the app bundle:
- POST /mailLogin
- GET  /getInfo
- POST /device/deviceList
- POST /device/sendPortDetailCmd
- POST /device/getPortDetail
- POST /device/statusChange
- POST /device/startCharge
- POST /device/stopCharge

Usage:
  python3 wegood_cloud_probe.py --email you@example.com
  python3 wegood_cloud_probe.py --email you@example.com --password 'secret'
  python3 wegood_cloud_probe.py --token 'your-token'
"""

from __future__ import annotations

import argparse
import getpass
import json
import ssl
import sys
import time
import urllib.error
import urllib.request
import urllib.parse
from urllib.parse import quote
from typing import Any, Dict, Optional, Tuple

try:
    import websocket  # type: ignore
except ModuleNotFoundError:
    websocket = None


DEFAULT_BASE_URL = "https://ev.weguyun.com"
DEFAULT_SOCKET_URL = "wss://ev.weguyun.com/websocket"
USER_AGENT = "WeGoodCharger-Probe/0.1"
PORT_STATUS_LABELS = {
    1: "plug_not_inserted",
    2: "charging",
    5: "ready_to_start",
    6: "scheduled",
    7: "cannot_schedule",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Probe the WeGood charger cloud API with your account credentials."
    )
    parser.add_argument("--email", help="Account email / username")
    parser.add_argument(
        "--password",
        help="Account password. If omitted, you will be prompted securely.",
    )
    parser.add_argument(
        "--token",
        help="Bearer token from a previous successful login. Skips /mailLogin.",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"API base URL. Default: {DEFAULT_BASE_URL}",
    )
    parser.add_argument(
        "--socket-url",
        default=DEFAULT_SOCKET_URL,
        help=f"WebSocket base URL. Default: {DEFAULT_SOCKET_URL}",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="HTTP timeout in seconds. Default: 15",
    )
    parser.add_argument(
        "--skip-devices",
        action="store_true",
        help="Only perform login and getInfo; skip device/deviceList.",
    )
    parser.add_argument(
        "--skip-status",
        action="store_true",
        default=True,
        help="Skip the charger status flow after deviceList. Default: enabled.",
    )
    parser.add_argument(
        "--status",
        dest="skip_status",
        action="store_false",
        help="Enable the charger status flow after deviceList.",
    )
    parser.add_argument(
        "--device-index",
        type=int,
        default=0,
        help="Zero-based index into deviceList for the status probe. Default: 0",
    )
    parser.add_argument(
        "--detail-poll-count",
        type=int,
        default=8,
        help="How many times to poll /device/getPortDetail when data is not ready. Default: 8",
    )
    parser.add_argument(
        "--detail-poll-interval",
        type=float,
        default=1.0,
        help="Seconds between /device/getPortDetail polls. Default: 1.0",
    )
    parser.add_argument(
        "--listen-status",
        action="store_true",
        help="Open the cloud WebSocket and wait for live charger status messages.",
    )
    parser.add_argument(
        "--start-charge",
        action="store_true",
        help="Call the cloud startCharge endpoint for the selected device.",
    )
    parser.add_argument(
        "--stop-charge",
        action="store_true",
        help="Call the cloud stopCharge endpoint for the selected device.",
    )
    parser.add_argument(
        "--port",
        type=int,
        help="Charging port number to use with --start-charge or --stop-charge.",
    )
    parser.add_argument(
        "--listen-seconds",
        type=float,
        default=10.0,
        help="How long to wait for WebSocket status messages. Default: 10",
    )
    parser.add_argument(
        "--ws-connect-timeout",
        type=float,
        default=10.0,
        help="WebSocket handshake timeout in seconds. Default: 10",
    )
    parser.add_argument(
        "--ws-read-timeout",
        type=float,
        default=15.0,
        help="WebSocket read timeout in seconds after connect. Default: 15",
    )
    parser.add_argument(
        "--ws-no-verify",
        action="store_true",
        help="Disable TLS certificate verification for WebSocket debugging.",
    )
    parser.add_argument(
        "--ws-trace",
        action="store_true",
        help="Enable websocket-client trace logging.",
    )
    parser.add_argument(
        "--raw-only",
        action="store_true",
        help="Print raw JSON responses only, without extra labels.",
    )
    return parser


def normalize_base_url(base_url: str) -> str:
    return base_url.rstrip("/")


def normalize_socket_url(socket_url: str) -> str:
    return socket_url.rstrip("/")


def request_json(
    method: str,
    url: str,
    timeout: float,
    payload: Optional[Dict[str, Any]] = None,
    token: Optional[str] = None,
) -> Tuple[int, Dict[str, str], Any]:
    body: Optional[bytes] = None
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }

    if token:
        headers["Authorization"] = f"Bearer {token}"

    if payload is not None:
        body = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(url=url, data=body, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            text = raw.decode(charset, errors="replace")
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = {"_raw": text}
            return resp.getcode(), dict(resp.headers.items()), parsed
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        text = raw.decode("utf-8", errors="replace")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = {"_raw": text}
        return exc.code, dict(exc.headers.items()), parsed


def pretty(title: str, value: Any, raw_only: bool) -> None:
    if raw_only:
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return

    print(f"\n=== {title} ===")
    print(json.dumps(value, ensure_ascii=False, indent=2))


def maybe_number(value: Any, digits: int = 2) -> Any:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value
    return round(number, digits)


def unwrap_data(value: Any) -> Any:
    if isinstance(value, dict) and "data" in value:
        return value["data"]
    return value


def normalize_device_payload(device: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(device)

    if "deviceId" not in payload:
        for candidate in ("imei", "device_id", "deviceid", "id"):
            if candidate in payload and payload[candidate]:
                payload["deviceId"] = payload[candidate]
                break

    if "qrcode" not in payload:
        for candidate in ("qrCode", "qr_code"):
            if candidate in payload and payload[candidate]:
                payload["qrcode"] = payload[candidate]
                break

    return payload


def body_data(value: Any) -> Any:
    if isinstance(value, dict):
        return value.get("data")
    return None


def summarize_status_payload(payload: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return None

    summary: Dict[str, Any] = {}
    voltage = payload.get("voltage")
    dev_temp = payload.get("dev_temper")

    if voltage not in (None, ""):
        summary["voltage_v"] = maybe_number(voltage)
    if dev_temp not in (None, ""):
        summary["device_temperature_c"] = maybe_number(dev_temp)

    for suffix, port_num in (("", 1), ("_1", 2)):
        status_key = "port_first_status" if port_num == 1 else "port_second_status"
        power_key = "power" if port_num == 1 else "power_1"
        elec_key = "elec" if port_num == 1 else "elec_1"
        time_key = "time" if port_num == 1 else "time_1"

        port_status = payload.get(status_key)
        power = payload.get(power_key)
        energy = payload.get(elec_key)
        charge_time = payload.get(time_key)

        if all(value in (None, "") for value in (port_status, power, energy, charge_time)):
            continue

        prefix = f"port_{port_num}"
        summary[f"{prefix}_status_code"] = port_status
        if port_status in PORT_STATUS_LABELS:
            summary[f"{prefix}_status"] = PORT_STATUS_LABELS[port_status]

        if power not in (None, ""):
            summary[f"{prefix}_power_w"] = maybe_number(power)
        if energy not in (None, ""):
            try:
                summary[f"{prefix}_energy_kwh"] = round(float(energy) / 100.0, 3)
            except (TypeError, ValueError):
                summary[f"{prefix}_energy_raw"] = energy
        if charge_time not in (None, ""):
            summary[f"{prefix}_charge_time_raw"] = charge_time

        current = None
        if power not in (None, "") and voltage not in (None, "", 0, "0"):
            try:
                current = round(float(power) / float(voltage), 2)
            except (TypeError, ValueError, ZeroDivisionError):
                current = None
        if current is not None:
            summary[f"{prefix}_current_a"] = current

    return summary or None


def print_status_summary(title: str, payload: Any, raw_only: bool) -> None:
    summary = summarize_status_payload(payload)
    if summary is not None:
        pretty(title, summary, raw_only)


def select_device(devices_data: Any, index: int) -> Optional[Dict[str, Any]]:
    devices = unwrap_data(devices_data)
    if not isinstance(devices, list):
        return None

    dict_devices = [item for item in devices if isinstance(item, dict)]
    if not dict_devices:
        return None

    if index < 0 or index >= len(dict_devices):
        raise IndexError(f"device index {index} out of range; found {len(dict_devices)} device objects")

    return dict_devices[index]


def extract_device_id(device_payload: Dict[str, Any]) -> Optional[str]:
    for key in ("deviceId", "imei", "id"):
        value = device_payload.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def listen_for_status(
    socket_url: str,
    device_payload: Dict[str, Any],
    token: str,
    connect_timeout: float,
    read_timeout: float,
    no_verify: bool,
    trace: bool,
    raw_only: bool,
) -> int:
    if websocket is None:
        if not raw_only:
            print(
                "\nWebSocket listener unavailable: install 'websocket-client' locally to enable --listen-status."
            )
            print("Suggested command: python3 -m pip install --user websocket-client")
        return 1

    device_id = extract_device_id(device_payload)
    if not device_id:
        if not raw_only:
            print("\nWebSocket listener skipped: selected device has no usable deviceId/imei/id.")
        return 1

    ws_url = f"{normalize_socket_url(socket_url)}/{quote(device_id, safe='')}/{quote(token, safe='')}"

    if not raw_only:
        print(f"\n=== WebSocket connect ===\n{ws_url}")

    if trace:
        websocket.enableTrace(True)  # type: ignore[union-attr]

    sslopt = {"cert_reqs": ssl.CERT_REQUIRED}
    if no_verify:
        sslopt = {"cert_reqs": ssl.CERT_NONE}

    ws = websocket.create_connection(  # type: ignore[union-attr]
        ws_url,
        timeout=connect_timeout,
        sslopt=sslopt,
    )

    try:
        ws.settimeout(read_timeout)
        while True:
            message = ws.recv()
            try:
                parsed = json.loads(message)
            except json.JSONDecodeError:
                parsed = {"_raw": message}

            pretty("WebSocket message", parsed, raw_only)
            if isinstance(parsed, dict) and parsed.get("type") == 103:
                print_status_summary("Decoded charger status", parsed.get("real_data"), raw_only)

            if isinstance(parsed, dict) and parsed.get("type") == 103:
                return 0
    except TimeoutError:
        if not raw_only:
            print("\nWebSocket listener timed out before a type 103 status message arrived.")
        return 1
    except Exception as exc:
        if not raw_only:
            print(f"\nWebSocket listener failed: {type(exc).__name__}: {exc}")
        return 1
    finally:
        ws.close()


def poll_port_detail(
    base_url: str,
    token: str,
    normalized_device: Dict[str, Any],
    visit_time: Any,
    timeout: float,
    poll_count: int,
    poll_interval: float,
    raw_only: bool,
) -> Any:
    port_detail_payload = dict(normalized_device)
    port_detail_payload["time"] = visit_time
    last_detail_data: Any = None

    for attempt in range(1, poll_count + 1):
        detail_status, detail_headers, detail_data = request_json(
            method="POST",
            url=f"{base_url}/device/getPortDetail",
            timeout=timeout,
            token=token,
            payload=port_detail_payload,
        )
        pretty(
            f"POST /device/getPortDetail (attempt {attempt})",
            {"status": detail_status, "body": detail_data},
            raw_only,
        )
        last_detail_data = detail_data

        detail_body_data = body_data(detail_data)
        if detail_body_data not in (None, ""):
            print_status_summary(
                f"Decoded charger status (attempt {attempt})",
                detail_body_data,
                raw_only,
            )
            return detail_data

        if attempt < poll_count:
            time.sleep(poll_interval)

    return last_detail_data


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.start_charge and args.stop_charge:
        parser.error("use only one of --start-charge or --stop-charge")
    if (args.start_charge or args.stop_charge) and args.port is None:
        parser.error("--port is required with --start-charge or --stop-charge")

    base_url = normalize_base_url(args.base_url)
    socket_url = normalize_socket_url(args.socket_url)

    if not args.token and not args.email:
        parser.error("provide either --token or --email")
    if args.token and args.email and not args.raw_only:
        print("\nUsing --token and skipping /mailLogin because a token was provided.")

    token = args.token
    if not token:
        password = args.password or getpass.getpass("Password: ")
        login_payload = {
            "username": args.email,
            "password": password,
        }

        login_status, login_headers, login_data = request_json(
            method="POST",
            url=f"{base_url}/mailLogin",
            timeout=args.timeout,
            payload=login_payload,
        )
        pretty("POST /mailLogin", {"status": login_status, "body": login_data}, args.raw_only)

        if isinstance(login_data, dict):
            token = login_data.get("token")

        if not token:
            if not args.raw_only:
                print("\nNo token returned by /mailLogin, so authenticated follow-up calls were skipped.")
            return 1

    info_status, info_headers, info_data = request_json(
        method="GET",
        url=f"{base_url}/getInfo",
        timeout=args.timeout,
        token=token,
    )
    pretty("GET /getInfo", {"status": info_status, "body": info_data}, args.raw_only)

    selected_device: Optional[Dict[str, Any]] = None
    normalized_device: Optional[Dict[str, Any]] = None

    if not args.skip_devices:
        devices_status, devices_headers, devices_data = request_json(
            method="POST",
            url=f"{base_url}/device/deviceList",
            timeout=args.timeout,
            token=token,
            payload={},
        )
        pretty(
            "POST /device/deviceList",
            {"status": devices_status, "body": devices_data},
            args.raw_only,
        )

        try:
            selected_device = select_device(devices_data, args.device_index)
        except IndexError as exc:
            if not args.raw_only:
                print(f"\nStatus probe skipped: {exc}")
            selected_device = None

        if selected_device is not None:
            normalized_device = normalize_device_payload(selected_device)
            pretty(
                "Selected device payload",
                normalized_device,
                args.raw_only,
            )

    if not args.skip_status and normalized_device is not None:
        send_status, send_headers, send_data = request_json(
            method="POST",
            url=f"{base_url}/device/sendPortDetailCmd",
            timeout=args.timeout,
            token=token,
            payload=normalized_device,
        )
        pretty(
            "POST /device/sendPortDetailCmd",
            {"status": send_status, "body": send_data},
            args.raw_only,
        )

        visit_time: Optional[Any] = None
        if isinstance(send_data, dict):
            visit_time = send_data.get("msg")
            if visit_time in ("", None):
                visit_time = send_data.get("data")

        if visit_time not in ("", None):
            detail_data = poll_port_detail(
                base_url=base_url,
                token=token,
                normalized_device=normalized_device,
                visit_time=visit_time,
                timeout=args.timeout,
                poll_count=args.detail_poll_count,
                poll_interval=args.detail_poll_interval,
                raw_only=args.raw_only,
            )

            port_detail_payload = dict(normalized_device)
            port_detail_payload["time"] = visit_time
            encoded_visit_time = urllib.parse.quote(str(visit_time), safe="")

            status_change_status, status_change_headers, status_change_data = request_json(
                method="POST",
                url=f"{base_url}/device/statusChange?time={encoded_visit_time}",
                timeout=args.timeout,
                token=token,
                payload=port_detail_payload,
            )
            pretty(
                "POST /device/statusChange",
                {"status": status_change_status, "body": status_change_data},
                args.raw_only,
            )
        elif not args.raw_only:
            print("\nStatus follow-up skipped: /device/sendPortDetailCmd did not return a usable visit time.")

    if args.listen_status and normalized_device is not None:
        listen_for_status(
            socket_url=socket_url,
            device_payload=normalized_device,
            token=token,
            connect_timeout=args.ws_connect_timeout,
            read_timeout=args.ws_read_timeout,
            no_verify=args.ws_no_verify,
            trace=args.ws_trace,
            raw_only=args.raw_only,
        )

    if normalized_device is not None and (args.start_charge or args.stop_charge):
        charge_payload = dict(normalized_device)
        charge_payload["port"] = args.port

        pretty(
            "Charge command payload",
            charge_payload,
            args.raw_only,
        )

        if args.start_charge:
            charge_status, charge_headers, charge_data = request_json(
                method="POST",
                url=f"{base_url}/device/startCharge",
                timeout=args.timeout,
                token=token,
                payload=charge_payload,
            )
            pretty(
                "POST /device/startCharge",
                {"status": charge_status, "body": charge_data},
                args.raw_only,
            )
        elif args.stop_charge:
            charge_status, charge_headers, charge_data = request_json(
                method="POST",
                url=f"{base_url}/device/stopCharge",
                timeout=args.timeout,
                token=token,
                payload=charge_payload,
            )
            pretty(
                "POST /device/stopCharge",
                {"status": charge_status, "body": charge_data},
                args.raw_only,
            )

    if not args.raw_only:
        print("\nToken preview:", f"{token[:16]}..." if len(token) > 16 else token)

    return 0


if __name__ == "__main__":
    sys.exit(main())
