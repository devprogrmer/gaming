"""CSV export."""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Iterable

from ..models import IPRecord


def _row(rec: IPRecord) -> dict:
    d = rec.to_dict()
    d["open_ports"] = ";".join(str(p) for p in rec.open_ports)
    return {k: ("" if d.get(k) is None else d.get(k)) for k in IPRecord.FIELDS}


def to_csv(records: Iterable[IPRecord]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(IPRecord.FIELDS))
    writer.writeheader()
    for rec in records:
        writer.writerow(_row(rec))
    return buf.getvalue()


def write_csv(records: Iterable[IPRecord], path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(to_csv(records), encoding="utf-8")
    return p
