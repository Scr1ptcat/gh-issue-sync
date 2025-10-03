from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from typing import Any, Dict

REDACTIONS = {"authorization", "proxy-authorization"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base: Dict[str, Any] = {
            "level": record.levelname,
            "time": int(time.time() * 1000),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if hasattr(record, "extra") and isinstance(record.extra, dict):
            base.update(record.extra)
        return json.dumps(base, separators=(",", ":"))


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)


def redact_headers(headers: Dict[str, str]) -> Dict[str, str]:
    return {
        k: ("<redacted>" if k.lower() in REDACTIONS else v) for k, v in headers.items()
    }


def new_request_id() -> str:
    return str(uuid.uuid4())
