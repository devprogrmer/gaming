from __future__ import annotations

from gaming.interactive.classify import (
    BAD,
    GOOD,
    MEDIUM,
    ProbeResult,
    classify,
    summarize,
)
from gaming.interactive.settings import Settings


def _settings() -> Settings:
    # Explicit defaults so the test is independent of any saved settings file.
    return Settings(
        good_latency_ms=80.0,
        good_loss_pct=10.0,
        medium_latency_ms=200.0,
        medium_loss_pct=40.0,
    )


def test_probe_result_derived_fields():
    p = ProbeResult("1.2.3.4", sent=4, received=3, avg_ms=50.0)
    assert p.reachable is True
    assert round(p.loss_pct, 2) == 25.0

    dead = ProbeResult("1.2.3.4", sent=4, received=0)
    assert dead.reachable is False
    assert dead.loss_pct == 100.0


def test_classify_good():
    p = ProbeResult("1.2.3.4", sent=4, received=4, avg_ms=30.0)
    assert classify(p, _settings()) == GOOD


def test_classify_medium_by_latency():
    p = ProbeResult("1.2.3.4", sent=4, received=4, avg_ms=150.0)
    assert classify(p, _settings()) == MEDIUM


def test_classify_medium_by_moderate_loss():
    # 25% loss, low latency -> not GOOD (loss > good), still within MEDIUM.
    p = ProbeResult("1.2.3.4", sent=4, received=3, avg_ms=40.0)
    assert classify(p, _settings()) == MEDIUM


def test_classify_bad_high_loss():
    # 3/4 lost = 75% loss -> BAD regardless of latency.
    p = ProbeResult("1.2.3.4", sent=4, received=1, avg_ms=20.0)
    assert classify(p, _settings()) == BAD


def test_classify_bad_unreachable():
    p = ProbeResult("1.2.3.4", sent=4, received=0)
    assert classify(p, _settings()) == BAD


def test_classify_bad_slow_beyond_medium():
    p = ProbeResult("1.2.3.4", sent=4, received=4, avg_ms=500.0)
    assert classify(p, _settings()) == BAD


def test_summarize_counts_all_labels():
    counts = summarize([GOOD, GOOD, MEDIUM, BAD, "GARBAGE"])
    assert counts == {GOOD: 2, MEDIUM: 1, BAD: 1}


def test_settings_clamped_orders_thresholds():
    s = Settings(good_latency_ms=100.0, medium_latency_ms=50.0).clamped()
    # medium must be strictly above good after clamping.
    assert s.medium_latency_ms > s.good_latency_ms
