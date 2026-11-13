"""Statistical drift & strategy deterioration detection (CUSUM, Page-Hinkley, KS-test, Rolling EV)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import numpy as np
from scipy.stats import ks_2samp


@dataclass(frozen=True)
class DriftReport:
    deteriorated: bool
    drift_score: float
    cusum_alarm: bool
    page_hinkley_alarm: bool
    distribution_shift_alarm: bool
    rolling_ev_negative: bool
    reasons: list[str] = field(default_factory=list)


class DriftDetector:
    """
    Distinguishes normal statistical variance from genuine strategy edge decay.
    Combines:
      1. One-sided downward CUSUM on trade returns
      2. Page-Hinkley change-point test
      3. Two-sample Kolmogorov-Smirnov distribution shift test (baseline vs recent)
      4. Rolling expected-value collapse check with minimum sample guard
    """

    def __init__(
        self,
        *,
        min_trades: int = 12,
        cusum_threshold: float = 3.0,
        ph_threshold: float = 3.5,
        ks_alpha: float = 0.05,
    ) -> None:
        self.min_trades = min_trades
        self.cusum_threshold = cusum_threshold
        self.ph_threshold = ph_threshold
        self.ks_alpha = ks_alpha

    def detect(
        self,
        returns: Sequence[float],
        *,
        baseline_returns: Sequence[float] | None = None,
    ) -> DriftReport:
        arr = np.asarray(returns, dtype=float)
        total_obs = arr.size + (len(baseline_returns) if baseline_returns is not None else 0)
        if total_obs < self.min_trades or arr.size < 6:
            return DriftReport(
                deteriorated=False,
                drift_score=0.0,
                cusum_alarm=False,
                page_hinkley_alarm=False,
                distribution_shift_alarm=False,
                rolling_ev_negative=False,
                reasons=[f"Sample size {total_obs} < min_trades {self.min_trades}; avoiding premature switching."],
            )

        # Reference baseline = first half if not explicitly supplied
        if baseline_returns is not None and len(baseline_returns) >= 8:
            base = np.asarray(baseline_returns, dtype=float)
            recent = arr
        else:
            split = max(8, arr.size // 2)
            base = arr[:split]
            recent = arr[split:]

        mu0 = float(np.mean(base))
        sigma0 = max(float(np.std(base, ddof=1)) if base.size > 1 else 0.01, 1e-4)
        z_recent = (recent - mu0) / sigma0

        # 1. Downward CUSUM: S_t = max(0, S_{t-1} - z_t - k)
        k = 0.5
        s_cusum = 0.0
        max_cusum = 0.0
        for z in z_recent:
            s_cusum = max(0.0, s_cusum - float(z) - k)
            max_cusum = max(max_cusum, s_cusum)
        cusum_alarm = max_cusum >= self.cusum_threshold

        # 2. Page-Hinkley downward shift test
        delta = 0.25
        running_sum = 0.0
        max_cum = 0.0
        ph_stat = 0.0
        for z in z_recent:
            running_sum += -float(z) - delta
            max_cum = max(max_cum, running_sum)
            ph_stat = max(ph_stat, running_sum - min(0.0, running_sum))
        ph_alarm = max_cum >= self.ph_threshold

        # 3. Distribution shift via KS test (only alarm if recent mean is also worse)
        ks_alarm = False
        ks_p = 1.0
        if recent.size >= 8 and base.size >= 8:
            _, ks_p = ks_2samp(base, recent)
            if ks_p < self.ks_alpha and float(np.mean(recent)) < mu0:
                ks_alarm = True

        # 4. Rolling EV collapse
        recent_mean = float(np.mean(recent)) if recent.size > 0 else float(np.mean(arr))
        rolling_ev_neg = recent.size >= 10 and recent_mean < -0.0005

        reasons: list[str] = []
        if cusum_alarm:
            reasons.append(f"Downward CUSUM breached ({max_cusum:.2f} >= {self.cusum_threshold}).")
        if ph_alarm:
            reasons.append(f"Page-Hinkley shift detected ({max_cum:.2f} >= {self.ph_threshold}).")
        if ks_alarm:
            reasons.append(f"KS return distribution shift (p={ks_p:.4f} < {self.ks_alpha}).")
        if rolling_ev_neg:
            reasons.append(f"Rolling recent mean return turned negative ({recent_mean:.5f}).")

        alarms = int(cusum_alarm) + int(ph_alarm) + int(ks_alarm) + int(rolling_ev_neg)
        # Require >= 2 corroborating signals or strong CUSUM+EV collapse to avoid noise switching
        deteriorated = alarms >= 2 or (cusum_alarm and rolling_ev_neg)
        drift_score = min(1.0, (max_cusum / max(self.cusum_threshold, 1.0)) * 0.4 + alarms * 0.2)

        return DriftReport(
            deteriorated=deteriorated,
            drift_score=round(drift_score, 4),
            cusum_alarm=cusum_alarm,
            page_hinkley_alarm=ph_alarm,
            distribution_shift_alarm=ks_alarm,
            rolling_ev_negative=rolling_ev_neg,
            reasons=reasons,
        )
