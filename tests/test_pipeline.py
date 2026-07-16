from __future__ import annotations

from gaming.config import load_config
from gaming.pipeline import discover, process, run_pipeline


def test_discover_offline_returns_records():
    cfg = load_config(None)
    cfg.discovery["offline"] = True
    records = discover(cfg)
    assert len(records) > 0
    # sources ran concurrently and merged
    sources = {r.source for r in records}
    assert sources


def test_process_dedups_and_filters():
    from gaming.models import Filters, IPRecord

    raw = [
        IPRecord(prefix="1.2.3.0/24", source="a", country="IR"),
        IPRecord(prefix="1.2.3.0/24", source="b", country="IR"),
        IPRecord(prefix="9.9.9.0/24", source="c", country="US"),
    ]
    out = process(raw, Filters(countries=["IR"]))
    assert len(out) == 1
    assert out[0].source == "a+b"


def test_run_pipeline_offline_no_reachability():
    cfg = load_config(None)
    cfg.discovery["offline"] = True
    cfg.reachability["enabled"] = False
    cfg.global_check["enabled"] = False
    records = run_pipeline(cfg)
    assert records
    # reachability disabled -> alive stays None
    assert all(r.alive is None for r in records)
