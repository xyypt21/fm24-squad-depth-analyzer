"""
FM2024 4-2-3-1 squad depth analysis core.

Computes EA (Expected Ability) for every player, uses the Hungarian algorithm
to pick the EA best and second-best starting XI, and builds the depth chart.

EA (Expected Ability) = CA + growth potential
  age < growth_until_age  EA = CA + (growth_until_age - age) × growth_per_year
  age >= growth_until_age EA = CA
  EA capped at PA
"""

from pathlib import Path
from typing import List, Optional

import numpy as np
from scipy.optimize import linear_sum_assignment

from fm_positions import DEPTH_SLOTS, SLOTS, player_can_play
from fm_report import generate_full_html

OUTPUT = Path(__file__).parent / "fm_analysis.html"


# ── EA calculation ─────────────────────────────────────────
def calculate_ea(
    players: List[dict],
    growth_until_age: int = 21,
    growth_per_year: int = 20,
) -> None:
    """
    Compute EA (Expected Ability) for every player.
    EA = CA + growth potential, capped at PA.
    growth_until_age: age at which growth stops (default 21), configurable.
    growth_per_year:  growth per year of age (default 20), configurable.
    """
    for player in players:
        if player["age"] >= growth_until_age:
            player["ea"] = player["ca"]
        else:
            growth = (growth_until_age - player["age"]) * growth_per_year
            player["ea"] = min(player["ca"] + growth, player["pa"])


# ── Squad selection (Hungarian algorithm) ─────────────────
def select_best_xi(candidates: List[dict], sort_key: str) -> List[tuple]:
    """
    Use the Hungarian algorithm to pick the optimal 11-man assignment.

    Cost design (all positive for stable solving):
      - Valid assignment: max_key - player_key (range 0 to max_key-1)
      - Invalid assignment: far larger than any valid cost sum, so the
        algorithm prefers valid lineups.
    """
    slot_count = len(SLOTS)
    max_key = max(p[sort_key] for p in candidates) if candidates else 200
    huge_penalty = max_key * slot_count * 2 + 1  # far beyond any valid cost sum

    cost = np.full((slot_count, len(candidates)), huge_penalty, dtype=np.int32)
    for slot_i, slot_name in enumerate(SLOTS):
        for player_i, player in enumerate(candidates):
            if player_can_play(player["position"], slot_name):
                cost[slot_i, player_i] = max_key - player[sort_key]

    row_indices, col_indices = linear_sum_assignment(cost)
    return [(SLOTS[row], candidates[col]) for row, col in zip(row_indices, col_indices)]


def filter_senior_players(players: List[dict]) -> List[dict]:
    """Keep only players aged >= 17."""
    return [p for p in players if p["age"] >= 17]


def build_depth_chart(players: List[dict]) -> dict:
    """Build depth-chart data per slot (full squad, players may appear twice)."""
    depth = {}
    for slot in DEPTH_SLOTS:
        eligible = [p for p in players if player_can_play(p["position"], slot)]
        eligible.sort(key=lambda p: p["ea"], reverse=True)
        n = 6 if slot in ("DMC", "DC") else (3 if slot == "GK" else 4)
        depth[slot] = eligible[:n]
    return depth


# ── Main flow ──────────────────────────────────────────────
def analyze(
    roster: List[dict],
    output: Optional[Path] = None,
    growth_until_age: int = 21,
    growth_per_year: int = 20,
) -> Optional[str]:
    """
    Core analysis flow: compute EA, pick the best and second-best XI,
    and build the depth chart.
    roster: list of player dicts, each with name/age/position/ca/pa.
    output: HTML output path, defaults to OUTPUT.
    growth_until_age: age at which EA growth stops (default 21).
    growth_per_year:  EA growth per year of age (default 20).
    Returns the HTML string, or None if roster is empty.
    """
    if not roster:
        print("未找到阵容数据")
        return None

    calculate_ea(roster, growth_until_age=growth_until_age, growth_per_year=growth_per_year)

    candidates = filter_senior_players(roster)
    ea_best = select_best_xi(candidates, "ea")
    used_1 = {id(p) for _, p in ea_best}
    ea_second = select_best_xi([p for p in candidates if id(p) not in used_1], "ea")
    depth_chart = build_depth_chart(candidates)

    dc_all = {id(p) for plist in depth_chart.values() for p in plist}
    html = generate_full_html(ea_best, ea_second, depth_chart, len(dc_all))
    out = output or OUTPUT
    out.write_text(html, encoding="utf-8")
    return html
