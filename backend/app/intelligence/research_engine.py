"""Research Engine with contextual UCB multi-armed bandit strictly for research/paper allocation."""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class BanditArmStats:
    strategy_name: str
    pulls: int = 0
    total_reward: float = 0.0

    @property
    def mean_reward(self) -> float:
        return self.total_reward / self.pulls if self.pulls > 0 else 0.0


class ResearchEngine:
    """
    Allocates research / paper-testing budget across candidate strategies using UCB1.
    Explicitly isolated from live capital execution.
    """

    def __init__(self, strategy_names: list[str], exploration_c: float = 1.4) -> None:
        self.exploration_c = exploration_c
        self.arms: dict[str, BanditArmStats] = {
            name: BanditArmStats(strategy_name=name) for name in strategy_names
        }

    def record_research_outcome(self, strategy_name: str, normalized_reward: float) -> None:
        if strategy_name not in self.arms:
            self.arms[strategy_name] = BanditArmStats(strategy_name=strategy_name)
        arm = self.arms[strategy_name]
        arm.pulls += 1
        arm.total_reward += normalized_reward

    def select_next_research_candidate(self) -> str:
        total_pulls = sum(a.pulls for a in self.arms.values())
        for name, arm in self.arms.items():
            if arm.pulls == 0:
                return name
        best_name = max(
            self.arms.keys(),
            key=lambda n: (
                self.arms[n].mean_reward
                + self.exploration_c * math.sqrt(math.log(total_pulls) / self.arms[n].pulls)
            ),
        )
        return best_name
