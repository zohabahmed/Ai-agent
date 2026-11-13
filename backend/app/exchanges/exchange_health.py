"""Exchange connectivity and health monitoring with circuit breaker."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.exchanges.base_exchange import BaseExchange


@dataclass
class ExchangeHealthStatus:
    exchange_id: str
    is_healthy: bool
    latency_ms: float | None
    consecutive_failures: int
    last_checked_at: datetime
    last_error: str | None = None
    circuit_open: bool = False


class ExchangeHealthMonitor:
    """Monitors exchange API reachability and trips a circuit breaker on repeated failures."""

    def __init__(self, failure_threshold: int = 3) -> None:
        self.failure_threshold = failure_threshold
        self._consecutive_failures = 0
        self._last_status: ExchangeHealthStatus | None = None

    @property
    def status(self) -> ExchangeHealthStatus | None:
        return self._last_status

    async def check(self, exchange: BaseExchange) -> ExchangeHealthStatus:
        now = datetime.now(timezone.utc)
        try:
            latency = await exchange.ping()
            self._consecutive_failures = 0
            self._last_status = ExchangeHealthStatus(
                exchange_id=exchange.exchange_id,
                is_healthy=True,
                latency_ms=round(latency, 2),
                consecutive_failures=0,
                last_checked_at=now,
                last_error=None,
                circuit_open=False,
            )
        except Exception as exc:
            self._consecutive_failures += 1
            circuit_open = self._consecutive_failures >= self.failure_threshold
            self._last_status = ExchangeHealthStatus(
                exchange_id=exchange.exchange_id,
                is_healthy=False,
                latency_ms=None,
                consecutive_failures=self._consecutive_failures,
                last_checked_at=now,
                last_error=str(exc) or type(exc).__name__,
                circuit_open=circuit_open,
            )
        return self._last_status

    def record_failure(self, exchange_id: str, error_msg: str) -> ExchangeHealthStatus:
        self._consecutive_failures += 1
        self._last_status = ExchangeHealthStatus(
            exchange_id=exchange_id,
            is_healthy=False,
            latency_ms=None,
            consecutive_failures=self._consecutive_failures,
            last_checked_at=datetime.now(timezone.utc),
            last_error=error_msg,
            circuit_open=self._consecutive_failures >= self.failure_threshold,
        )
        return self._last_status

    def reset(self) -> None:
        self._consecutive_failures = 0
