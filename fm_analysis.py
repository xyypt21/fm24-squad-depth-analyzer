"""
FM2024 4-2-3-1 squad depth analysis core.

Computes EA (Expected Ability) for every player and uses the Hungarian algorithm
to pick the two best non-overlapping starting XIs by EA, then renders an HTML
report. Weak-slot highlighting uses the first XI's average EA as reference,
at 90% / 85% for the two XIs respectively. Also
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
import ssl
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from urllib.parse import quote

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
    }


def save_config(config: Dict[str, Any], path: Optional[Path] = None) -> None:
    """Write the config back to the file."""
    path = Path(path) if path else CONFIG_PATH
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


# ── Formation slots and FM position parsing ──────────────
# 4-2-3-1 starting XI, ordered top-to-bottom as the pitch is drawn.
SLOTS: List[str] = ["GK", "DL", "DC", "DC", "DR", "DMC", "DMC", "AML", "AMC", "AMR", "STC"]

# 22 人：每个位置两个槽位（首发 + 替补），一次匈牙利算法整体最优选出。
# 槽位顺序 = 两套相同阵型，前 11 为首发组、后 11 为替补组；渲染时按 EA 归位。
SLOTS_22: List[str] = SLOTS + SLOTS

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

# 单次请求超时（代理不通会无限阻塞）
_REQUEST_TIMEOUT = 10
# 单批翻译失败重试次数（代理/网关偶发 SSL 断连，重试通常即成功）
_RETRIES = 3


def _proxies():
    if not PROXY_URL:
        return None
    return {"http": PROXY_URL, "https": PROXY_URL}


def _translate_batch(names, proxies):
    """把整批名字每行一个 "player:xxx" 拼成一段一次翻译，再按行拆回。

    走 Google gtx 公开端点，逐段拆回 originalText 与译文对齐。
    逐行 "player:" 前缀让 Google 把每行当球员名处理，能正确识别名/姓、保留
    间隔号（·）。保持原始大小写。返回 {原名: 中文}；译文与原名相同或异常时
    不返回该名字。
    """
    if not names:
        return {}
    text = "\n".join("player:" + n for n in names)
    try:
        url = (
            "https://translate.googleapis.com/translate_a/single"
            "?client=gtx&sl=en&tl=zh-CN&dt=t&q="
            + quote(text)
        )
        proxy_handler = urllib.request.ProxyHandler(proxies or {})
        # gtx 是公开端点，无证书链校验，避免个别代理/网关打断 TLS
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        https_handler = urllib.request.HTTPSHandler(context=ctx)
        opener = urllib.request.build_opener(proxy_handler, https_handler)
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0", "Accept-Encoding": "identity"}
        )
        with opener.open(req, timeout=_REQUEST_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        # 网络/代理/SSL 异常一律静默：翻译失败时保留英文原名，不阻塞分析
        return {}
    if not data or not data[0]:
        return {}
    # 每行译文都带 "球员：/玩家：" 前缀，剥掉后与原名逐行对应
    lines = []
    for seg in data[0]:
        if not seg or not seg[0]:
            continue
        line = seg[0].strip()
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


def _translate_xi_names(*groups, translate: bool = True) -> None:
    """把入选名单的球员名翻译成中文。

    translate: 为 False 时原样保留英文名。
    启用时：对多组名单去重收集名字，整批一次走 Google 在线翻译（经本地代理），
    就地改 player dict 的 name，未命中的保持原样。
    groups: 每组是 (位置, 球员) 元组的 XI，或纯球员 dict 列表（如成熟球员榜）。
    """
    if not translate:
        return
    players = [
        item if isinstance(item, dict) else item[1]
        for group in groups
        for item in group
    ]
    seen = set()
    names = []
    for p in players:
        if p["name"] not in seen:
            seen.add(p["name"])
            names.append(p["name"])
    if not names:
        return
    translated = auto_translate(names)
    for p in players:
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
def select_best_xi(candidates: List[dict], sort_key: str, slots: List[str] = None) -> List[tuple]:
    """
    Use the Hungarian algorithm to pick the optimal assignment.

    Cost design (all positive for stable solving):
      - Valid assignment: max_key - player_key (range 0 to max_key-1)
      - Invalid assignment: far larger than any valid cost sum, so the
        algorithm prefers valid lineups.
    slots: slot names to fill (default SLOTS, 11 人；传 SLOTS_22 则一次选 22 人)。
    无效分配（找不到能打该位置的球员）会被丢弃，对应槽位留空，绝不把
    不能打该位置的球员硬塞进去。
    """
    slots = slots or SLOTS
    slot_count = len(slots)
    max_key = max(p[sort_key] for p in candidates) if candidates else 200
    huge_penalty = max_key * slot_count * 2 + 1  # far beyond any valid cost sum

    from scipy.optimize import linear_sum_assignment  # noqa: PLC0415 延迟导入，加快启动

    cost = np.full((slot_count, len(candidates)), huge_penalty, dtype=np.int32)
    for slot_i, slot_name in enumerate(slots):
        for player_i, player in enumerate(candidates):
            if player_can_play(player["position"], slot_name):
                cost[slot_i, player_i] = max_key - player[sort_key]

    row_indices, col_indices = linear_sum_assignment(cost)
    result = []
    for row, col in zip(row_indices, col_indices):
        if cost[row, col] < huge_penalty:
            result.append((slots[row], candidates[col]))
    return result


# ── HTML report rendering ─────────────────────────────────
def compute_average(xi, sort_key):
    """Compute the average score of a starting XI."""
    return int(sum(p[sort_key] for _, p in xi) / len(xi)) if xi else 0


def is_weak(player_ea, reference, ratio):
    return player_ea < reference * ratio


def render_player_slot(slot_name, player, sort_key, reference_value, ratio, sub=False):
    """Render the HTML for a single player slot. sub=True 表示替补（次佳）槽位。"""
    weak = " slot-weak" if is_weak(player[sort_key], reference_value, ratio) else ""
    subcls = " slot-sub" if sub else ""
    return (
        f"<div class='slot{weak}{subcls}'>"
        f"<div class='slot-label'>{slot_name}{'②' if sub else ''}</div>"
        f"<div class='p-name'>{player['name']}</div>"
        f"<div class='p-stat'>{player['age']:.1f}岁 · CA{player['ca']} · EA{player['ea']:.0f}</div>"
        f"</div>"
    )


def render_pitch_22(ea_first, ea_second, sort_key, reference, ratio=0.9):
    """Render a single 22-man pitch: each position shows first XI + second XI.

    同一位置的首发/替补叠在一个 slot 内（首发实线、替补虚线），
    弱项高亮：首发/替补统一按 ratio，均以首发均 EA 为基准。
    ea_first/ea_second 是按位置归位的 (slot, player) 列表，按 SLOTS 顺序排列，
    每位置取序列里第 occ 人；某位置替补不足时该槽不渲染替补。
    """
    # 按位置分组（保持 SLOTS 内的出现顺序）
    first_by_slot: Dict[str, List[dict]] = {}
    second_by_slot: Dict[str, List[dict]] = {}
    for slot_name, p in ea_first:
        first_by_slot.setdefault(slot_name, []).append(p)
    for slot_name, p in ea_second:
        second_by_slot.setdefault(slot_name, []).append(p)

    def slot_html(index):
        slot_name = SLOTS[index]
        occ = sum(1 for j in range(index) if SLOTS[j] == slot_name)
        firsts = first_by_slot.get(slot_name, [])
        seconds = second_by_slot.get(slot_name, [])
        player = firsts[occ] if occ < len(firsts) else None
        sub_player = seconds[occ] if occ < len(seconds) else None
        if player is None and sub_player is None:
            return "<div class='slot' style='visibility:hidden;'></div>"
        parts = []
        if player is None:
            parts.append("<div class='slot' style='visibility:hidden;'></div>")
        else:
            parts.append(render_player_slot(slot_name, player, sort_key, reference, ratio))
        if sub_player is not None:
            parts.append(render_player_slot(slot_name, sub_player, sort_key, reference, ratio, sub=True))
        return f"<div class='slot-stack'>" + "".join(parts) + "</div>"

    row_indices = [(10,), (7, 8, 9), (5, 6), (1, 2, 3, 4), (0,)]
    rows = [
        "<div class='f-row'>" + "".join(slot_html(i) for i in indices) + "</div>"
        for indices in row_indices
    ]
    return "\n".join(rows)


def render_pitch_22_card(ea_first, ea_second, sort_key, ratio=0.9):
    """Render a single 22-man pitch card."""
    if not ea_first:
        return (
            "<div class='pitch' style='display:flex;align-items:center;"
            "justify-content:center;color:rgba(255,255,255,0.5);font-size:14px;'>"
            "球员不足</div>"
        )
    reference = compute_average(ea_first, sort_key)
    return (
        f"<div class='pitch'>"
        f"{render_pitch_22(ea_first, ea_second, sort_key, reference, ratio)}"
        f"</div>"
    )


def select_squad(candidates: List[dict], key: str):
    """匈牙利一次选 22 人 + 按位置归位，返回 (首发, 替补)。

    同一位置多槽成本等价，求解器不区分首发/替补；按位置归位：
    每个位置（SLOTS 里出现 k 次）取其实际分配到槽位的球员，key 前 k 名进首发、
    其余进替补；某位置没有合适球员时该位置槽位留空（不出现在返回里）。
    """
    picks = select_best_xi(candidates, key, SLOTS_22)
    by_pos: Dict[str, List[tuple]] = {}
    for slot_name, p in picks:
        by_pos.setdefault(slot_name, []).append((slot_name, p))
    ea_first = []
    ea_second = []
    # 按去重位置遍历：每个位置（在 SLOTS 出现 k 次）最多 2k 个槽位。
    # 不能直接 for slot in SLOTS——DC/DMC 重复出现会整组重复入队。
    for slot in dict.fromkeys(SLOTS):
        k = SLOTS.count(slot)
        group = sorted(by_pos.get(slot, []), key=lambda t: t[1][key], reverse=True)
        ea_first.extend(group[:k])
        ea_second.extend(group[k : k * 2])
    return ea_first, ea_second


def render_remaining_list(players: List[dict]) -> str:
    """渲染右侧"剩余能力前11"：按 CA 排序的未入选球员列表。"""
    if not players:
        return "<div class='remaining'>无剩余球员</div>"
    rows = []
    for rank, p in enumerate(players, 1):
        rows.append(
            f"<div class='r-row'>"
            f"<span class='r-rank'>{rank}</span>"
            f"<span class='r-name'>{p['name']}</span>"
            f"<span class='r-pos'>{p['position']}</span>"
            f"<span class='r-age'>{p['age']:.0f}岁</span>"
            f"<span class='r-stat'>CA{p['ca']}</span>"
            f"<span class='r-ea'>EA{p['ea']:.0f}</span>"
            f"</div>"
        )
    return "<div class='remaining'>" + "".join(rows) + "</div>"


def generate_full_html(
    ea_first, ea_second, remaining=None, ratio=0.9, ca_threshold=None
):
    template_dir = Path(__file__).parent / "templates"
    template = (template_dir / "report.html").read_text(encoding="utf-8")
    css = (template_dir / "style.css").read_text(encoding="utf-8")
    return (
        template.replace("{{CSS_STYLE}}", css)
        .replace(
            "{{PITCH_22}}",
            render_pitch_22_card(ea_first, ea_second, "ea", ratio),
        )
        .replace(
            "{{REMAINING_PLAYERS}}",
            render_remaining_list(remaining or []),
        )
        .replace("{{AVG_EA_BEST}}", str(compute_average(ea_first, "ea")))
        .replace("{{AVG_EA_SECOND}}", str(compute_average(ea_second, "ea")))
        .replace(
            "{{CA_THRESHOLD}}",
            str(int(round(ca_threshold))) if ca_threshold is not None else "",
        )
    )


# ── Main flow ──────────────────────────────────────────────
def compute_ca_threshold(candidates: List[dict]) -> int:
    """门槛 = 除门将外按 CA 降序第 30 人的 CA；不足 30 人取最低者。

    门将单独算（GK 是独立位置，不占外场名额），所以外场球员排到第 30 名
    的 CA 作为"值得进入阵容深度分析"的下限。
    """
    field = [p for p in candidates if "GK" not in parse_position_tokens(p["position"])]
    field.sort(key=lambda p: p["ca"], reverse=True)
    if not field:
        return 0
    return field[min(29, len(field) - 1)]["ca"]


def analyze(
    roster: List[dict],
    output: Optional[Path] = None,
    min_age: int = 17,
    growth_until_age: int = 21,
    growth_per_year: int = 20,
    translate: bool = True,
    ratio: float = 0.9,
) -> Optional[str]:
    """
    Core analysis flow: compute EA, then pick the two best non-overlapping
    starting XIs by EA.
    roster: list of player dicts, each with name/age/position/ca/pa.
    output: HTML output path, defaults to OUTPUT.
    min_age: only players aged >= min_age are considered (default 17).
    growth_until_age: age at which EA growth stops (default 21).
    growth_per_year:  EA growth per year of age (default 20).
    translate: translate selected player names to Chinese (default True).
    ratio: weak-slot threshold as a fraction of the first XI's average EA,
    applied to both XIs (default 0.9).
    Returns the HTML string, or None if roster is empty.
    """
    candidates = list(roster)
    if not candidates:
        print("未找到阵容数据")
        return None

    # 门槛 = 除门将外按 CA 降序第 30 人的 CA，低于门槛的球员不纳入 EA 计算
    ca_threshold = compute_ca_threshold(candidates)
    ea_candidates = [p for p in candidates if p["ca"] >= ca_threshold]
    calculate_ea(ea_candidates, growth_until_age=growth_until_age, growth_per_year=growth_per_year)
    ea_first, ea_second = select_squad(ea_candidates, "ea")

    # 剩余球员 = 达门槛但未入选 EA 22 人者，按 CA 排序取前 11
    chosen_ids = {id(p) for _s, p in ea_first} | {id(p) for _s, p in ea_second}
    remaining = sorted(
        (p for p in ea_candidates if id(p) not in chosen_ids),
        key=lambda p: p["ca"],
        reverse=True,
    )[:11]

    # 只翻译最终出现在网页（入选阵容 + 剩余榜）里的球员名字，避免多余请求
    _translate_xi_names(ea_first, ea_second, remaining, translate=translate)

    html = generate_full_html(
        ea_first,
        ea_second,
        remaining=remaining,
        ratio=ratio,
        ca_threshold=ca_threshold,
    )
    out = output or OUTPUT
    out.write_text(html, encoding="utf-8")
    return html
