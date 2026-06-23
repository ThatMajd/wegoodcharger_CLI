from __future__ import annotations

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import typer

from .redaction import redact


class DebugLogger:
    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled

    def log(self, message: str, **fields: Any) -> None:
        if not self.enabled:
            return

        if fields:
            safe_fields = redact(fields)
            rendered = json.dumps(safe_fields, ensure_ascii=False, sort_keys=True)
            typer.echo(f"[debug] {message} {rendered}", err=True)
        else:
            typer.echo(f"[debug] {message}", err=True)

    @contextmanager
    def timed(self, message: str, **fields: Any) -> Iterator[None]:
        start = time.monotonic()
        self.log(f"{message}: start", **fields)
        try:
            yield
        finally:
            elapsed_ms = round((time.monotonic() - start) * 1000, 1)
            self.log(f"{message}: finished", elapsed_ms=elapsed_ms, **fields)
