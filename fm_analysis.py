"""
FM2024 4-2-3-1 squad depth analysis tool.

Parses the RTF roster → computes EA → uses the Hungarian algorithm to pick
the EA best and second-best starting XI.

EA (Expected Ability) = CA + growth potential
  age < 21   EA = CA + (21 - age) × 20
  age >= 21  EA = CA
  EA capped at PA
"""

import json
import os
import re
import warnings
from pathlib import Path

import numpy as np
from scipy.optimize import linear_sum_assignment

# ── Paths & config ──────────────────────────────────────────
OUTPUT = Path(__file__).parent / "fm_analysis.html"
CONFIG_PATH = Path(__file__).parent / "config.json"
DEFAULT_CONFIG = {
    "rtf_path": r"~\Documents\Sports Interactive\Football Manager 2024\team.rtf",
    "growth_until_age": 21,
    "growth_per_year": 20,
}


# ── Config file ────────────────────────────────────────────
def load_config(path=None):
    """Read the config file, falling back to defaults if missing or corrupt."""
    path = Path(path) if path else CONFIG_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return dict(DEFAULT_CONFIG)
    merged = {**DEFAULT_CONFIG, **data}
    return {
        "rtf_path": merged["rtf_path"],
        "growth_until_age": int(merged["growth_until_age"]),
        "growth_per_year": int(merged["growth_per_year"]),
    }


def save_config(config, path=None):
    """Write the config back to the file."""
    path = Path(path) if path else CONFIG_PATH
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


# ── Formation slots ────────────────────────────────────────
SLOTS = ["GK", "DL", "DC", "DC", "DR", "DMC", "DMC", "AML", "AMC", "AMR", "STC"]

# Depth-chart slots: auto-derived from SLOTS (deduped by first occurrence,
# recording the index of each slot in the formation).
# Note: SLOTS is the 11-man formation (with repeated DC/DMC), whereas the
# reinforcement scores and depth chart are grouped by *position*, so we use
# DEPTH_SLOT_MAP to translate a position into indices in the ea_first/ea_second
# lists (ordered as in SLOTS). E.g. DEPTH_SLOT_MAP["DC"] == [2, 3] means the two
# centre-backs sit at indices 2 and 3 of the 11-man lists.
DEPTH_SLOT_MAP = {}
for i, slot in enumerate(SLOTS):
    DEPTH_SLOT_MAP.setdefault(slot, []).append(i)
DEPTH_SLOTS = list(DEPTH_SLOT_MAP)


# ── Position parsing ───────────────────────────────────────
# Valid FM position roles and side letters
VALID_ROLES = {"GK", "D", "WB", "DM", "M", "AM", "ST"}
VALID_SIDES = {"C", "L", "R"}

# All valid position tokens (full FM position set)
VALID_POSITION_TOKENS = {
    "GK",
    "WBL",
    "WBR",
    "DMC",
    "STC",
    *(r + s for r in ("D", "M", "AM") for s in VALID_SIDES),
}


def parse_position_tokens(position_text):
    """
    Split an FM position string into "role+side" tokens, keeping only valid ones.
    Roles without parentheses are treated as centre (+C), e.g. "DM" -> ["DMC"].

    Examples:
      "M (L), AM (RLC)"     -> ["ML", "AMR", "AML", "AMC"]
      "D/WB (R)"            -> ["DR", "WBR"]
      "ST (C)"              -> ["STC"]
      "DM"                  -> ["DMC"]
      "GK"                  -> ["GK"]
    """
    tokens = []
    # Grab each "role(sides)" chunk in one regex pass, skipping commas/spaces.
    # E.g. "M (L), AM (RLC)" yields (M, L) then (AM, RLC)
    for roles_text, sides_text in re.findall(
        r"([A-Z/]+)\s*(?:\(([A-Z]+)\))?", position_text.upper()
    ):
        # Handle compound roles like D/WB, taking D and WB one by one
        for role in roles_text.split("/"):
            # Skip roles that are not valid (e.g. XX)
            if role not in VALID_ROLES:
                continue
            if sides_text:
                # Sides in parentheses: validate them, then build one token each
                if all(s in VALID_SIDES for s in sides_text):
                    tokens.extend(role + s for s in sides_text)
            else:
                # No parentheses: GK stays GK, other roles treated as centre,
                # e.g. DM -> DMC
                tokens.append("GK" if role == "GK" else role + "C")
    # Safety filter: drop any token not in the valid position table
    return [t for t in tokens if t in VALID_POSITION_TOKENS]


def player_can_play(position_text, slot_name):
    """Check whether a player can fill a formation slot."""
    return slot_name in parse_position_tokens(position_text)


# ── RTF file parsing ───────────────────────────────────────
def _clean(text):
    """Strip zero-width spaces and surrounding whitespace (FM exports carry \\u200b)."""
    return "".join(c for c in text if c != "\u200b").strip()


def _num(text):
    """Extract the digits out of a cell."""
    return int("".join(c for c in text if c.isdigit()))


def _parse_columns(headers):
    """Locate each column from the header row (ability is exported twice; take the 2nd as CA)."""
    col_index = {}
    ca_count = 0
    for i, header in enumerate(headers):
        if header == "姓名":
            col_index["name"] = i
        elif header == "年龄":
            col_index["age"] = i
        elif header == "位置":
            col_index["pos"] = i
        elif header == "能力":
            ca_count += 1
            if ca_count == 2:
                col_index["ca"] = i
        elif header == "潜力":
            col_index["pa"] = i
    return col_index


def read_roster_from_rtf(path=None):
    """
    Read the RTF table exported by FM and return the list of players.
    Each player has: name, age, position, ca, pa.
    path: RTF file path; defaults to the path in the config file.
    """
    path = Path(path) if path else Path(load_config()["rtf_path"])
    path = path.expanduser()
    if not path.exists():
        return []

    content = path.read_text(encoding="utf-8", errors="replace")
    lines = [line for line in content.split("\n") if "|" in line and "---" not in line]
    if not lines:
        return []

    # Parse the header row to locate each column
    headers = [_clean(cell) for cell in lines[0].split("|")]
    col_index = _parse_columns(headers)

    # Parse player data line by line
    players = []
    for line in lines[1:]:
        cells = [_clean(cell) for cell in line.split("|")]
        try:
            name = cells[col_index["name"]]
            age = _num(cells[col_index["age"]])
            pos = cells[col_index["pos"]]
            ca = _num(cells[col_index["ca"]])
            pa = _num(cells[col_index["pa"]])
        except (ValueError, KeyError, IndexError):
            continue
        if age <= 0 or not pos or ca <= 0:
            continue
        if not parse_position_tokens(pos):
            warnings.warn(f"Unrecognized position for {name}: {pos}", stacklevel=2)
        players.append({"name": name, "age": age, "position": pos, "ca": ca, "pa": pa})

    return players


# ── EA calculation ─────────────────────────────────────────
def calculate_ea(players, growth_until_age=21, growth_per_year=20):
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
def select_best_xi(candidates, sort_key):
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


def filter_senior_players(players):
    """Keep only players aged >= 17."""
    return [p for p in players if p["age"] >= 17]


def build_depth_chart(players):
    """Build depth-chart data per slot (full squad, players may appear twice)."""
    depth = {}
    for slot in DEPTH_SLOTS:
        eligible = [p for p in players if player_can_play(p["position"], slot)]
        eligible.sort(key=lambda p: p["ea"], reverse=True)
        n = 6 if slot in ("DMC", "DC") else (3 if slot == "GK" else 4)
        depth[slot] = eligible[:n]
    return depth


# ── Main flow ──────────────────────────────────────────────
def analyze(roster, output=None, growth_until_age=21, growth_per_year=20):
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
    from fm_report import generate_full_html

    html = generate_full_html(ea_best, ea_second, depth_chart, len(dc_all))
    out = output or OUTPUT
    out.write_text(html, encoding="utf-8")
    return html


def main():
    html = analyze(read_roster_from_rtf())
    if html:
        os.startfile(OUTPUT.resolve())


if __name__ == "__main__":
    main()
