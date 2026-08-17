"""The live runner: preflight, candle aggregation, and the gate holding.

Nothing here places an order. The point of these tests is the opposite — that
the things which must be true before an order is even conceivable are checked,
and that the last one of them is the deployment gate.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from earner.trading.live import CandleBuilder, WARMUP_CANDLES, preflight


@pytest.fixture
def credentialled(cfg):
    return replace(cfg, kotak_consumer_key="ck", kotak_mobile="+919000000000",
                   kotak_ucc="ABC12")


# ── preflight ───────────────────────────────────────────────────────────────

def test_preflight_names_every_missing_credential(cfg, monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "LIVE")
    monkeypatch.setenv("LIVE_TRADING_CONFIRMATION", "I_UNDERSTAND_REAL_MONEY")
    problems = " | ".join(preflight(cfg, ["RELIANCE"]))

    assert "KOTAK_CONSUMER_KEY" in problems
    assert "KOTAK_MOBILE" in problems
    assert "KOTAK_UCC" in problems


def test_preflight_refuses_without_the_confirmation(credentialled, monkeypatch):
    """Credentials present, mode LIVE, confirmation missing. Still refused."""
    monkeypatch.setenv("TRADING_MODE", "LIVE")
    monkeypatch.delenv("LIVE_TRADING_CONFIRMATION", raising=False)

    problems = preflight(credentialled, ["RELIANCE"])
    assert any("gate is shut" in p for p in problems)


def test_preflight_refuses_a_wrong_confirmation_phrase(credentialled, monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "LIVE")
    monkeypatch.setenv("LIVE_TRADING_CONFIRMATION", "yes I am sure")

    assert any("gate is shut" in p for p in preflight(credentialled, ["RELIANCE"]))


def test_preflight_refuses_an_empty_universe(credentialled, monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "LIVE")
    monkeypatch.setenv("LIVE_TRADING_CONFIRMATION", "I_UNDERSTAND_REAL_MONEY")

    assert any("no symbols" in p for p in preflight(credentialled, []))


def test_run_live_refuses_before_it_touches_the_network(credentialled, monkeypatch):
    """The refusal has to come first — after connect() it is already too late."""
    from earner.trading.live import run_live

    monkeypatch.setenv("TRADING_MODE", "PAPER")
    with pytest.raises(RuntimeError, match="cannot go live"):
        run_live(credentialled, universe=["RELIANCE"], capital=100_000.0)


# ── candle aggregation ──────────────────────────────────────────────────────

# A true minute boundary. Picking a round-looking number is not the same thing:
# 1,000,000 is 40 seconds into a bar, which silently splits a test across two.
BASE = 1_000_020
assert BASE % 60 == 0


def test_quotes_are_bucketed_into_one_minute_candles():
    builder = CandleBuilder()
    for offset, price in ((0, 100.0), (15, 102.0), (30, 99.0), (45, 101.0)):
        builder.add("RELIANCE", price, now=BASE + offset)
    builder.add("RELIANCE", 105.0, now=BASE + 60)      # next minute

    closed = builder.candles["RELIANCE"]
    assert len(closed) == 1
    assert closed[0].open == 100.0 and closed[0].close == 101.0
    assert closed[0].high == 102.0 and closed[0].low == 99.0


def test_the_bar_in_progress_is_visible_to_the_engine():
    """Waiting for a bar to close before reacting is waiting up to 60 seconds
    with a live stop."""
    builder = CandleBuilder()
    builder.add("TCS", 100.0, now=BASE)
    builder.add("TCS", 101.0, now=BASE + 30)

    market = builder.market()
    assert len(market["TCS"]) == 1
    assert market["TCS"][-1].close == 101.0
    assert builder.candles.get("TCS", []) == [], "nothing has closed yet"


def test_volume_comes_from_the_difference_in_cumulative_volume():
    """The exchange reports the day's running total; the bar wants the delta."""
    builder = CandleBuilder()
    builder.add("TCS", 100.0, cumulative_volume=10_000, now=BASE)
    builder.add("TCS", 100.5, cumulative_volume=12_500, now=BASE + 10)
    builder.add("TCS", 101.0, cumulative_volume=13_000, now=BASE + 20)

    assert builder.market()["TCS"][-1].volume == 3_000     # 2,500 + 500


def test_the_first_quote_contributes_no_volume():
    """There is no previous total to subtract, so inventing one would be a lie."""
    builder = CandleBuilder()
    builder.add("TCS", 100.0, cumulative_volume=99_999, now=BASE)
    assert builder.market()["TCS"][-1].volume == 0.0


def test_a_volume_counter_that_goes_backwards_does_not_go_negative():
    builder = CandleBuilder()
    builder.add("TCS", 100.0, cumulative_volume=10_000, now=BASE)
    builder.add("TCS", 100.0, cumulative_volume=9_000, now=BASE + 10)
    assert builder.market()["TCS"][-1].volume >= 0


def test_warmup_is_not_declared_until_there_is_enough_history():
    builder = CandleBuilder()
    for minute in range(WARMUP_CANDLES):
        builder.add("TCS", 100.0 + minute, now=BASE + minute * 60)

    assert builder.ready("TCS") is False, "the final bar is still open"
    builder.add("TCS", 140.0, now=BASE + WARMUP_CANDLES * 60)
    assert builder.ready("TCS") is True


def test_symbols_are_aggregated_independently():
    builder = CandleBuilder()
    builder.add("TCS", 100.0, now=BASE)
    builder.add("RELIANCE", 1_400.0, now=BASE)
    builder.add("TCS", 101.0, now=BASE + 10)

    market = builder.market()
    assert market["TCS"][-1].close == 101.0
    assert market["RELIANCE"][-1].close == 1_400.0
