"""Jinja2 environment.

Autoescaping is on for every template; audit payloads are rendered from
untrusted-ish agent output later in the project, so escaping is not optional.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi.templating import Jinja2Templates

TEMPLATE_DIR = Path(__file__).parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


def format_timestamp(value: datetime | None) -> str:
    if value is None:
        return "—"
    return value.strftime("%Y-%m-%d %H:%M:%SZ")


def pretty_json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, default=str)


def short_hash(value: str | None) -> str:
    return "—" if not value else f"{value[:8]}…"


templates.env.filters["timestamp"] = format_timestamp
templates.env.filters["pretty_json"] = pretty_json
templates.env.filters["short_hash"] = short_hash
templates.env.autoescape = True
