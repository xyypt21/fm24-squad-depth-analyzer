"""
FM2024 4-2-3-1 阵容厚度分析工具。

解析 RTF 阵容 → 计算 EA → 用匈牙利算法分配 3 套 EA 最佳 11 人。

EA（预期能力）= CA + 成长潜力
  年龄 < 21  EA = CA + (21 - 年龄) × 20
  年龄 ≥ 21   EA = CA
  EA 不超过 PA
"""

import re
import os
from pathlib import Path
from datetime import datetime

# ── 路径与阵型 ──────────────────────────────────────────
FM_DIR = Path(r"C:\Users\xyy\Documents\Sports Interactive\Football Manager 2024")
RTF_PATH = FM_DIR / "team.rtf"
OUTPUT   = Path(__file__).parent / "fm_analysis.html"

CONFIG_PATH = Path(__file__).parent / "config.json"
DEFAULT_CONFIG = {
    "rtf_path": str(RTF_PATH),
    "growth_until_age": 21,
    "growth_per_year": 20,
}


# ── 配置文件 ────────────────────────────────────────────
def load_config(path=None):
    """读取配置文件，缺失或损坏时返回默认值。"""
    path = Path(path) if path else CONFIG_PATH
    if not path.exists():
        return dict(DEFAULT_CONFIG)
    import json
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return dict(DEFAULT_CONFIG)
    return {
        "rtf_path": data.get("rtf_path", DEFAULT_CONFIG["rtf_path"]),
        "growth_until_age": int(data.get("growth_until_age", DEFAULT_CONFIG["growth_until_age"])),
        "growth_per_year": int(data.get("growth_per_year", DEFAULT_CONFIG["growth_per_year"])),
    }


def save_config(config, path=None):
    """将配置写回文件。"""
    path = Path(path) if path else CONFIG_PATH
    import json
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

SLOTS = ["GK", "DL", "DC", "DC", "DR", "DM", "DM",
         "AML", "AMC", "AMR", "ST"]

# 每个阵型槽位能接受哪些位置 token
SLOT_COMPAT = {
    "GK":  {"GK"},
    "DL":  {"DL"},
    "DC":  {"DC"},
    "DR":  {"DR"},
    "DM":  {"DM"},
    "AML": {"AML"},
    "AMC": {"AMC"},
    "AMR": {"AMR"},
    "ST":  {"STC"},
}

# 深度图展示的槽位（含具体 SLOTS 下标映射）
DEPTH_SLOTS = ["GK", "DL", "DC", "DR", "DM", "AML", "AMC", "AMR", "ST"]
DEPTH_SLOT_MAP = {
    "GK":  [0],
    "DL":  [1],
    "DC":  [2, 3],
    "DR":  [4],
    "DM":  [5, 6],
    "AML": [7],
    "AMC": [8],
    "AMR": [9],
    "ST":  [10],
}


# ── 位置解析 ────────────────────────────────────────────
def parse_position_tokens(position_text):
    """
    将 FM 位置字符串拆成角色+侧面的组合 token。
    
    例子：
      "M (L), AM (RLC)"     → ["ML", "AMR", "AML", "AMC"]
      "D/WB (R)"            → ["DR", "WBR"]
      "ST (C)"              → ["STC"]
      "GK"                  → ["GK"]
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
            if sides_text:
                for side in sides_text:
                    tokens.append(role + side)
            else:
                tokens.append(role)
    return tokens


def player_can_play(position_text, slot_name):
    """判断球员能否打某个阵型槽位"""
    return any(
        token in SLOT_COMPAT.get(slot_name, set())
        for token in parse_position_tokens(position_text)
    )


# ── RTF 文件解析 ────────────────────────────────────────
def read_roster_from_rtf(path=None):
    """
    读取 FM 导出的 RTF 表格文件，返回球员列表。
    每个球员包含：name, age, position, ca, pa
    path: RTF 文件路径，默认使用 RTF_PATH。
    """
    path = Path(path) if path else RTF_PATH
    if not path.exists():
        return []

    content = path.read_text(encoding="utf-8", errors="replace")
    lines = [line for line in content.split("\n")
             if "|" in line and "---" not in line]
    if not lines:
        return []

    # 解析表头，动态识别各列位置
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

    # 逐行解析球员数据
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


# ── EA 计算 ─────────────────────────────────────────────
def calculate_ea(players, growth_until_age=21, growth_per_year=20):
    """
    为每个球员计算 EA（预期能力）。
    EA = CA + 成长潜力，上限 PA，不超过 PA。
    growth_until_age: 成长停止的年龄（默认 21），可调。
    growth_per_year:  每岁成长值（默认 20），可调。
    """
    for player in players:
        if player["age"] >= growth_until_age:
            player["ea"] = player["ca"]
        else:
            growth = (growth_until_age - player["age"]) * growth_per_year
            player["ea"] = min(player["ca"] + growth, player["pa"])


# ── 阵容分配（匈牙利算法） ───────────────────────────
def select_best_xi(candidates, sort_key):
    """
    用匈牙利算法从候选球员中选出最优 11 人分配方案。
    
    成本设计（全正数，避免算法异常）：
      - 合法分配：max_key - player_key（范围 0 到 max_key-1）
      - 非法分配：远大于所有合法成本之和，确保算法优先选合法
    """
    import numpy as np
    from scipy.optimize import linear_sum_assignment

    slot_count = len(SLOTS)
    max_key = max(p[sort_key] for p in candidates) if candidates else 200
    huge_penalty = max_key * slot_count * 2 + 1  # 远超 11 个合法成本之和

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
    """筛选年龄 >= 17 的球员"""
    return [p for p in players if p["age"] >= 17]


def build_depth_chart(players):
    """为每个槽位生成深度图数据（全量球员，不限已用，允许重复）。"""
    depth = {}
    for slot in DEPTH_SLOTS:
        eligible = [p for p in players if player_can_play(p["position"], slot)]
        eligible.sort(key=lambda p: p["ea"], reverse=True)
        n = 6 if slot in ("DM", "DC") else (3 if slot == "GK" else 4)
        depth[slot] = eligible[:n]
    return depth


# ── HTML 生成 ───────────────────────────────────────────
def compute_average(xi, sort_key):
    """计算阵容的平均分"""
    return int(sum(p[sort_key] for _, p in xi) / len(xi)) if xi else 0


def render_player_slot(slot_name, player, sort_key, reference_value):
    """渲染单个球员槽位的 HTML"""
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
    渲染 11 人球场阵型图。
    行排列：ST / AML,AMC,AMR / DM,DM / DL,DC,DC,DR / GK
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
    """渲染一张完整的球场卡片"""
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
    补强分：分数越高越需要补强。每个位置最多 4 分。
    首发该位置有红色 +2，次佳该位置有红色 +1，深度红色比例（保留 1 位小数）。
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
    """渲染深度图（2 列纵向分区布局，按补强分降序排列）"""
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


# ── 主流程 ──────────────────────────────────────────────
def analyze(roster, output=None, growth_until_age=21, growth_per_year=20):
    """
    核心分析流程：计算 EA、分配两套 EA 最佳 11 人、生成深度图。
    roster: 球员 dict 列表，每个含 name/age/position/ca/pa。
    output: 输出的 HTML 路径，默认 OUTPUT。
    growth_until_age: EA 成长停止年龄（默认 21）。
    growth_per_year:  EA 每岁成长值（默认 20）。
    返回 HTML 字符串；roster 为空返回 None。
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
