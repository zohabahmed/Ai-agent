"""Kelly Criterion Engine: Binary Kelly, Fractional Kelly, Uncertainty Adjustment, and Expected-Log-Growth Optimizer."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import math
from typing import Sequence

import numpy as np
from scipy.optimize import minimize_scalar

from app.config import KellyMode, KELLY_MULTIPLIERS


@dataclass(frozen=True)
class KellyResult:
    full_kelly: Decimal
    fractional_kelly: Decimal
    applied_multiplier: Decimal
    uncertainty_haircut: Decimal
    reward_risk_ratio: Decimal
    win_probability: Decimal
    expected_log_growth: float
    is_valid: bool
    reason: str


class KellyCalculator:
    """
    Mathematical Kelly Criterion implementation for survival-first sizing.
    Uses conservative fractional Kelly and rejects trades with <= 0 edge or excessive uncertainty.
    """

    def __init__(
        self,
        *,
        kelly_mode: KellyMode = KellyMode.CONSERVATIVE,
        max_risk_per_trade: Decimal = Decimal("0.0025"),
        max_uncertainty_width: float = 0.35,
    ) -> None:
        # Never enable aggressive mode automatically
        if kelly_mode == KellyMode.AGGRESSIVE:
            kelly_mode = KellyMode.NORMAL
        self.kelly_mode = kelly_mode
        self.multiplier = KELLY_MULTIPLIERS[kelly_mode]
        self.max_risk_per_trade = max_risk_per_trade
        self.max_uncertainty_width = max_uncertainty_width

    def calculate_binary(
        self,
        *,
        win_probability: float,
        reward_risk_ratio: float,
        uncertainty_width: float = 0.10,
        expected_cost_pct: float = 0.0,
        stop_distance_pct: float = 0.01,
    ) -> KellyResult:
        p = float(win_probability)
        b = float(reward_risk_ratio)
        if not (0.0 < p < 1.0) or b <= 0.0 or stop_distance_pct <= 0.0:
            return KellyResult(
                full_kelly=Decimal("0"),
                fractional_kelly=Decimal("0"),
                applied_multiplier=self.multiplier,
                uncertainty_haircut=Decimal("0"),
                reward_risk_ratio=Decimal(str(max(0.0, b))),
                win_probability=Decimal(str(max(0.0, min(1.0, p)))),
                expected_log_growth=0.0,
                is_valid=False,
                reason="Invalid probability, reward-to-risk ratio, or stop distance.",
            )

        # Adjust effective reward/risk for round-trip execution cost relative to stop distance
        cost_in_r = expected_cost_pct / stop_distance_pct
        effective_b = b - cost_in_r
        if effective_b <= 0.0:
            return KellyResult(
                full_kelly=Decimal("0"),
                fractional_kelly=Decimal("0"),
                applied_multiplier=self.multiplier,
                uncertainty_haircut=Decimal("1"),
                reward_risk_ratio=Decimal(str(round(effective_b, 6))),
                win_probability=Decimal(str(round(p, 6))),
                expected_log_growth=0.0,
                is_valid=False,
                reason=f"Trading costs ({cost_in_r:.2f}R) consume entire reward-to-risk ({b:.2f}R).",
            )

        q = 1.0 - p
        f_star = ((effective_b * p) - q) / effective_b
        if f_star <= 0.0:
            return KellyResult(
                full_kelly=Decimal("0"),
                fractional_kelly=Decimal("0"),
                applied_multiplier=self.multiplier,
                uncertainty_haircut=Decimal("1"),
                reward_risk_ratio=Decimal(str(round(effective_b, 6))),
                win_probability=Decimal(str(round(p, 6))),
                expected_log_growth=0.0,
                is_valid=False,
                reason=f"Negative/zero Kelly edge (f*={f_star:.4f}); trade rejected.",
            )

        if uncertainty_width > self.max_uncertainty_width:
            return KellyResult(
                full_kelly=Decimal(str(round(f_star, 6))),
                fractional_kelly=Decimal("0"),
                applied_multiplier=self.multiplier,
                uncertainty_haircut=Decimal("0"),
                reward_risk_ratio=Decimal(str(round(effective_b, 6))),
                win_probability=Decimal(str(round(p, 6))),
                expected_log_growth=0.0,
                is_valid=False,
                reason=f"Probability uncertainty width {uncertainty_width:.3f} exceeds max {self.max_uncertainty_width:.3f}.",
            )

        # Smooth uncertainty haircut: wider CI -> smaller fraction
        haircut = max(0.25, 1.0 - (uncertainty_width / (self.max_uncertainty_width * 1.25)))
        raw_frac = Decimal(str(f_star)) * self.multiplier * Decimal(str(haircut))
        capped_frac = min(raw_frac, self.max_risk_per_trade)

        f_float = float(capped_frac)
        # Expected log growth G(f) = p*ln(1 + b*f) + q*ln(1 - f)
        g_val = p * math.log(1.0 + effective_b * f_float) + q * math.log(max(1e-12, 1.0 - f_float))

        return KellyResult(
            full_kelly=Decimal(str(round(f_star, 6))),
            fractional_kelly=capped_frac.quantize(Decimal("0.000001")),
            applied_multiplier=self.multiplier,
            uncertainty_haircut=Decimal(str(round(haircut, 4))),
            reward_risk_ratio=Decimal(str(round(effective_b, 6))),
            win_probability=Decimal(str(round(p, 6))),
            expected_log_growth=round(g_val, 8),
            is_valid=capped_frac > 0 and g_val > 0,
            reason=f"Full Kelly={f_star:.4f}, mult={self.multiplier}, haircut={haircut:.2f}, final={capped_frac:.6f}",
        )

    def optimize_empirical_log_growth(self, returns: Sequence[float]) -> tuple[Decimal, float]:
        """
        Expected-log-growth optimizer for arbitrary empirical return distributions:
        maximizes E[ln(1 + f * r)] over f in [0, 0.95 / max_loss].
        """
        arr = np.asarray(returns, dtype=float)
        if arr.size < 10 or float(arr.mean()) <= 0.0:
            return Decimal("0"), 0.0

        min_ret = float(arr.min())
        upper = 1.0 if min_ret >= 0 else min(1.0, 0.90 / abs(min_ret))
        if upper <= 0.0:
            return Decimal("0"), 0.0

        def neg_log_growth(f: float) -> float:
            wealth_rel = 1.0 + f * arr
            if np.any(wealth_rel <= 0.0):
                return 1e9
            return -float(np.mean(np.log(wealth_rel)))

        res = minimize_scalar(neg_log_growth, bounds=(0.0, upper), method="bounded")
        f_opt = float(res.x) if res.success else 0.0
        g_opt = -float(res.fun) if res.success else 0.0
        if f_opt <= 0.0 or g_opt <= 0.0:
            return Decimal("0"), 0.0

        fractional = min(Decimal(str(f_opt)) * self.multiplier, self.max_risk_per_trade)
        return fractional.quantize(Decimal("0.000001")), round(g_opt, 8)
