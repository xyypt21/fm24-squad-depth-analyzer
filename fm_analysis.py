"""
FM2024 4-2-3-1 squad depth analysis core.

Computes EA (Expected Ability) for every player and uses the Hungarian algorithm
to pick the three best non-overlapping starting XIs by EA, then renders an HTML
report. Weak-slot highlighting uses the first XI's average EA as reference,
at 90% / 85% / 80% for the three XIs respectively. Also
loads/saves the config file, parses FM position strings,
and optionally translates selected player names to Chinese.

EA (Expected Ability) = CA + growth potential
  age < growth_until_age  EA = CA + (growth_until_age - age) × growth_per_year
  age >= growth_until_age EA = CA
  EA capped at PA
"""

import json
import os
import re
import socket
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import numpy as np

OUTPUT = Path(__file__).parent / "fm_analysis.html"
CONFIG_PATH = Path(__file__).parent / "config.json"
DEFAULT_CONFIG: Dict[str, Any] = {
    "club_uid": 920,
    "min_age": 17,
    "growth_until_age": 21,
    "growth_per_year": 20,
    "translate_names": False,
    "merge_club2": False,
    "club2_uid": 0,
    "ratio_best": 0.9,
    "ratio_second": 0.85,
    "ratio_third": 0.8,
}


# ── Config file loading/saving ────────────────────────────
def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    """Read the config file, falling back to defaults if missing or corrupt."""
    path = Path(path) if path else CONFIG_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return dict(DEFAULT_CONFIG)
    merged = {**DEFAULT_CONFIG, **data}
    return {
        "club_uid": int(merged["club_uid"]),
        "min_age": int(merged["min_age"]),
        "growth_until_age": int(merged["growth_until_age"]),
        "growth_per_year": int(merged["growth_per_year"]),
        "translate_names": bool(merged["translate_names"]),
        "merge_club2": bool(merged["merge_club2"]),
        "club2_uid": int(merged["club2_uid"]),
        "ratio_best": float(merged["ratio_best"]),
        "ratio_second": float(merged["ratio_second"]),
        "ratio_third": float(merged["ratio_third"]),
    }


def save_config(config: Dict[str, Any], path: Optional[Path] = None) -> None:
    """Write the config back to the file."""
    path = Path(path) if path else CONFIG_PATH
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


# ── Formation slots and FM position parsing ──────────────
# 4-2-3-1 starting XI, ordered top-to-bottom as the pitch is drawn.
SLOTS: List[str] = ["GK", "DL", "DC", "DC", "DR", "DMC", "DMC", "AML", "AMC", "AMR", "STC"]

# Valid FM role names and side letters.
VALID_ROLES: Set[str] = {"GK", "D", "WB", "DM", "M", "AM", "ST"}
VALID_SIDES: Set[str] = {"C", "L", "R"}

# Full valid position token set (role + side combinations).
VALID_POSITION_TOKENS: Set[str] = {
    "GK",
    "WBL",
    "WBR",
    "DMC",
    "STC",
    *(r + s for r in ("D", "M", "AM") for s in VALID_SIDES),
}


def parse_position_tokens(position_text: str) -> List[str]:
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
    tokens: List[str] = []
    # Grab each "role(sides)" chunk in one regex pass, skipping commas/spaces.
    # E.g. "M (L), AM (RLC)" yields (M, L) then (AM, RLC)
    for roles_text, sides_text in re.findall(
        r"([A-Z/]+)\s*(?:\(([A-Z]+)\))?", position_text.upper()
    ):
        # Handle compound roles like D/WB, taking D and WB one by one.
        for role in roles_text.split("/"):
            if role not in VALID_ROLES:
                continue
            if sides_text:
                # Sides in parentheses: validate them, then build one token each.
                if all(s in VALID_SIDES for s in sides_text):
                    tokens.extend(role + s for s in sides_text)
            else:
                # No parentheses: GK stays GK, other roles treated as centre,
                # e.g. DM -> DMC.
                tokens.append("GK" if role == "GK" else role + "C")
    # Safety filter: drop any token not in the valid position table.
    return [t for t in tokens if t in VALID_POSITION_TOKENS]


def player_can_play(position_text: str, slot_name: str) -> bool:
    """Check whether a player can fill a formation slot."""
    return slot_name in parse_position_tokens(position_text)


# ── Online name translation (Google via local proxy) ─────
# 进程内在线翻译缓存与失败黑名单（避免重复请求）
_cache: dict = {}
_failed: set = set()

# 本地代理（Clash 等默认端口 7897），走境外 Google 翻译必需。
# 可通过环境变量 FM_PROXY 覆盖，空字符串/None 表示不走代理。
PROXY_URL = os.environ.get("FM_PROXY", "http://127.0.0.1:7897")

# 单次请求超时（deep-translator 未显式设 timeout，代理不通会无限阻塞）
_REQUEST_TIMEOUT = 10
# 单个名字失败重试次数
_RETRIES = 2


def _proxies():
    if not PROXY_URL:
        return None
    return {"http": PROXY_URL, "https": PROXY_URL}


def _translate_batch(names, proxies):
    """把整批名字每行一个 "player:xxx" 拼成一段一次翻译，再按行拆回。

    逐行 "player:" 前缀让 Google 把每行当球员名处理，能正确识别名/姓、保留
    间隔号（·），且不会像 "name:" 那样把个别名字原样返回。
    保持原始大小写。返回 {原名: 中文}；翻译异常/未命中的名字不返回。
    """
    if not names:
        return {}
    text = "\n".join("player:" + n for n in names)
    try:
        from deep_translator import GoogleTranslator  # noqa: PLC0415 延迟导入，加快启动

        # deep-translator 未设 socket 超时，代理不通会无限阻塞；设全局默认超时兜底
        _previous = socket.getdefaulttimeout()
        socket.setdefaulttimeout(_REQUEST_TIMEOUT)
        try:
            out = GoogleTranslator(source="en", target="zh-CN", proxies=proxies).translate(text)
        finally:
            socket.setdefaulttimeout(_previous)
    except ImportError:
        return {}
    if not out:
        return {}

    # 每行剥掉 "玩家：/球员：/姓名：/名称：" 前缀后与原名逐行对应
    lines = []
    for line in out.split("\n"):
        line = line.strip()
        for sep in ("：", ":"):
            if sep in line:
                line = line.split(sep, 1)[1].strip()
                break
        if line:
            lines.append(line)
    result = {}
    for name, line in zip(names, lines):
        if line and line != name:
            result[name] = line
    return result


def _batch_translate(names):
    """批量翻译一批名字：一次请求整批走 Google 翻译（经本地代理）。

    保持原始大小写（小写会丢失名/姓间的间隔号 ·），
    返回 {原名: 中文}；翻译异常/未命中的名字不返回（由调用方原样保留）。
    只走内存缓存，不写文件。
    """
    todo = []
    result = {}
    for n in names:
        if n in _cache:
            result[n] = _cache[n]
        elif n not in _failed:
            todo.append(n)
    if not todo:
        return result

    proxies = _proxies()
    translated = {}
    for _ in range(_RETRIES):
        translated = _translate_batch(todo, proxies)
        if translated:
            break

    for name, cn in translated.items():
        _cache[name] = cn
        result[name] = cn
    return result


def auto_translate(names):
    """翻译一批英文名，整批一次走 Google 在线翻译（经本地代理）。"""
    names = tuple(dict.fromkeys(names))
    if not names:
        return {}
    return _batch_translate(names)


def _translate_xi_names(*xis, translate: bool = True) -> None:
    """把入选 XI 的球员名翻译成中文。

    translate: 为 False 时原样保留英文名。
    启用时：对多组 XI 去重收集名字，整批一次走 Google 在线翻译（经本地代理），
    就地改 player dict 的 name，未命中的保持原样。
    """
    if not translate:
        return
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


# ── HTML report rendering ─────────────────────────────────
def compute_average(xi, sort_key):
    """Compute the average score of a starting XI."""
    return int(sum(p[sort_key] for _, p in xi) / len(xi)) if xi else 0


def is_weak(player_ea, reference, ratio):
    return player_ea < reference * ratio


def render_player_slot(slot_name, player, sort_key, reference_value, ratio):
    """Render the HTML for a single player slot."""
    weak = " slot-weak" if is_weak(player[sort_key], reference_value, ratio) else ""
    return (
        f"<div class='slot{weak}'>"
        f"<div class='slot-label'>{slot_name}</div>"
        f"<div class='p-name'>{player['name']}</div>"
        f"<div class='p-stat'>{player['age']:.1f}岁 · CA{player['ca']} · EA{player['ea']:.0f}</div>"
        f"</div>"
    )


def render_pitch(xi, sort_key, reference_value, ratio):
    """
    Render the 11-man pitch diagram.
    Row layout: STC / AML,AMC,AMR / DMC,DMC / DL,DC,DC,DR / GK
    """

    def slot_html(index):
        if index >= len(xi):
            return "<div class='slot' style='visibility:hidden;'></div>"
        slot_name, player = xi[index]
        return render_player_slot(slot_name, player, sort_key, reference_value, ratio)

    row_indices = [(10,), (7, 8, 9), (5, 6), (1, 2, 3, 4), (0,)]
    rows = [
        "<div class='f-row'>" + "".join(slot_html(i) for i in indices) + "</div>"
        for indices in row_indices
    ]
    return "\n".join(rows)


def render_pitch_card(xi, sort_key, reference=None, ratio=0.9):
    """Render a full pitch card."""
    average = compute_average(xi, sort_key)
    if not xi:
        return (
            "<div class='pitch' style='display:flex;align-items:center;"
            "justify-content:center;color:rgba(255,255,255,0.5);font-size:14px;'>"
            "球员不足</div>"
        )
    return f"<div class='pitch'>{render_pitch(xi, sort_key, reference or average, ratio)}</div>"


def generate_full_html(
    ea_first,
    ea_second,
    ea_third,
    ratio_best=0.9,
    ratio_second=0.85,
    ratio_third=0.8,
):
    template_dir = Path(__file__).parent / "templates"
    template = (template_dir / "report.html").read_text(encoding="utf-8")
    css = (template_dir / "style.css").read_text(encoding="utf-8")
    ref_ea = compute_average(ea_first, "ea")
    return (
        template.replace("{{CSS_STYLE}}", css)
        .replace(
            "{{PITCH_EA_BEST}}",
            render_pitch_card(ea_first, "ea", reference=ref_ea, ratio=ratio_best),
        )
        .replace(
            "{{PITCH_EA_SECOND}}",
            render_pitch_card(ea_second, "ea", reference=ref_ea, ratio=ratio_second),
        )
        .replace(
            "{{PITCH_EA_THIRD}}",
            render_pitch_card(ea_third, "ea", reference=ref_ea, ratio=ratio_third),
        )
        .replace("{{AVG_EA_BEST}}", str(compute_average(ea_first, "ea")))
        .replace("{{AVG_EA_SECOND}}", str(compute_average(ea_second, "ea")))
        .replace("{{AVG_EA_THIRD}}", str(compute_average(ea_third, "ea")))
    )


# ── Main flow ──────────────────────────────────────────────
def analyze(
    roster: List[dict],
    output: Optional[Path] = None,
    min_age: int = 17,
    growth_until_age: int = 21,
    growth_per_year: int = 20,
    translate: bool = True,
    ratio_best: float = 0.9,
    ratio_second: float = 0.85,
    ratio_third: float = 0.8,
) -> Optional[str]:
    """
    Core analysis flow: compute EA, then pick the three best non-overlapping
    starting XIs by EA.
    roster: list of player dicts, each with name/age/position/ca/pa.
    output: HTML output path, defaults to OUTPUT.
    min_age: only players aged >= min_age are considered (default 17).
    growth_until_age: age at which EA growth stops (default 21).
    growth_per_year:  EA growth per year of age (default 20).
    translate: translate selected player names to Chinese (default True).
    ratio_best/second/third: weak-slot threshold as a fraction of the first
    XI's average EA (defaults 0.9 / 0.85 / 0.8).
    Returns the HTML string, or None if roster is empty.
    """
    candidates = [p for p in roster if p["age"] >= min_age]
    if not candidates:
        print("未找到阵容数据")
        return None

    calculate_ea(candidates, growth_until_age=growth_until_age, growth_per_year=growth_per_year)

    ea_first = select_best_xi(candidates, "ea")
    used_ea = {id(p) for _, p in ea_first}
    ea_second = select_best_xi([p for p in candidates if id(p) not in used_ea], "ea")
    used_ea |= {id(p) for _, p in ea_second}
    ea_third = select_best_xi([p for p in candidates if id(p) not in used_ea], "ea")

    # 只翻译最终出现在网页（入选阵容）里的球员名字，避免多余请求
    _translate_xi_names(ea_first, ea_second, ea_third, translate=translate)

    html = generate_full_html(
        ea_first,
        ea_second,
        ea_third,
        ratio_best=ratio_best,
        ratio_second=ratio_second,
        ratio_third=ratio_third,
    )
    out = output or OUTPUT
    out.write_text(html, encoding="utf-8")
    return html
