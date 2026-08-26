"""用户俱乐部自动检测（人控经理向量 ∩ 球队经理指针）。

原理：
1. [exe+mgr_hnp_rva] -> 管理器上下文；上下文 +hmgr_start_off / +hmgr_end_off
   是一个指针向量的 begin/end。整块读出，解析成"疑似人控经理对象地址"集合。
   （不逐个校验类型——向量可能很大，逐项 syscall 太慢；只做指针合法性过滤。）
2. 枚举全部球队及其主教练：
   a. 首选全局球队表 [exe+team_root_rva] -> 容器(+0x80) -> 表 -> 记录；
   b. 表不可用时回退球员记录签名扫描，从每条 +0x130 取"当前球队条目"(TEAM)。
3. 交集：教练对象落在第 1 步集合里的球队，即人控经理执教的球队 ->
   归一化到父俱乐部 uid + 名字。单人游戏通常恰命中一家。

注意：偏移锁定当前 FM24 构建；游戏更新后需更新 fm_offsets_info.json。
"""

from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Set

from fmlib.clubs import (
    read_club_name,
    read_club_uid,
    type_id_ok,
    uid_sane,
)
from fmlib.memory import FmMemory
from fmlib.offsets import Offsets
from fmlib.session import GameSession
from fmlib.tables import TableError, walk_table

# 球员记录（entity_ap）特征签名与布局，与 fm24_probe.py 同源（实测验证）
PLAYER_SIGNATURE = b"\x58\xe9\xa4\x45"  # 记录头 u32 = 0x45A4E958
PLAYER_STRIDE = 0x3E8  # 记录间距 1000 字节
P_CUR_TEAM_ENTRY = 0x130  # +304 当前球队条目(TEAM)指针
PLAYER_MAX_HITS = 400000
RECORD_HEAD_NEED = P_CUR_TEAM_ENTRY + 8  # 只需读到 +0x138

MAX_VECTOR_BYTES = 32 * 1024 * 1024  # 向量整读上限（异常大时截断并告警）
MIN_PTR = 0x10000
MAX_PTR = 0x7FF000000000
MANAGER_BLOCK = 0x400  # 教练对象头部扫描范围：在其中找指向人控记录的指针

LogFn = Optional[Callable[[str], None]]


def _log(log: LogFn, text: str):
    if log:
        log(text)


class DetectionError(RuntimeError):
    """检测失败（附诊断信息）。"""


@dataclass
class ClubInfo:
    """检测到的"人控经理执教球队"所属俱乐部。"""

    uid: int
    name: Optional[str]
    team_addr: int  # 执教球队记录地址（TEAM）
    club_addr: int  # 俱乐部记录地址（CLUB）
    person_addr: int  # 命中的教练/经理对象地址


# ── 第 1 步：人控经理向量 ─────────────────────────────────
def human_manager_ptrs(session: GameSession, log: LogFn = None) -> Set[int]:
    """读人控经理向量，返回其中疑似合法指针的集合。"""
    off: Offsets = session.offsets
    mem = session.mem
    base = session.base

    ctx = mem.read_ptr(base + off.mgr_hnp_rva)
    if not ctx:
        raise DetectionError(
            f"[exe+0x{off.mgr_hnp_rva:X}] 读取失败（0），游戏可能未载入存档，"
            f"或偏移与当前版本不匹配"
            f"（游戏 {session.product_version or '?'} vs 偏移表 {off.fm_version}）。"
        )
    begin = mem.read_ptr(ctx + off.hmgr_start_off)
    end = mem.read_ptr(ctx + off.hmgr_end_off)
    begin_s = f"0x{begin:X}" if begin else "None"
    end_s = f"0x{end:X}" if end else "None"
    _log(log, f"[chain] ctx=0x{ctx:X} begin={begin_s} end={end_s}")
    if not begin or not end or end <= begin:
        raise DetectionError(
            f"人控经理向量无效：ctx=0x{ctx:X} begin={begin_s} end={end_s}"
            "（偏移可能与当前游戏版本不匹配，可用 fm_probe_user.py 进一步诊断）"
        )
    span = end - begin
    truncated = ""
    if span % 8 != 0:
        raise DetectionError(f"人控经理向量尺寸非 8 对齐：{span} 字节 @0x{begin:X}")
    if span > MAX_VECTOR_BYTES:
        span = MAX_VECTOR_BYTES
        truncated = f"（超过 {MAX_VECTOR_BYTES // (1024 * 1024)}MB 上限，已截断）"
    buf = mem.read_bytes(begin, span)
    if not buf:
        raise DetectionError(f"人控经理向量读取失败 @0x{begin:X} span={span}")
    ptrs = {
        int.from_bytes(buf[i : i + 8], "little")
        for i in range(0, len(buf) - 7, 8)
        if MIN_PTR <= int.from_bytes(buf[i : i + 8], "little") < MAX_PTR
    }
    sample = " ".join(f"0x{p:X}" for p in sorted(ptrs)[:5])
    _log(
        log,
        f"[chain] 人控经理向量: {len(buf) // 8} 槽 / 合法指针 {len(ptrs)} 个{truncated}"
        + (f" 样例: {sample}" if sample else ""),
    )
    if not ptrs:
        raise DetectionError("人控经理向量里没有合法指针（内容可能不是对象数组）。")
    return ptrs


# ── 第 2 步：从球员记录枚举球队与其教练 ────────────────────
def _segmentize(addrs: Iterable[int]) -> List[List[int]]:
    """按 stride==PLAYER_STRIDE 把命中地址分成连续段 [[start,count],...]。"""
    segs: List[List[int]] = []
    for a in sorted(addrs):
        if segs and a - (segs[-1][0] + (segs[-1][1] - 1) * PLAYER_STRIDE) == PLAYER_STRIDE:
            segs[-1][1] += 1
        else:
            segs.append([a, 1])
    return segs


def iter_teams(session: GameSession, log: LogFn = None) -> Dict[int, int]:
    """扫描球员记录，返回 {TEAM 条目地址: 出现次数}（即现存全部球队）。"""
    mem = session.mem
    hits = mem.scan_pattern(PLAYER_SIGNATURE, max_hits=PLAYER_MAX_HITS, writable_only=True)
    _log(log, f"[teams] 球员记录签名命中 {len(hits)} 处")
    teams: Dict[int, int] = {}
    for start, count in _segmentize(hits):
        buf = mem.read_bytes(start, count * PLAYER_STRIDE)
        if not buf:
            continue
        for i in range(count):
            base_i = i * PLAYER_STRIDE
            entry = int.from_bytes(
                buf[base_i + P_CUR_TEAM_ENTRY : base_i + RECORD_HEAD_NEED], "little"
            )
            if entry >= MIN_PTR:
                teams[entry] = teams.get(entry, 0) + 1
    _log(log, f"[teams] 去重后 {len(teams)} 支球队")
    return teams


def resolve_manager(mem: FmMemory, session: GameSession, team_addr: int) -> Optional[int]:
    """TEAM -> 教练对象地址（entity_hnp）。

    实测 manager_ptr 指向的是教练的 ENTITY 记录（头 tag 0x45A6A318），
    不是人控向量里的 PERSON 对象，因此这里不做类型强校验。
    """
    off = session.offsets
    mp = mem.read_ptr(team_addr + off.team_manager_ptr_off)
    if mp and MIN_PTR <= mp < MAX_PTR:
        return mp
    return None


def _manager_block_hit(mem: FmMemory, block_addr: int, pset: Set[int], size: int = MANAGER_BLOCK):
    """在 [block_addr, +size) 里找第一个属于 pset 的 8 字节值。

    返回 (命中值, 块内偏移) 或 None。用于把"教练 ENTITY 对象"与
    "人控经理向量里的 PERSON 指针"关联起来——实体块内某处存有
    指向其 person 的指针，无需知道具体偏移。
    """
    buf = mem.read_bytes(block_addr, size)
    if not buf:
        return None
    for i in range(0, len(buf) - 7, 8):
        v = int.from_bytes(buf[i : i + 8], "little")
        if v in pset:
            return v, i
    return None


def walk_team_table(session: GameSession, log: LogFn = None):
    """走全局球队表，返回 (record_addr_list, TableInfo)。失败抛 TableError。"""
    off = session.offsets
    if off.team_table_root_rva is None:
        raise TableError("偏移表没有 team_table_chain 节")
    records, info = walk_table(
        session.mem,
        session.base,
        off.team_table_root_rva,
        off.table_container_off,
        off.team_type_id,
    )
    _log(
        log,
        f"[table] 全局球队表: mode={info.mode} stride=0x{info.stride:X} "
        f"count={info.count} start=0x{info.start:X}"
        + ("(截断)" if info.truncated else "")
        + ("; ".join(info.notes) if info.notes else ""),
    )
    return records, info


# ── 主入口 ────────────────────────────────────────────
def _match_clubs(
    session: GameSession,
    pset: Set[int],
    team_addrs: Iterable[int],
    log: LogFn = None,
) -> List[ClubInfo]:
    """在人控经理集合与球队教练之间取交集，归出俱乐部列表。

    匹配规则：教练对象（TEAM+0x80）内存块头部 MANAGER_BLOCK 字节里
    出现人控向量中的指针，即视为同一人（实体块内存有指向其 person
    的引用，具体偏移无关紧要）。
    """
    off = session.offsets
    mem = session.mem
    matches: List[ClubInfo] = []
    seen_clubs: Set[int] = set()
    seen_managers: Set[int] = set()
    for team_addr in team_addrs:
        manager = resolve_manager(mem, session, team_addr)
        if not manager or manager in seen_managers:
            continue
        seen_managers.add(manager)
        # 实测：人控向量里的指针就是教练 ENTITY 本身，直接相等即命中；
        # 块内引用扫描作为兜底（兼容向量存 person 等关联对象的版本）。
        if manager in pset:
            hit = (manager, 0)
        else:
            found = _manager_block_hit(mem, manager, pset)
            hit = found if found else None
        if not hit:
            continue
        person, block_off = hit
        club = mem.read_ptr(team_addr + off.team_club_ptr_off)
        if not club or not type_id_ok(mem, session.base, club, off.club_type_id):
            _log(
                log,
                f"[hit] 球队 0x{team_addr:X} 教练块+0x{block_off:X} 命中人控指针，"
                "但没有有效俱乐部链接（可能是国家队）",
            )
            continue
        uid = read_club_uid(mem, off, club)
        if not uid_sane(uid) or uid in seen_clubs:
            continue
        seen_clubs.add(uid)
        matches.append(
            ClubInfo(
                uid=uid,
                name=read_club_name(mem, off, club),
                team_addr=team_addr,
                club_addr=club,
                person_addr=person,
            )
        )
    return matches


def detect_user_clubs(session: GameSession, log: LogFn = None) -> List[ClubInfo]:
    """检测用户（人控经理）执教的俱乐部，返回去重列表（单人游戏通常 1 项）。

    球队来源依次尝试：全局球队表 -> 球员记录签名扫描。
    """
    pset = human_manager_ptrs(session, log=log)

    sources: List[tuple] = []
    try:
        records, _ = walk_team_table(session, log=log)
        sources.append(("全局球队表", records))
    except TableError as exc:
        _log(log, f"[table] 全局球队表不可用，回退球员记录扫描: {exc}")
    sources.append(("球员记录扫描", None))

    last_teams: Dict[int, int] = {}
    for label, records in sources:
        teams = records if records is not None else iter_teams(session, log=log)
        matches = _match_clubs(session, pset, teams, log=log)
        if isinstance(teams, dict):
            last_teams = teams
        if matches:
            _log(log, f"[done] 经 {label} 命中 {len(matches)} 家俱乐部")
            return matches
        _log(log, f"[done] {label} 未命中")

    raise DetectionError(
        f"未找到交集：人控经理集合 {len(pset)} 个指针，"
        f"{len(last_teams)} 支球队的教练对象内存块里都没有这些指针。"
        "可能：① 你当前无执教俱乐部（失业）；② 教练实体与 person 的关联"
        "不在对象头部（请运行 fm_probe_user.py 输出诊断信息）；"
        "③ 偏移与游戏版本不匹配。"
    )
