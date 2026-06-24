from __future__ import annotations

from dataclasses import dataclass
from typing import Any


PORT_STATUS_LABELS = {
    1: "plug_not_inserted",
    2: "charging",
    5: "ready_to_start",
    6: "scheduled",
    7: "cannot_schedule",
}


@dataclass(frozen=True)
class DeviceRef:
    device_id: str
    qrcode: str | None = None
    ccid: str | None = None
    raw: dict[str, Any] | None = None

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "deviceId": self.device_id,
            "ccid": self.ccid or self.device_id,
        }
        if self.qrcode:
            payload["qrcode"] = self.qrcode
        return payload

    def to_config(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "qrcode": self.qrcode,
            "ccid": self.ccid,
            "raw": self.raw or {},
        }

    @classmethod
    def from_config(cls, value: dict[str, Any]) -> "DeviceRef":
        return cls(
            device_id=str(value["device_id"]),
            qrcode=value.get("qrcode"),
            ccid=value.get("ccid"),
            raw=value.get("raw") if isinstance(value.get("raw"), dict) else None,
        )


def unwrap_data(value: Any) -> Any:
    if isinstance(value, dict) and "data" in value:
        return value["data"]
    return value


def maybe_number(value: Any, digits: int = 2) -> Any:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value
    return round(number, digits)


def normalize_device_payload(device: dict[str, Any]) -> dict[str, Any]:
    payload = dict(device)

    if "deviceId" not in payload:
        for candidate in ("imei", "device_id", "deviceid", "id"):
            if payload.get(candidate):
                payload["deviceId"] = payload[candidate]
                break

    if "qrcode" not in payload:
        for candidate in ("qrCode", "qr_code"):
            if payload.get(candidate):
                payload["qrcode"] = payload[candidate]
                break

    return payload


def device_ref_from_payload(device: dict[str, Any]) -> DeviceRef:
    payload = normalize_device_payload(device)
    device_id = payload.get("deviceId")
    if device_id in (None, ""):
        raise ValueError("device payload has no usable deviceId/imei/id")

    return DeviceRef(
        device_id=str(device_id),
        qrcode=str(payload["qrcode"]) if payload.get("qrcode") not in (None, "") else None,
        ccid=str(payload["ccid"]) if payload.get("ccid") not in (None, "") else None,
        raw=payload,
    )


def device_list_from_response(value: Any) -> list[DeviceRef]:
    devices = unwrap_data(value)
    if not isinstance(devices, list):
        return []

    refs: list[DeviceRef] = []
    for item in devices:
        if not isinstance(item, dict):
            continue
        try:
            refs.append(device_ref_from_payload(item))
        except ValueError:
            continue
    return refs


def select_device(devices: list[DeviceRef], selector: str) -> DeviceRef:
    try:
        index = int(selector)
    except ValueError:
        index = -1

    if 0 <= index < len(devices):
        return devices[index]

    for device in devices:
        if device.device_id == selector:
            return device
        if device.qrcode == selector:
            return device
        if device.ccid == selector:
            return device

    raise ValueError(f"device {selector!r} was not found")


def body_data(value: Any) -> Any:
    if isinstance(value, dict):
        return value.get("data")
    return None


def charge_records_from_response(value: Any) -> list[dict[str, Any]]:
    records = body_data(value)
    if not isinstance(records, list):
        return []

    formatted: list[dict[str, Any]] = []
    for item in records:
        if not isinstance(item, dict):
            continue
        formatted.append(format_charge_record(item))
    return formatted


def format_charge_record(record: dict[str, Any]) -> dict[str, Any]:
    formatted = dict(record)
    elec = formatted.get("elec")
    if elec not in (None, ""):
        try:
            formatted["elec_kwh"] = round(float(elec) / 100.0, 3)
        except (TypeError, ValueError):
            pass
    return formatted


def summarize_status_payload(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None

    summary: dict[str, Any] = {}
    voltage = payload.get("voltage")
    dev_temp = payload.get("dev_temper")

    if voltage not in (None, ""):
        summary["voltage_v"] = maybe_number(voltage)
    if dev_temp not in (None, ""):
        summary["device_temperature_c"] = maybe_number(dev_temp)

    for port_num in (1, 2):
        status_key = "port_first_status" if port_num == 1 else "port_second_status"
        power_key = "power" if port_num == 1 else "power_1"
        energy_key = "elec" if port_num == 1 else "elec_1"
        time_key = "time" if port_num == 1 else "time_1"

        port_status = payload.get(status_key)
        power = payload.get(power_key)
        energy = payload.get(energy_key)
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
