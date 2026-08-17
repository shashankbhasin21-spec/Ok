"""Market data parsing and backtest arithmetic.

No network here — the parsing is exercised against captured response shapes, so
the tests stay deterministic. What matters is that the harness cannot flatter a
result: gaps must not be filled, bars outside the session must not be traded,
and the reported P&L must be after costs.
"""

from __future__ import annotations

from datetime import datetime

from earner.trading.backtest import BacktestResult, DayResult
from earner.trading.risk import IST
from earner.trading.marketdata import _to_candles, by_day


def yahoo(stamps, opens, highs, lows, closes, volumes=None) -> dict:
    return {
        "timestamp": stamps,
        "indicators": {"quote": [{
            "open": opens, "high": highs, "low": lows, "close": closes,
            "volume": volumes if volumes is not None else [1_000] * len(stamps),
        }]},
    }


# 09:15 IST on a weekday. Computed rather than typed: an epoch guessed by hand
# lands mid-session or outside it, and the test then measures the wrong thing.
OPEN_IST = int(datetime(2026, 8, 17, 9, 15, tzinfo=IST).timestamp())


# ── parsing ─────────────────────────────────────────────────────────────────

def test_real_bars_become_candles():
    candles = _to_candles(yahoo([OPEN_IST], [100.0], [101.0], [99.5], [100.5], [12_345]))
    assert len(candles) == 1
    assert candles[0].open == 100.0 and candles[0].high == 101.0
    assert candles[0].close == 100.5 and candles[0].volume == 12_345


def test_a_gap_is_dropped_not_filled():
    """A forward-filled bar is a price nobody traded at. A strategy tested
    against one is being tested against fiction."""
    data = yahoo([OPEN_IST, OPEN_IST + 60, OPEN_IST + 120],
                 [100.0, None, 102.0], [101.0, None, 103.0],
                 [99.0, None, 101.0], [100.5, None, 102.5])
    assert len(_to_candles(data)) == 2


def test_missing_volume_is_zero_not_invented():
    candles = _to_candles(yahoo([OPEN_IST], [100.0], [100.0], [100.0], [100.0], [None]))
    assert candles[0].volume == 0.0


def test_ragged_arrays_do_not_crash():
    data = yahoo([OPEN_IST, OPEN_IST + 60], [100.0], [101.0], [99.0], [100.5])
    assert len(_to_candles(data)) == 1


# ── session filtering ───────────────────────────────────────────────────────

def test_only_regular_session_bars_are_kept():
    """Pre-open and post-close prints are not tradeable and must not produce signals."""
    stamps = [OPEN_IST - 3600, OPEN_IST, OPEN_IST + 3600, OPEN_IST + 8 * 3600]
    candles = _to_candles(yahoo(stamps, [100.0] * 4, [100.0] * 4, [100.0] * 4, [100.0] * 4))
    kept = by_day(candles)
    assert sum(len(v) for v in kept.values()) == 2, "08:15 and 17:15 are outside the session"


def test_days_are_kept_separate():
    stamps = [OPEN_IST, OPEN_IST + 86_400]
    candles = _to_candles(yahoo(stamps, [100.0] * 2, [100.0] * 2, [100.0] * 2, [100.0] * 2))
    assert len(by_day(candles)) == 2


# ── reporting ───────────────────────────────────────────────────────────────

def day(name, gross, costs, trades=10):
    return DayResult(day=name, gross=gross, costs=costs, net=gross - costs,
                     trades=trades, halted=False)


def test_the_headline_number_is_after_costs():
    result = BacktestResult(days=[day("d1", 1_000, 900), day("d2", 500, 900)],
                            capital=100_000)
    assert result.gross == 1_500
    assert result.costs == 1_800
    assert result.net == -300, "a gross profit can still be a net loss"


def test_win_rate_counts_net_days_not_gross_ones():
    """A day that made money on price and lost it to brokerage is a losing day."""
    result = BacktestResult(days=[day("d1", 800, 900), day("d2", 1_500, 900)],
                            capital=100_000)
    assert result.win_rate == 0.5


def test_days_without_trades_do_not_dilute_the_win_rate():
    result = BacktestResult(
        days=[day("d1", 1_500, 900), day("d2", 0, 0, trades=0)], capital=100_000)
    assert result.win_rate == 1.0
    assert len(result.traded_days) == 1


def test_drawdown_is_peak_to_trough_not_worst_day():
    """Three -1,000 days in a row is a -3,000 drawdown, not a -1,000 one."""
    result = BacktestResult(
        days=[day("d1", 1_000, 0), day("d2", 0, 1_000),
              day("d3", 0, 1_000), day("d4", 0, 1_000)], capital=100_000)
    assert result.max_drawdown == -3_000


def test_an_untraded_backtest_reports_zero_rather_than_dividing_by_zero():
    result = BacktestResult(days=[day("d1", 0, 0, trades=0)], capital=100_000)
    assert result.win_rate == 0.0
    assert result.max_drawdown == 0.0
