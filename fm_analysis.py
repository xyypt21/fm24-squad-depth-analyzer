"""
FM2024 4-2-3-1 squad depth analysis tool.

Parses an RTF roster -> computes EA -> uses the Hungarian algorithm
to pick the best and second-best starting XIs.

EA (Expected Ability) = CA + growth potential
  age < 21  EA = CA + (21 - age) * 20
  age >= 21 EA = CA
  EA is capped at PA
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path

# ── Paths & Config ──────────────────────────────────────
OUTPUT = Path(__file__).parent / "fm_analysis.html"

CONFIG_PATH = Path(__file__).parent / "config.json"
DEFAULT_CONFIG = {
    "rtf_path": r"C:\Users\xyy\Documents\Sports Interactive\Football Manager 2024\team.rtf",
    "growth_until_age": 21,
    "growth_per_year": 20,
}


# ── Config File ─────────────────────────────────────────
def load_config(path=None):
    """Load config from disk, falling back to defaults if missing or corrupt."""
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
    """Persist config back to disk."""
    path = Path(path) if path else CONFIG_PATH
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


# ── Formation Slots ─────────────────────────────────────
SLOTS = ["GK", "DL", "DC", "DC", "DR", "DMC", "DMC",
         "AML", "AMC", "AMR", "STC"]

# Position tokens each formation slot can accept
SLOT_COMPAT = {
    "GK":  {"GK"},
    "DL":  {"DL"},
    "DC":  {"DC"},
    "DR":  {"DR"},
    "DMC": {"DMC"},
    "AML": {"AML"},
    "AMC": {"AMC"},
    "AMR": {"AMR"},
    "STC": {"STC"},
}

# Slots shown in the depth chart (with their SLOTS index mapping)
DEPTH_SLOTS = ["GK", "DL", "DC", "DR", "DMC", "AML", "AMC", "AMR", "STC"]
DEPTH_SLOT_MAP = {
    "GK":  [0],
    "DL":  [1],
    "DC":  [2, 3],
    "DR":  [4],
    "DMC": [5, 6],
    "AML": [7],
    "AMC": [8],
    "AMR": [9],
    "STC": [10],
}


# ── Position Parsing ────────────────────────────────────
# Legal FM position roles and side letters
VALID_ROLES = {"GK", "D", "WB", "DM", "M", "AM", "ST"}
VALID_SIDES = {"C", "L", "R"}

# All legal single-side position tokens (full FM position list)
VALID_POSITION_TOKENS = {
    "GK",
    "WBL", "WBR",
    "DMC",
    "STC",
    *(r + s for r in ("D", "M", "AM") for s in VALID_SIDES),
}


def parse_position_tokens(position_text):
    """
    Split an FM position string into role+side combination tokens.
    Only legal FM positions are kept.

    Examples:
      "M (L), AM (RLC)"     -> ["ML", "AMR", "AML", "AMC"]
      "D/WB (R)"            -> ["DR", "WBR"]
      "ST (C)"              -> ["STC"]
      "GK"                  -> ["GK"]
    """
    tokens = []
    for part in position_text.upper().split(","):
        part = part.strip()
        if not part:
            continue
        match = re.match(r'([A-Z/]+)\s*(?:\(([A-Z]+)\))?', part)
        if not match:
            continue
        roles_text, sides_text = match.group(1), (match.group(2) or "")
        for role in roles_text.split("/"):
            if role not in VALID_ROLES:
                continue
            if sides_text:
                if not all(s in VALID_SIDES for s in sides_text):
                    continue
                tokens.extend(role + s for s in sides_text)
            elif role == "GK":
                tokens.append("GK")
    return [t for t in tokens if t in VALID_POSITION_TOKENS]


def player_can_play(position_text, slot_name):
    """Return whether a player can play a given formation slot."""
    return any(
        token in SLOT_COMPAT.get(slot_name, set())
        for token in parse_position_tokens(position_text)
    )


# ── RTF Parsing ─────────────────────────────────────────
def read_roster_from_rtf(path=None):
    """
    Read an RTF table exported from FM and return a list of players.
    Each player dict contains: name, age, position, ca, pa.
    path: RTF file path, defaults to the path from config.
    """
    path = Path(path) if path else Path(load_config()["rtf_path"])
    if not path.exists():
        return []

    content = path.read_text(encoding="utf-8", errors="replace")
    lines = [line for line in content.split("\n")
             if "|" in line and "---" not in line]
    if not lines:
        return []

    # Parse the header row to locate each column dynamically
    headers = [
        ''.join(c for c in cell if c != '\u200b').strip()
        for cell in lines[0].split('|')
    ]
    col_index = {}
    ca_count = 0
    for i, header in enumerate(headers):
        if   header == '姓名': col_index['name'] = i
        elif header == '年龄': col_index['age']  = i
        elif header == '位置': col_index['pos']  = i
        elif header == '能力':
            ca_count += 1
            if ca_count == 2:
                col_index['ca'] = i
        elif header == '潜力': col_index['pa']  = i

    # Parse player rows one by one
    players = []
    for line in lines[1:]:
        cells = [cell.strip() for cell in line.split("|")]
        try:
            name = ''.join(c for c in cells[col_index['name']] if c != '\u200b').strip()
            age  = int(''.join(c for c in cells[col_index['age']]  if c.isdigit()))
            pos  = ''.join(c for c in cells[col_index['pos']]  if c != '\u200b').strip()
            ca   = int(''.join(c for c in cells[col_index['ca']]  if c.isdigit()))
            pa   = int(''.join(c for c in cells[col_index['pa']]  if c.isdigit()))
            if age > 0 and pos and ca > 0:
                players.append(dict(name=name, age=age, position=pos, ca=ca, pa=pa))
        except (ValueError, KeyError, IndexError):
            pass

    return players


# ── EA Calculation ──────────────────────────────────────
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


# ── Lineup Selection (Hungarian Algorithm) ──────────────
def select_best_xi(candidates, sort_key):
    """
    Pick the optimal starting XI from candidates using the Hungarian algorithm.

    Cost design (all positive to keep the algorithm stable):
      - Valid assignment: max_key - player_key (range 0 .. max_key-1)
      - Invalid assignment: far larger than the sum of all valid costs,
        ensuring the algorithm prefers valid placements.
    """
    import numpy as np
    from scipy.optimize import linear_sum_assignment

    slot_count = len(SLOTS)
    max_key = max(p[sort_key] for p in candidates) if candidates else 200
    huge_penalty = max_key * slot_count * 2 + 1  # far above sum of valid costs

    cost = np.full((slot_count, len(candidates)), huge_penalty, dtype=np.int32)
    for slot_i, slot_name in enumerate(SLOTS):
        for player_i, player in enumerate(candidates):
            if player_can_play(player["position"], slot_name):
                cost[slot_i, player_i] = max_key - player[sort_key]

    row_indices, col_indices = linear_sum_assignment(cost)
    return [
        (SLOTS[row], candidates[col])
        for row, col in zip(row_indices, col_indices)
    ]


def filter_senior_players(players):
    """Keep only players aged >= 17."""
    return [p for p in players if p["age"] >= 17]


def build_depth_chart(players):
    """Build depth chart data per slot (full roster, players may repeat)."""
    depth = {}
    for slot in DEPTH_SLOTS:
        eligible = [p for p in players if player_can_play(p["position"], slot)]
        eligible.sort(key=lambda p: p["ea"], reverse=True)
        n = 6 if slot in ("DMC", "DC") else (3 if slot == "GK" else 4)
        depth[slot] = eligible[:n]
    return depth


# ── HTML Generation ─────────────────────────────────────
def compute_average(xi, sort_key):
    """Compute the average score of a starting XI."""
    return int(sum(p[sort_key] for _, p in xi) / len(xi)) if xi else 0


def render_player_slot(slot_name, player, sort_key, reference_value):
    """Render a single player slot as HTML."""
    weak = " slot-weak" if is_weak(player[sort_key], reference_value) else ""
    return (
        f"<div class='slot{weak}'>"
        f"<div class='slot-label'>{slot_name}</div>"
        f"<div class='p-name'>{player['name']}</div>"
        f"<div class='p-stat'>{player['age']}岁 · CA{player['ca']} · EA{player['ea']:.0f}</div>"
        f"</div>"
    )


def render_pitch(xi, sort_key, reference_value):
    """
    Render the 11-player pitch formation diagram.
    Rows: ST / AML,AMC,AMR / DM,DM / DL,DC,DC,DR / GK
    """
    def slot_html(index):
        if index >= len(xi):
            return "<div class='slot' style='visibility:hidden;'></div>"
        slot_name, player = xi[index]
        return render_player_slot(slot_name, player, sort_key, reference_value)

    row_indices = [(10,), (7, 8, 9), (5, 6), (1, 2, 3, 4), (0,)]
    rows = [
        "<div class='f-row'>" + "".join(slot_html(i) for i in indices) + "</div>"
        for indices in row_indices
    ]
    return "\n".join(rows)


def render_pitch_card(xi, sort_key, reference=None):
    """Render a complete pitch card."""
    average = compute_average(xi, sort_key)
    if not xi:
        return (
            "<div class='pitch' style='display:flex;align-items:center;"
            "justify-content:center;color:rgba(255,255,255,0.5);font-size:14px;'>"
            "球员不足</div>"
        )
    return (
        f"<div class='pitch'>"
        f"{render_pitch(xi, sort_key, reference or average)}"
        f"</div>"
    )


def is_weak(player_ea, ref):
    return player_ea < ref * 0.9


def compute_scores(ea_first, ea_second, depth_data, ref):
    """
    Reinforcement score: the higher, the more the position needs strengthening.
    Max 4 per position. A weak starter adds +2, a weak backup adds +1,
    plus the ratio of weak players in depth (1 decimal place).
    """
    scores = {}
    for slot in DEPTH_SLOTS:
        indices = DEPTH_SLOT_MAP[slot]
        first_red = 2 if any(i < len(ea_first) and is_weak(ea_first[i][1]["ea"], ref) for i in indices) else 0
        second_red = 1 if any(i < len(ea_second) and is_weak(ea_second[i][1]["ea"], ref) for i in indices) else 0
        depth_players = depth_data.get(slot, [])
        red_ratio = round(sum(1 for p in depth_players if is_weak(p["ea"], ref)) / len(depth_players), 1) if depth_players else 0
        scores[slot] = first_red + second_red + red_ratio
    return scores


def render_depth_chart(depth_data, reference_value, scores=None):
    """Render the depth chart (2-column vertical layout, sorted by score desc)."""
    slots_sorted = sorted(DEPTH_SLOTS, key=lambda s: -(scores.get(s, 0) if scores else 0))

    mid = (len(slots_sorted) + 1) // 2
    columns = [slots_sorted[:mid], slots_sorted[mid:]]

    def render_column(col_data):
        parts = []
        for slot in col_data:
            players = depth_data.get(slot, [])
            avg = int(sum(p["ea"] for p in players) / len(players)) if players else 0
            items = ""
            for p in players:
                weak = " dc-weak" if is_weak(p["ea"], reference_value) else ""
                items += (
                    f"<div class='dc-item{weak}'>"
                    f"<span class='dc-name'>{p['name']}</span>"
                    f"<span class='dc-age'>{p['age']}岁</span>"
                    f"<span class='dc-ca'>CA{p['ca']}</span>"
                    f"<span class='dc-ea'>EA{p['ea']:.0f}</span>"
                    f"</div>"
                )
            score = scores.get(slot, 0) if scores else 0
            score_cls = " sc-low" if score == 0 else (" sc-mid" if score < 2 else " sc-high")
            parts.append(
                f"<div class='dc-section'>"
                f"<div class='dc-header'>{slot}<span class='dc-score{score_cls}'>{score}</span> · 均{avg}</div>"
                f"{items}"
                f"</div>"
            )
        return "<div class='dc-col'>" + "\n".join(parts) + "</div>"

    return render_column(columns[0]) + render_column(columns[1])


CSS_STYLE = """
* { margin:0; padding:0; box-sizing:border-box; }
html,body { height:100%; font-family:"Microsoft YaHei","Segoe UI",sans-serif; background:#f0f2f5; }
.container { height:100%; max-width:1400px; margin:0 auto; padding:10px 14px; display:flex; flex-direction:column; }
.card { background:white; border-radius:10px; box-shadow:0 2px 10px rgba(0,0,0,0.07); flex:1; min-height:0; }
.card-body { padding:10px; height:100%; display:flex; align-items:stretch; }
.grid { display:grid; grid-template-columns:1fr 1fr 1fr; gap:10px; width:100%; height:100%; }
.col { display:flex; flex-direction:column; height:100%; min-width:0; }
.pitch { background:linear-gradient(135deg,#2d7d46,#1b5e30); border-radius:8px; padding:8px 4px; flex:1; display:flex; flex-direction:column; justify-content:space-evenly; position:relative; width:100%; }
.pitch-label { position:absolute; left:8px; bottom:6px; color:rgba(255,255,255,0.45); font-size:14px; line-height:1.4; pointer-events:none; }
.f-row { display:flex; justify-content:center; gap:4px; margin:2px 0; }
.slot { background:rgba(255,255,255,0.12); border:1px solid rgba(255,255,255,0.2); border-radius:4px; padding:4px 8px; text-align:center; min-width:80px; }
.slot-weak { background:rgba(231,76,60,0.25); border-color:rgba(231,76,60,0.5); }
.slot-label { font-size:10px; color:rgba(255,255,255,0.45); letter-spacing:1px; }
.p-name { font-size:14px; color:#fff; line-height:1.4; word-break:break-all; }
.p-stat { font-size:12px; color:rgba(255,255,255,0.65); }
.dc-col { display:flex; flex-direction:column; flex:1; min-width:0; }
.col-header { font-size:16px; font-weight:bold; text-align:center; padding:6px 0 2px; color:#333; }
.col-avg { font-weight:normal; font-size:16px; color:#888; margin-left:8px; }

.dc-section { flex-shrink:0; }
.dc-header { font-size:13px; font-weight:bold; color:#fff; padding:4px 6px 2px; border-bottom:1px solid rgba(255,255,255,0.15); }
.dc-score { float:right; font-size:12px; font-weight:normal; margin-left:6px; padding:0 4px; border-radius:3px; }
.sc-high { color:#e57373; }
.sc-mid { color:#ffb74d; }
.sc-low { color:#81c784; }
.dc-item { font-size:12px; color:rgba(255,255,255,0.85); line-height:1.6; padding:1px 6px; display:flex; gap:6px; }
.dc-weak { color:#ffb74d; }
.dc-name { flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.dc-age, .dc-ca, .dc-ea { flex-shrink:0; }
.footer { text-align:center; font-size:10px; color:#bdc3c7; flex-shrink:0; padding:4px; }
"""


def generate_full_html(ea_first, ea_second, depth_chart, dc_unique_count):
    ref = compute_average(ea_first, "ea")
    scores = compute_scores(ea_first, ea_second, depth_chart, ref)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>FM2024 4231 阵容厚度</title>
<style>{CSS_STYLE}</style>
</head>
<body>
<div class="container">
    <div class="card">
        <div class="card-body">
            <div class="grid">
                <div class='col'>{render_pitch_card(ea_first, "ea")}<div class='col-header'>EA最佳11人<span class='col-avg'>均EA {compute_average(ea_first, "ea")}</span></div></div>
                <div class='col'>{render_pitch_card(ea_second, "ea", ref)}<div class='col-header'>EA次佳11人<span class='col-avg'>均EA {compute_average(ea_second, "ea")}</span></div></div>
                <div class='col'><div class='pitch' style='overflow-y:auto;justify-content:flex-start;flex-direction:row;gap:4px;'>{render_depth_chart(depth_chart, ref, scores)}</div><div class='col-header'>深度图<span class='col-avg'>共{dc_unique_count}人</span></div></div>
            </div>
        </div>
    </div>
    <div class="footer">分析生成于 {datetime.now().strftime('%Y-%m-%d %H:%M')}</div>
</div>
</body>
</html>"""


# ── Main Flow ───────────────────────────────────────────
def analyze(roster, output=None, growth_until_age=21, growth_per_year=20):
    """
    Core analysis flow: compute EA, select best and second-best XIs,
    and generate the depth chart.
    roster: list of player dicts, each with name/age/position/ca/pa.
    output: HTML output path, defaults to OUTPUT.
    growth_until_age: age at which EA growth stops (default 21).
    growth_per_year:  EA growth per year of age (default 20).
    Returns the HTML string, or None if roster is empty.
    """
    if not roster:
        print("未找到阵容数据")
        return None

    calculate_ea(roster, growth_until_age=growth_until_age,
                 growth_per_year=growth_per_year)

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


def main():
    html = analyze(read_roster_from_rtf())
    if html:
        os.startfile(OUTPUT.resolve())


if __name__ == "__main__":
    main()
