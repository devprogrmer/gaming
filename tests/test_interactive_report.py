from __future__ import annotations

from gaming.interactive.report import _fmt_abroad, render_results
from gaming.interactive.storage import ResultRow


def _row(**over) -> ResultRow:
    base = dict(host="8.8.8.8", verdict="GOOD", avg_ms=10.0, loss_pct=0.0, sent=4, received=4)
    base.update(over)
    return ResultRow(**base)


def test_fmt_abroad_ok():
    row = _row(abroad_reachable=True, abroad_nodes_ok=3, abroad_nodes_total=4, abroad_status="ok")
    assert _fmt_abroad(row) == "OK (3/4)"


def test_fmt_abroad_fail():
    row = _row(abroad_reachable=False, abroad_nodes_ok=1, abroad_nodes_total=4, abroad_status="ok")
    assert _fmt_abroad(row) == "FAIL (1/4)"


def test_fmt_abroad_unavailable_distinct_from_not_checked():
    # Provider outage: distinct "unavailable" cell (Part D).
    down = _row(abroad_reachable=None, abroad_status="unavailable")
    assert _fmt_abroad(down) == "unavailable"
    # Genuinely not checked (abroad disabled / pre-migration row).
    skipped = _row(abroad_reachable=None, abroad_status=None)
    assert _fmt_abroad(skipped) == "not checked"


def test_render_results_shows_unavailable():
    rows = [_row(abroad_reachable=None, abroad_status="unavailable")]
    out = render_results(rows, stream=None)
    assert "unavailable" in out
