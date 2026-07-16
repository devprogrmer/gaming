from __future__ import annotations

import json

from gaming.models import IPRecord
from gaming.reporting import export
from gaming.reporting.console import render_console
from gaming.reporting.csv_export import to_csv
from gaming.reporting.json_export import to_json


def test_console_render_contains_data(sample_records):
    text = render_console(sample_records)
    assert "PREFIX" in text
    assert "185.143.232.0/22" in text
    assert "record(s)" in text


def test_console_empty():
    assert "No records" in render_console([])


def test_json_export_roundtrip(sample_records):
    text = to_json(sample_records)
    data = json.loads(text)
    assert len(data) == len(sample_records)
    assert data[0]["prefix"] == "185.143.232.0/22"
    assert "alive" in data[0]
    assert "global_reachable" in data[0]


def test_csv_export_header_and_rows(sample_records):
    text = to_csv(sample_records)
    lines = text.strip().splitlines()
    header = lines[0].split(",")
    for field in ("source", "asn", "prefix", "alive", "global_reachable", "notes"):
        assert field in header
    assert len(lines) == len(sample_records) + 1


def test_csv_open_ports_serialized():
    rec = IPRecord(prefix="1.2.3.0/24", open_ports=[80, 443])
    text = to_csv([rec])
    assert "80;443" in text


def test_export_dispatch(sample_records, tmp_path):
    out = tmp_path / "out.json"
    text = export(sample_records, "json", out)
    assert out.exists()
    assert json.loads(text)

    csv_out = tmp_path / "out.csv"
    export(sample_records, "csv", csv_out)
    assert csv_out.exists()
