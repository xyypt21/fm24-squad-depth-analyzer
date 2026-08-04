"""
FM2024 4-2-3-1 阵容深度分析工具。

解析 RTF 阵容 → 计算 EA → 用匈牙利算法选出 EA 最佳与次佳 11 人。

EA（预期能力）= CA + 成长潜力
  年龄 < 21  EA = CA + (21 - 年龄) × 20
  年龄 ≥ 21   EA = CA
  EA 不超过 PA
"""

import json
import os
import re
import warnings
from pathlib import Path

import numpy as np
from scipy.optimize import linear_sum_assignment

# ── 路径与配置 ──────────────────────────────────────────
OUTPUT = Path(__file__).parent / "fm_analysis.html"
CONFIG_PATH = Path(__file__).parent / "config.json"
DEFAULT_CONFIG = {
    "rtf_path": r"C:\Users\xyy\Documents\Sports Interactive\Football Manager 2024\team.rtf",
    "growth_until_age": 21,
    "growth_per_year": 20,
}


# ── 配置文件 ────────────────────────────────────────────
def load_config(path=None):
    """读取配置文件，缺失或损坏时回退到默认值。"""
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
    """将配置写回文件。"""
    path = Path(path) if path else CONFIG_PATH
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


# ── 阵型槽位 ────────────────────────────────────────────
SLOTS = ["GK", "DL", "DC", "DC", "DR", "DMC", "DMC", "AML", "AMC", "AMR", "STC"]

# 深度图槽位：由 SLOTS 自动推导（按首现顺序去重，并记录每个槽位的下标）
# 说明：SLOTS 是 11 人阵型（含重复的 DC/DMC），而补强分/深度图按"位置"统计，
#       需用 DEPTH_SLOT_MAP 把位置翻译成 ea_first/ea_second 列表（按 SLOTS 顺序）中的下标。
#       例如 DEPTH_SLOT_MAP["DC"] == [2, 3] 表示两个中卫位于 11 人列表的下标 2 和 3。
DEPTH_SLOT_MAP = {}
for i, slot in enumerate(SLOTS):
    DEPTH_SLOT_MAP.setdefault(slot, []).append(i)
DEPTH_SLOTS = list(DEPTH_SLOT_MAP)


# ── 位置解析 ────────────────────────────────────────────
# 合法的 FM 位置角色与侧面字母
VALID_ROLES = {"GK", "D", "WB", "DM", "M", "AM", "ST"}
VALID_SIDES = {"C", "L", "R"}

# 全部合法的位置 token（FM 完整位置列表）
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
    将 FM 位置字符串拆成"角色+侧面"组合 token，只保留合法位置。
    无括号的角色按中路（+C）处理，例如 "DM" → ["DMC"]。

    例子：
      "M (L), AM (RLC)"     → ["ML", "AMR", "AML", "AMC"]
      "D/WB (R)"            → ["DR", "WBR"]
      "ST (C)"              → ["STC"]
      "DM"                  → ["DMC"]
      "GK"                  → ["GK"]
    """
    tokens = []
    # 用正则一次性抓取每段"角色(侧面)"，自动跳过逗号与空格。
    # 例如 "M (L), AM (RLC)" 依次抓到 (M, L) 和 (AM, RLC)
    for roles_text, sides_text in re.findall(
        r"([A-Z/]+)\s*(?:\(([A-Z]+)\))?", position_text.upper()
    ):
        # 处理 D/WB 这类复合角色，逐个取 D、WB
        for role in roles_text.split("/"):
            # 角色不在合法位置里（如 XX）则跳过
            if role not in VALID_ROLES:
                continue
            if sides_text:
                # 有括号侧面：先校验侧面字母合法，再逐个拼成 token
                if all(s in VALID_SIDES for s in sides_text):
                    tokens.extend(role + s for s in sides_text)
            else:
                # 无括号：GK 保持 GK，其它角色按中路(+C)处理，如 DM -> DMC
                tokens.append("GK" if role == "GK" else role + "C")
    # 兜底过滤：不在合法位置表里的 token 一律丢弃
    return [t for t in tokens if t in VALID_POSITION_TOKENS]


def player_can_play(position_text, slot_name):
    """判断球员能否胜任某个阵型槽位。"""
    return slot_name in parse_position_tokens(position_text)


# ── RTF 文件解析 ────────────────────────────────────────
def _clean(text):
    """去掉零宽空格并去除首尾空白（FM 导出表格常带 \u200b）。"""
    return "".join(c for c in text if c != "\u200b").strip()


def _num(text):
    """提取单元格中的数字部分。"""
    return int("".join(c for c in text if c.isdigit()))


def _parse_columns(headers):
    """根据表头识别各列下标（能力列导出两次，取第二个作为 CA）。"""
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
    读取 FM 导出的 RTF 表格，返回球员列表。
    每个球员包含：name, age, position, ca, pa。
    path: RTF 文件路径，默认读取配置文件中的路径。
    """
    path = Path(path) if path else Path(load_config()["rtf_path"])
    if not path.exists():
        return []

    content = path.read_text(encoding="utf-8", errors="replace")
    lines = [line for line in content.split("\n") if "|" in line and "---" not in line]
    if not lines:
        return []

    # 解析表头，动态定位各列
    headers = [_clean(cell) for cell in lines[0].split("|")]
    col_index = _parse_columns(headers)

    # 逐行解析球员数据
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


# ── EA 计算 ─────────────────────────────────────────────
def calculate_ea(players, growth_until_age=21, growth_per_year=20):
    """
    为每个球员计算 EA（预期能力）。
    EA = CA + 成长潜力，不超过 PA。
    growth_until_age: 成长停止年龄（默认 21），可配置。
    growth_per_year:  每岁成长值（默认 20），可配置。
    """
    for player in players:
        if player["age"] >= growth_until_age:
            player["ea"] = player["ca"]
        else:
            growth = (growth_until_age - player["age"]) * growth_per_year
            player["ea"] = min(player["ca"] + growth, player["pa"])


# ── 阵容分配（匈牙利算法） ──────────────────────────────
def select_best_xi(candidates, sort_key):
    """
    用匈牙利算法从候选中选出最优 11 人分配方案。

    成本设计（全部为正数，保证算法稳定）：
      - 合法分配：max_key - player_key（范围 0 到 max_key-1）
      - 非法分配：远大于所有合法成本之和，确保算法优先选合法方案
    """
    slot_count = len(SLOTS)
    max_key = max(p[sort_key] for p in candidates) if candidates else 200
    huge_penalty = max_key * slot_count * 2 + 1  # 远超合法成本之和

    cost = np.full((slot_count, len(candidates)), huge_penalty, dtype=np.int32)
    for slot_i, slot_name in enumerate(SLOTS):
        for player_i, player in enumerate(candidates):
            if player_can_play(player["position"], slot_name):
                cost[slot_i, player_i] = max_key - player[sort_key]

    row_indices, col_indices = linear_sum_assignment(cost)
    return [(SLOTS[row], candidates[col]) for row, col in zip(row_indices, col_indices)]


def filter_senior_players(players):
    """筛选年龄 >= 17 的球员。"""
    return [p for p in players if p["age"] >= 17]


def build_depth_chart(players):
    """为每个槽位生成深度图数据（全量球员，允许重复出场）。"""
    depth = {}
    for slot in DEPTH_SLOTS:
        eligible = [p for p in players if player_can_play(p["position"], slot)]
        eligible.sort(key=lambda p: p["ea"], reverse=True)
        n = 6 if slot in ("DMC", "DC") else (3 if slot == "GK" else 4)
        depth[slot] = eligible[:n]
    return depth


# ── 主流程 ──────────────────────────────────────────────
def analyze(roster, output=None, growth_until_age=21, growth_per_year=20):
    """
    核心分析流程：计算 EA、选出最佳与次佳 11 人、生成深度图。
    roster: 球员 dict 列表，每个含 name/age/position/ca/pa。
    output: HTML 输出路径，默认 OUTPUT。
    growth_until_age: EA 成长停止年龄（默认 21）。
    growth_per_year:  EA 每岁成长值（默认 20）。
    返回 HTML 字符串；roster 为空返回 None。
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
