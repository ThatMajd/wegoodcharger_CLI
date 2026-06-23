from __future__ import annotations

import getpass
import json
from typing import Any

import typer

from .auth import AuthStore
from .client import ApiError, CloudClient, DEFAULT_BASE_URL, TokenExpired
from .config import Config, load_config, save_config
from .debug import DebugLogger
from .domain import DeviceRef, device_list_from_response, select_device, summarize_status_payload
from .redaction import redact


app = typer.Typer(no_args_is_help=True, help="Read-only cloud CLI for WeGood EV chargers.")


class AppState:
    debug = DebugLogger(False)
    base_url = DEFAULT_BASE_URL
    timeout = 15.0


state = AppState()


@app.callback()
def main(
    debug: bool = typer.Option(False, "--debug", help="Print redacted HTTP diagnostics to stderr."),
    base_url: str = typer.Option(DEFAULT_BASE_URL, "--base-url", help="Cloud API base URL."),
    timeout: float = typer.Option(15.0, "--timeout", help="HTTP timeout in seconds."),
) -> None:
    state.debug = DebugLogger(debug)
    state.base_url = base_url.rstrip("/")
    state.timeout = timeout


@app.command()
def login(
    email: str = typer.Option(..., "--email", "-e", prompt=True, help="WeGood account email."),
    debug: bool = typer.Option(False, "--debug", help="Print redacted HTTP diagnostics to stderr."),
) -> None:
    """Log in and store the returned token in the system keyring."""
    apply_command_debug(debug)
    password = getpass.getpass("Password: ")
    client = CloudClient(state.base_url, state.timeout, state.debug)

    try:
        token = client.login(email, password)
    except ApiError as exc:
        fail(f"Login failed: {exc}", exc)

    AuthStore().set_token(email, token)
    config = load_config()
    config.email = email
    config.base_url = state.base_url
    save_config(config)
    typer.echo("Logged in. Token stored in the system keyring.")


@app.command()
def devices(
    json_output: bool = typer.Option(False, "--json", help="Print devices as JSON."),
    debug: bool = typer.Option(False, "--debug", help="Print redacted HTTP diagnostics to stderr."),
) -> None:
    """List cloud devices associated with the account."""
    apply_command_debug(debug)
    config, token = require_auth()
    client = CloudClient(config.base_url, state.timeout, state.debug)

    try:
        response = client.device_list(token)
    except TokenExpired as exc:
        handle_expired_token(config, exc)
    except ApiError as exc:
        fail("Could not fetch devices.", exc)

    device_refs = device_list_from_response(response)
    if json_output:
        echo_json([device.to_config() for device in device_refs])
        return

    if not device_refs:
        typer.echo("No devices found.")
        return

    if len(device_refs) == 1 and config.default_device is None:
        config.default_device = device_refs[0]
        save_config(config)
        typer.echo(f"Only one charger found; default device set to {config.default_device.device_id}.")

    for index, device in enumerate(device_refs):
        parts = [f"[{index}]", device.device_id]
        if device.qrcode:
            parts.append(f"qrcode={device.qrcode}")
        if device.ccid:
            parts.append(f"ccid={device.ccid}")
        typer.echo("  ".join(parts))


@app.command("use-device")
def use_device(
    selector: str = typer.Argument(..., help="Device index, device id, qrcode, or ccid."),
    debug: bool = typer.Option(False, "--debug", help="Print redacted HTTP diagnostics to stderr."),
) -> None:
    """Save the default device used by status."""
    apply_command_debug(debug)
    config, token = require_auth()
    client = CloudClient(config.base_url, state.timeout, state.debug)

    try:
        response = client.device_list(token)
    except TokenExpired as exc:
        handle_expired_token(config, exc)
    except ApiError as exc:
        fail("Could not fetch devices.", exc)

    device_refs = device_list_from_response(response)
    try:
        config.default_device = select_device(device_refs, selector)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    save_config(config)
    typer.echo(f"Default device set to {config.default_device.device_id}.")


@app.command()
def status(
    device: str | None = typer.Option(None, "--device", help="Device index, device id, qrcode, or ccid override."),
    json_output: bool = typer.Option(False, "--json", help="Print decoded status as JSON."),
    poll_count: int = typer.Option(8, "--poll-count", min=1, help="Status poll attempts."),
    poll_interval: float = typer.Option(1.0, "--poll-interval", min=0.0, help="Seconds between status polls."),
    debug: bool = typer.Option(False, "--debug", help="Print redacted HTTP diagnostics to stderr."),
) -> None:
    """Fetch one read-only charger status snapshot."""
    apply_command_debug(debug)
    config, token = require_auth()
    client = CloudClient(config.base_url, state.timeout, state.debug)

    selected = config.default_device
    if device is not None:
        try:
            response = client.device_list(token)
            selected = select_device(device_list_from_response(response), device)
        except TokenExpired as exc:
            handle_expired_token(config, exc)
        except (ApiError, ValueError) as exc:
            fail(f"Could not select device {device!r}.", exc)

    if selected is None:
        selected = auto_select_single_device(config, token, client, json_output=json_output)

    try:
        result = client.poll_status(
            token,
            selected.to_payload(),
            poll_count=poll_count,
            poll_interval=poll_interval,
        )
    except TokenExpired as exc:
        handle_expired_token(config, exc)
    except ApiError as exc:
        fail("Could not fetch charger status.", exc)

    if json_output:
        echo_json(
            {
                "device_id": selected.device_id,
                "summary": result["summary"],
                "raw": result["raw"],
                "partial_success": result.get("partial_success", False),
                "message": result.get("message"),
            }
        )
        return

    print_human_status(selected.device_id, result["summary"], result["raw"], result.get("message"))


def auto_select_single_device(config: Config, token: str, client: CloudClient, *, json_output: bool) -> DeviceRef:
    try:
        response = client.device_list(token)
    except TokenExpired as exc:
        handle_expired_token(config, exc)
    except ApiError as exc:
        fail("Could not fetch devices.", exc)

    device_refs = device_list_from_response(response)
    if len(device_refs) == 1:
        config.default_device = device_refs[0]
        save_config(config)
        if not json_output:
            typer.echo(f"Only one charger found; default device set to {config.default_device.device_id}.")
        return config.default_device

    if not device_refs:
        raise typer.BadParameter("No chargers found on this account.")
    raise typer.BadParameter("No default device set. Run `wegoodcharger-cli devices` then `wegoodcharger-cli use-device <index>`.")


def require_auth() -> tuple[Config, str]:
    config = load_config()
    if not config.email:
        raise typer.BadParameter("No account email saved. Run `wegoodcharger-cli login --email <email>`.")

    token = AuthStore().get_token(config.email)
    if not token:
        raise typer.BadParameter(f"No stored token for {config.email}. Run `wegoodcharger-cli login --email {config.email}`.")

    return config, token


def apply_command_debug(debug: bool) -> None:
    if debug:
        state.debug = DebugLogger(True)


def handle_expired_token(config: Config, exc: TokenExpired) -> None:
    if config.email:
        AuthStore().clear_token(config.email)
        fail(
            f"Session expired. Run `wegoodcharger-cli login --email {config.email}` and try again.",
            exc,
            exit_code=3,
        )
    fail("Session expired. Run `wegoodcharger-cli login --email <email>` and try again.", exc, exit_code=3)


def fail(message: str, exc: BaseException | None = None, exit_code: int = 1) -> None:
    state.debug.log("command failed", detail=message, error=str(exc) if exc else None)
    typer.echo(message, err=True)
    if isinstance(exc, ApiError) and exc.body is not None and state.debug.enabled:
        typer.echo(json.dumps(redact(exc.body), ensure_ascii=False, indent=2), err=True)
    raise typer.Exit(exit_code)


def echo_json(value: Any) -> None:
    typer.echo(json.dumps(redact(value), ensure_ascii=False, indent=2))


def print_human_status(device_id: str, summary: dict[str, Any], raw: Any, message: str | None = None) -> None:
    typer.echo(f"Device: {device_id}")
    if message:
        typer.echo(message)
    if not summary:
        fallback = summarize_status_payload(raw) or {}
        summary = fallback
    if not summary:
        typer.echo("No decoded status fields were returned.")
        return

    for key in sorted(summary):
        typer.echo(f"{key}: {summary[key]}")
