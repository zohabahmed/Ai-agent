"""Market data validation: duplicate prevention, gap detection, OHLCV sanity, stale checks."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from app.exchanges.base_exchange import Ticker

TIMEFRAME_SECONDS: dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}


@dataclass
class ValidationReport:
    is_valid: bool
    valid_candles: list[dict[str, Any]]
    duplicates_removed: int = 0
    missing_gaps: list[datetime] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class MarketDataValidator:
    """Validates OHLCV series, timestamps, ticker freshness, and bid/ask spread."""

    def __init__(self, stale_max_seconds: int = 120, max_spread_bps: Decimal = Decimal("35.0")) -> None:
        self.stale_max_seconds = stale_max_seconds
        self.max_spread_bps = max_spread_bps

    def validate_candles(
        self,
        candles: list[dict[str, Any]],
        timeframe: str,
        *,
        now: datetime | None = None,
        allow_historical: bool = False,
    ) -> ValidationReport:
        if not candles:
            return ValidationReport(is_valid=False, valid_candles=[], errors=["Empty candle dataset."])

        step_sec = TIMEFRAME_SECONDS.get(timeframe)
        if step_sec is None:
            return ValidationReport(is_valid=False, valid_candles=[], errors=[f"Unsupported timeframe: {timeframe}"])

        seen_ts: set[datetime] = set()
        duplicates = 0
        valid: list[dict[str, Any]] = []
        errors: list[str] = []
        ref_now = now or datetime.now(timezone.utc)

        for c in sorted(candles, key=lambda x: x["timestamp"]):
            ts: datetime = c["timestamp"]
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            else:
                ts = ts.astimezone(timezone.utc)

            if ts in seen_ts:
                duplicates += 1
                continue
            seen_ts.add(ts)

            if ts > ref_now + timedelta(seconds=step_sec):
                errors.append(f"Future candle timestamp detected: {ts.isoformat()}")
                continue

            o = Decimal(str(c["open"]))
            h = Decimal(str(c["high"]))
            l = Decimal(str(c["low"]))
            cl = Decimal(str(c["close"]))
            v = Decimal(str(c["volume"]))

            if min(o, h, l, cl) <= 0 or v < 0:
                errors.append(f"Invalid non-positive price or negative volume at {ts.isoformat()}")
                continue
            if h < max(o, cl) or l > min(o, cl) or h < l:
                errors.append(f"OHLC structural violation at {ts.isoformat()}")
                continue

            valid.append(
                {
                    "timestamp": ts,
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": cl,
                    "volume": v,
                    "is_validated": True,
                }
            )

        if not valid:
            return ValidationReport(
                is_valid=False,
                valid_candles=[],
                duplicates_removed=duplicates,
                errors=errors or ["No valid candles remained after filtering."],
            )

        missing_gaps: list[datetime] = []
        step = timedelta(seconds=step_sec)
        for i in range(1, len(valid)):
            prev_ts = valid[i - 1]["timestamp"]
            curr_ts = valid[i]["timestamp"]
            diff = (curr_ts - prev_ts).total_seconds()
            if diff > step_sec * 1.5:
                gap_ts = prev_ts + step
                while gap_ts < curr_ts:
                    missing_gaps.append(gap_ts)
                    gap_ts += step

        if not allow_historical:
            latest_ts = valid[-1]["timestamp"]
            # A candle is considered stale if it is older than 2 timeframes + stale_max_seconds
            max_age = step_sec + self.stale_max_seconds
            if (ref_now - latest_ts).total_seconds() > max_age:
                errors.append(
                    f"Latest {timeframe} candle ({latest_ts.isoformat()}) is stale relative to {ref_now.isoformat()}."
                )

        is_valid = len(errors) == 0
        return ValidationReport(
            is_valid=is_valid,
            valid_candles=valid,
            duplicates_removed=duplicates,
            missing_gaps=missing_gaps,
            errors=errors,
        )

    def validate_ticker(self, ticker: Ticker, *, now: datetime | None = None) -> tuple[bool, list[str]]:
        errors: list[str] = []
        ref_now = now or datetime.now(timezone.utc)
        ts = ticker.timestamp if ticker.timestamp.tzinfo else ticker.timestamp.replace(tzinfo=timezone.utc)
        age = (ref_now - ts).total_seconds()
        if age > self.stale_max_seconds:
            errors.append(f"Stale ticker for {ticker.symbol}: age {age:.1f}s > {self.stale_max_seconds}s")
        if age < -30:
            errors.append(f"Ticker timestamp in future for {ticker.symbol}")
        if ticker.last <= 0 or ticker.bid <= 0 or ticker.ask <= 0:
            errors.append(f"Invalid zero/negative quote prices for {ticker.symbol}")
        if ticker.ask < ticker.bid:
            errors.append(f"Crossed book (ask < bid) for {ticker.symbol}")
        if ticker.spread_bps > self.max_spread_bps:
            errors.append(
                f"Spread too wide for {ticker.symbol}: {ticker.spread_bps:.2f} bps > {self.max_spread_bps} bps"
            )
        return len(errors) == 0, errors
