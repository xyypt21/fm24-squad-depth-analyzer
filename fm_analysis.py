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

from fm_positions import SLOTS, player_can_play
from fm_report import generate_full_html

OUTPUT = Path(__file__).parent / "fm_analysis.html"


def _translate_xi_names(*xis) -> None:
    """只翻译最终出现在网页（入选阵容）里的球员名字。

    对多组 XI 去重收集名字，一次性并发走 Google 在线翻译（经本地代理），
    就地改 player dict 的 name，未命中的保持原样。
    """
    from fm_names import auto_translate  # noqa: PLC0415 延迟导入，加快启动

    seen = set()
    names = []
    for xi in xis:
        for _pos, p in xi:
            if p["name"] not in seen:
                seen.add(p["name"])
                names.append(p["name"])
    if not names:
        return
    translated = auto_translate(names)
    for xi in xis:
        for _pos, p in xi:
            if p["name"] in translated:
                p["name"] = translated[p["name"]]


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

    from scipy.optimize import linear_sum_assignment  # noqa: PLC0415 延迟导入，加快启动

    cost = np.full((slot_count, len(candidates)), huge_penalty, dtype=np.int32)
    for slot_i, slot_name in enumerate(SLOTS):
        for player_i, player in enumerate(candidates):
            if player_can_play(player["position"], slot_name):
                cost[slot_i, player_i] = max_key - player[sort_key]

    row_indices, col_indices = linear_sum_assignment(cost)
    return [(SLOTS[row], candidates[col]) for row, col in zip(row_indices, col_indices)]


# ── Main flow ──────────────────────────────────────────────
def analyze(
    roster: List[dict],
    output: Optional[Path] = None,
    min_age: int = 17,
    growth_until_age: int = 21,
    growth_per_year: int = 20,
) -> Optional[str]:
    """
    Core analysis flow: compute EA, pick the best and second-best XI for both
    CA (current ability) and EA (expected ability).
    roster: list of player dicts, each with name/age/position/ca/pa.
    output: HTML output path, defaults to OUTPUT.
    min_age: only players aged >= min_age are considered (default 17).
    growth_until_age: age at which EA growth stops (default 21).
    growth_per_year:  EA growth per year of age (default 20).
    Returns the HTML string, or None if roster is empty.
    """
    candidates = [p for p in roster if p["age"] >= min_age]
    if not candidates:
        print("未找到阵容数据")
        return None

    calculate_ea(candidates, growth_until_age=growth_until_age, growth_per_year=growth_per_year)

    ca_first = select_best_xi(candidates, "ca")
    used_ca = {id(p) for _, p in ca_first}
    ca_second = select_best_xi([p for p in candidates if id(p) not in used_ca], "ca")

    ea_first = select_best_xi(candidates, "ea")
    used_ea = {id(p) for _, p in ea_first}
    ea_second = select_best_xi([p for p in candidates if id(p) not in used_ea], "ea")

    # 只翻译最终出现在网页（入选阵容）里的球员名字，避免多余请求
    _translate_xi_names(ca_first, ca_second, ea_first, ea_second)

    html = generate_full_html(ca_first, ca_second, ea_first, ea_second)
    out = output or OUTPUT
    out.write_text(html, encoding="utf-8")
    return html
