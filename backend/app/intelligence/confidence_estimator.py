"""Bayesian win-probability & uncertainty estimation from actual historical/OOS results."""

from __future__ import annotations

from dataclasses import dataclass
from scipy.stats import beta as beta_dist


@dataclass(frozen=True)
class ProbabilityEstimate:
    mean_win_probability: float
    conservative_win_probability: float  # Lower confidence bound used for Kelly sizing
    ci_low: float
    ci_high: float
    uncertainty_width: float
    sample_size: int
    meets_min_sample: bool
    oos_validated: bool
    reason: str


class ConfidenceEstimator:
    """
    Estimates trading win probability using a Bayesian Beta-Binomial conjugate model.
    Never treats an unvalidated heuristic confidence score as a statistical win probability.
    """

    def __init__(
        self,
        *,
        prior_alpha: float = 2.0,
        prior_beta: float = 2.5,
        min_sample_size: int = 20,
        ci_level: float = 0.90,
        max_uncertainty_width: float = 0.35,
    ) -> None:
        self.prior_alpha = prior_alpha
        self.prior_beta = prior_beta
        self.min_sample_size = min_sample_size
        self.ci_level = ci_level
        self.max_uncertainty_width = max_uncertainty_width

    def estimate(
        self,
        *,
        wins: int,
        losses: int,
        oos_wins: int = 0,
        oos_losses: int = 0,
    ) -> ProbabilityEstimate:
        total = wins + losses
        oos_total = oos_wins + oos_losses

        # Weight out-of-sample evidence more heavily when available
        effective_wins = float(wins) + 1.5 * float(oos_wins)
        effective_losses = float(losses) + 1.5 * float(oos_losses)

        alpha_post = self.prior_alpha + effective_wins
        beta_post = self.prior_beta + effective_losses

        mean_p = alpha_post / (alpha_post + beta_post)
        tail = (1.0 - self.ci_level) / 2.0
        ci_low = float(beta_dist.ppf(tail, alpha_post, beta_post))
        ci_high = float(beta_dist.ppf(1.0 - tail, alpha_post, beta_post))
        width = ci_high - ci_low

        meets_sample = total >= self.min_sample_size
        oos_ok = oos_total >= max(5, self.min_sample_size // 4)

        # Conservative probability: use lower quartile / CI bound when sample is smaller or unvalidated
        if meets_sample and oos_ok:
            conservative_p = float(beta_dist.ppf(0.20, alpha_post, beta_post))
        else:
            conservative_p = ci_low

        reason = (
            f"Bayesian Beta({alpha_post:.1f}, {beta_post:.1f}): "
            f"mean={mean_p:.3f}, conservative={conservative_p:.3f}, "
            f"90% CI=[{ci_low:.3f}, {ci_high:.3f}], n={total} (oos={oos_total})"
        )
        return ProbabilityEstimate(
            mean_win_probability=round(mean_p, 6),
            conservative_win_probability=round(conservative_p, 6),
            ci_low=round(ci_low, 6),
            ci_high=round(ci_high, 6),
            uncertainty_width=round(width, 6),
            sample_size=total,
            meets_min_sample=meets_sample,
            oos_validated=oos_ok,
            reason=reason,
        )
