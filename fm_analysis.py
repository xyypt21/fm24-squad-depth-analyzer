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
import ssl
import time
import urllib.error
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
# 每次都联网实时翻译，不使用任何缓存（不读盘、不写盘）。
# 本地代理（Clash 等默认端口 7897），走境外 Google 翻译必需。
# 可通过环境变量 FM_PROXY 覆盖，空字符串/None 表示不走代理。
PROXY_URL = os.environ.get("FM_PROXY", "http://127.0.0.1:7897")

# 单次请求超时（代理不通会无限阻塞）
_REQUEST_TIMEOUT = 10
# 翻译端点回退链：主端点（translate_a/single gtx）被 IP 限流时换备用端点
# （clients5 translate_a/t dict-chrome-ex，限流池独立），两者都挂才退避重试。
_TRANSLATE_URLS = (
    "https://translate.googleapis.com/translate_a/single"
    "?client=gtx&sl=en&tl=zh-CN&dt=t&q=",
    "https://clients5.google.com/translate_a/t"
    "?client=dict-chrome-ex&sl=en&tl=zh-CN&q=",
)
# 端点全被限流时的重试次数与指数退避基数（5/10 秒），
# 并尊重 Retry-After 头；限流窗口常达几十秒，短退避必然全灭。
_RETRIES = 2
_BACKOFF_BASE = 5.0


class _RateLimited(Exception):
    """翻译端点返回 429 时抛出，携带建议等待秒数（Retry-After，可能为 0）。"""

    def __init__(self, retry_after: float):
        super().__init__(f"HTTP 429, retry after {retry_after}s")
        self.retry_after = retry_after


def _proxies():
    if not PROXY_URL:
        return None
    return {"http": PROXY_URL, "https": PROXY_URL}


def _fetch_translate(url: str, proxies):
    """请求一个翻译端点，返回解析后的 JSON。

    429 抛 _RateLimited（带 Retry-After）；其余异常原样抛出由调用方处理。
    """
    proxy_handler = urllib.request.ProxyHandler(proxies or {})
    # 公开端点无证书链校验，避免个别代理/网关打断 TLS
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    opener = urllib.request.build_opener(
        proxy_handler, urllib.request.HTTPSHandler(context=ctx)
    )
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0", "Accept-Encoding": "identity"}
    )
    try:
        with opener.open(req, timeout=_REQUEST_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            # Retry-After 可能是秒数或 HTTP 日期，解析失败按 0 处理
            try:
                retry_after = float(exc.headers.get("Retry-After") or 0)
            except ValueError:
                retry_after = 0.0
            raise _RateLimited(retry_after) from exc
        raise


def _extract_lines(data) -> List[str]:
    """把端点响应统一拆成逐行译文。

    single 端点 (gtx): data[0] 是分段列表，每段 [dst, src, ...]，dst 即该行译文
        （自带换行分隔），把所有段的 dst 拼接后 splitlines 即得逐行译文；用顺序
        与输入名字对齐。
    t 端点 (clients5): data[0] 是字符串（可含换行）或字符串列表。
    """
    first = data[0]
    if isinstance(first, str):
        return first.splitlines()
    if isinstance(first, list) and first and isinstance(first[0], list):
        text = "".join(seg[0] for seg in first if seg and isinstance(seg[0], str))
        return text.splitlines()
    parts = []
    for item in first or []:
        if isinstance(item, str):
            parts.append(item)
        elif item and item[0]:
            parts.append(item[0])
    return "\n".join(parts).splitlines()


def _fetch_any(text: str, proxies):
    """沿端点回退链请求翻译，返回解析后的 JSON。

    主端点被限流/断连时自动换下一个（限流池独立，往往能成功）。
    全部端点都限流时抛最先的 _RateLimited；全部网络故障时返回 None。
    """
    rate_exc = None
    for base_url in _TRANSLATE_URLS:
        try:
            return _fetch_translate(base_url + quote(text), proxies)
        except _RateLimited as exc:
            rate_exc = rate_exc or exc
        except Exception:
            pass  # 断连/超时等瞬时故障：试下一个端点
    if rate_exc is not None:
        raise rate_exc
    return None


def _translate_batch(names, proxies):
    """把整批名字每行一个 "player:xxx" 拼成一段一次翻译，再按行拆回。

    逐行 "player:" 前缀让 Google 把每行当球员名处理，能正确识别名/姓、保留
    间隔号（·）。保持原始大小写。返回 {原名: 中文}；
    译文为空或与原文相同（如已是中文名）的名字不返回（保留英文名）。
    全部端点都限流时抛 _RateLimited 交给上层退避；全部网络故障或响应异常时
    静默返回 {}，不阻塞分析。
    """
    if not names:
        return {}
    data = _fetch_any("\n".join("player:" + n for n in names), proxies)
    if not data or not data[0]:
        return {}
    # 每行译文都带 "球员：/玩家：" 前缀，剥掉后与原名逐行对应
    result = {}
    for name, raw in zip(names, _extract_lines(data)):
        line = raw.strip()
        for sep in ("：", ":"):
            if sep in line:
                line = line.split(sep, 1)[1].strip()
                break
        # 仅缓存真正译出的名字；译文为空或与原文相同不写入缓存
        if line and line != name:
            result[name] = line
    return result


def _translate_with_retry(names, proxies):
    """带退避的整批翻译：限流按 Retry-After/指数退避，普通失败线性退避。"""
    translated = {}
    wait = 0.0
    for attempt in range(_RETRIES):
        try:
            translated = _translate_batch(names, proxies)
            # 普通失败（断连/空响应）：沿用线性退避
            wait = 1.0 * (attempt + 1)
        except _RateLimited as exc:
            # 限流：取 Retry-After 与指数退避的较大者
            wait = max(exc.retry_after, _BACKOFF_BASE * (2**attempt))
            translated = {}
        if translated:
            break
        if attempt + 1 < _RETRIES:
            time.sleep(wait)
    return translated


def _batch_translate(names):
    """批量翻译一批名字：一次请求整批走 Google 翻译（经本地代理）。

    不使用任何缓存，每次都实时联网翻译。保持原始大小写（小写会丢失
    名/姓间的间隔号 ·），返回 {原名: 中文}；翻译异常/未命中的名字
    不返回（由调用方原样保留英文名）。
    """
    if not names:
        return {}
    return _translate_with_retry(names, _proxies())


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
    """渲染右侧剩余球员列表（按给定排序 key 取前 11）。"""
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


# ── 位置池 + 替补表 ─────────────────────────────────────
def position_pool_size(slot_name: str) -> int:
    """某位置的池子大小：GK=3，双槽位(DC/DMC)=6，单槽位=4。"""
    if slot_name == "GK":
        return 3
    return SLOTS.count(slot_name) * 2 + 2


def compute_position_pool(candidates: List[dict]) -> Dict[str, List[dict]]:
    """每个位置取 EA 前 N 人组成池子（同一球员可属多个位置）。

    N = position_pool_size：GK=3、DC/DMC=6、其余=4。
    """
    pool: Dict[str, List[dict]] = {}
    for slot in dict.fromkeys(SLOTS):  # 保持阵型内位置顺序去重
        able = [p for p in candidates if player_can_play(p["position"], slot)]
        able.sort(key=lambda p: p["ea"], reverse=True)
        pool[slot] = able[: position_pool_size(slot)]
    return pool


def render_depth_table(
    pool: Dict[str, List[dict]], chosen_ids: set, reference: float = 0, ratio: float = 0.9
) -> str:
    """渲染替补表：每个位置只列出池子中的替补（未入选 22 人的球员）。

    chosen_ids: 匈牙利算法选出的 22 人 id 集合。
    弱项标红与最佳 22 人同逻辑：EA < reference * ratio 的球员名标红。
    """
    if not pool:
        return "<div class='remaining'>无数据</div>"
    rows = []
    for slot, players in pool.items():
        bench = [p for p in players if id(p) not in chosen_ids]
        # 无替补的位置不显示
        if not bench:
            continue
        gap = max(0, position_pool_size(slot) - len(players))
        status_cls = "d-ok" if not gap else "d-short"
        names = "、".join(
            (
                f"<span class='weak-name'>{p['name']}(CA{p['ca']}/EA{int(round(p['ea']))})</span>"
                if is_weak(p["ea"], reference, ratio)
                else f"{p['name']}(CA{p['ca']}/EA{int(round(p['ea']))})"
            )
            for p in bench
        ) or "无"
        rows.append(
            f"<div class='d-row {status_cls}'>"
            f"<div class='d-head'><span class='d-slot'>{slot}</span>"
            f"<span class='d-count'>替补{len(bench)}/{len(players)}</span>"
            f"{('<span class=\'d-gap\'>缺 ' + str(gap) + ' 人</span>') if gap else ''}</div>"
            f"<div class='d-players'>{names}</div>"
            f"</div>"
        )
    if not rows:
        return "<div class='remaining'>无替补球员</div>"
    return "<div class='remaining'>" + "".join(rows) + "</div>"


def render_sell_table(players: List[dict], reference: float = 0, ratio: float = 0.9) -> str:
    """渲染可卖榜：既不在 22 人主力、也不在替补表的人，按年龄降序取前 11。

    弱项标红与最佳 22 人同逻辑：EA < reference * ratio 的整行标红。
    """
    if not players:
        return "<div class='remaining'>无可卖球员</div>"
    rows = []
    for rank, p in enumerate(players, 1):
        weak = " weak-name" if is_weak(p["ea"], reference, ratio) else ""
        rows.append(
            f"<div class='s-row'>"
            f"<span class='s-rank'>{rank}</span>"
            f"<span class='s-name{weak}'>{p['name']}</span>"
            f"<span class='s-pos'>{p['position']}</span>"
            f"<span class='s-age'>{p['age']:.0f}岁</span>"
            f"<span class='s-stat'>CA{p['ca']}</span>"
            f"<span class='s-ea'>EA{p['ea']:.0f}</span>"
            f"</div>"
        )
    return "<div class='remaining'>" + "".join(rows) + "</div>"


def generate_full_html(
    ea_first,
    ea_second,
    depth_html=None,
    sell_html=None,
    ratio=0.9,
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
            "{{DEPTH}}",
            depth_html or render_depth_table({}),
        )
        .replace(
            "{{SELL}}",
            sell_html or render_sell_table([]),
        )
        .replace("{{AVG_EA_BEST}}", str(compute_average(ea_first, "ea")))
        .replace("{{AVG_EA_SECOND}}", str(compute_average(ea_second, "ea")))
    )


# ── Main flow ──────────────────────────────────────────────
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

    # 按年龄过滤（默认 >= 17 岁）
    candidates = [p for p in candidates if p["age"] >= min_age]
    if not candidates:
        print("没有符合年龄要求的球员")
        return None

    # 全部球员都算 EA
    calculate_ea(candidates, growth_until_age=growth_until_age, growth_per_year=growth_per_year)

    # 匈牙利算法从全部达龄球员里选 22 人（首发 + 替补）
    ea_first, ea_second = select_squad(candidates, "ea")
    chosen_ids = {id(p) for _s, p in ea_first} | {id(p) for _s, p in ea_second}
    # 弱项标红基准：首发 11 人平均 EA，替补表/可卖榜与最佳 22 人共用
    reference = compute_average(ea_first, "ea")

    # 所有达龄球员的英文名一起翻译（覆盖主力/替补表/可卖榜/未上榜全部），
    # 只发一次批量请求；未命中的保持原样。须在渲染前执行，渲染结果才用中文名。
    _translate_xi_names(candidates, translate=translate)

    # 替补表：位置池（每位置 EA 前 N 人）里的非主力（未入选 22 人）
    pool = compute_position_pool(candidates)
    depth_html = render_depth_table(pool, chosen_ids, reference=reference, ratio=ratio)

    # 可卖榜：既不在 22 人主力、也不在替补表（任何位置池）的人，按年龄降序前 11
    pool_ids = {id(p) for players in pool.values() for p in players}
    sell_candidates = [p for p in candidates if id(p) not in chosen_ids and id(p) not in pool_ids]
    sell_candidates.sort(key=lambda p: p["age"], reverse=True)
    sell_top = sell_candidates[:11]
    sell_html = render_sell_table(sell_top, reference=reference, ratio=ratio)

    html = generate_full_html(
        ea_first,
        ea_second,
        depth_html=depth_html,
        sell_html=sell_html,
        ratio=ratio,
    )
    out = output or OUTPUT
    out.write_text(html, encoding="utf-8")
    return html
