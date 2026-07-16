"""JSON export."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from ..models import IPRecord


def to_json(records: Iterable[IPRecord], *, indent: int = 2) -> str:
    payload = [r.to_dict() for r in records]
    return json.dumps(payload, indent=indent, ensure_ascii=False)


def write_json(records: Iterable[IPRecord], path: str | Path, *, indent: int = 2) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(to_json(records, indent=indent), encoding="utf-8")
    return p
