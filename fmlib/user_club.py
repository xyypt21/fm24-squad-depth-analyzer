"""用户俱乐部自动检测（人控经理链 + 指针反查）。

原理（偏移来自 fm_offsets_info.json）：
1. [exe+mgr_hnp_rva] -> 管理器上下文；上下文 +hmgr_start_off / +hmgr_end_off
   是 std::vector<HUMAN_NON_PLAYER*> 的 begin/end。
2. 向量元素是人控角色（经理）的 PERSON 记录，头部 tagged id 可用
   hnp_vtable_rva 校验。单人游戏里通常只有一人，即玩家本人。
3. 在可写堆中反查该 person 地址的 8 字节引用：命中地址 - team.manager_ptr(0x80)
   即候选 TEAM 记录（他执教的球队）；校验 TEAM 头部类型后取
   TEAM+club_ptr -> CLUB，读出俱乐部 uid 与名字。

注意：偏移锁定当前 FM24 构建；游戏更新后需更新 fm_offsets_info.json。
"""

from dataclasses import dataclass
from typing import Callable, List, Optional

from fmlib.clubs import (
    read_club_name,
    read_club_uid,
    type_id_ok,
    uid_sane,
)
from fmlib.offsets import Offsets
from fmlib.session import GameSession

MAX_MANAGER_SLOTS = 64  # 人控经理向量合理上限（网络游戏也就几个）
POINTER_SCAN_MAX_HITS = 4096
MAX_TEAM_TYPE = 50  # team_type_enum 最大值 44，留余量

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
    person_addr: int  # 人控经理 person 记录地址


def _person_tag_ok(session: GameSession, addr: int) -> bool:
    """校验 HNP person 记录头；偏移表没有该类型标记时放行（宽松模式）。"""
    tag = session.offsets.hnp_type_id
    if tag is None:
        return True
    return type_id_ok(session.mem, session.base, addr, tag)


def human_persons(session: GameSession, log: LogFn = None) -> List[int]:
    """从人控经理链取出全部 HUMAN_NON_PLAYER person 记录地址（去重保序）。"""
    off: Offsets = session.offsets
    mem = session.mem
    base = session.base

    ctx = mem.read_ptr(base + off.mgr_hnp_rva)
    if not ctx:
        raise DetectionError(
            f"[exe+0x{off.mgr_hnp_rva:X}] 读取失败（0），"
            f"游戏可能未载入存档或偏移与当前版本不匹配"
            f"（游戏 {session.product_version or '?'} vs 偏移表 {off.fm_version}）。"
        )
    begin = mem.read_ptr(ctx + off.hmgr_start_off)
    end = mem.read_ptr(ctx + off.hmgr_end_off)
    begin_s = f"0x{begin:X}" if begin else "None"
    end_s = f"0x{end:X}" if end else "None"
    _log(log, f"[chain] ctx=0x{ctx:X} begin={begin_s} end={end_s}")
    if not begin or not end or end < begin:
        raise DetectionError(f"人控经理向量无效（begin/end 读取失败）：ctx=0x{ctx:X}")
    span = end - begin
    if span % 8 != 0 or span > MAX_MANAGER_SLOTS * 8:
        raise DetectionError(f"人控经理向量尺寸异常：{span} 字节 @0x{begin:X}")

    persons = []
    for i in range(span // 8):
        entry = mem.read_u64(begin + i * 8)
        if not entry:
            continue
        if _person_tag_ok(session, entry):
            persons.append(entry)
            continue
        # 兼容包一层的情况：条目 -> 对象 -> person
        inner = mem.read_ptr(entry) if entry > 0x10000 else None
        if inner and _person_tag_ok(session, inner):
            persons.append(inner)
    unique = list(dict.fromkeys(persons))
    _log(log, f"[chain] 人控经理 {len(unique)} 人: " + " ".join(f"0x{p:X}" for p in unique))
    if not unique:
        raise DetectionError(
            "人控经理向量存在但没有有效的 HUMAN_NON_PLAYER 记录"
            "（hnp 类型标记不匹配，偏移表版本可能不对）。"
        )
    return unique


def _clubs_for_person(session: GameSession, person: int, log: LogFn = None) -> List[ClubInfo]:
    """指针反查：找引用 person 的 TEAM 记录，归出俱乐部列表。"""
    off = session.offsets
    mem = session.mem
    base = session.base

    hits = mem.find_qword(person, max_hits=POINTER_SCAN_MAX_HITS)
    _log(log, f"[scan] 0x{person:X} 引用命中 {len(hits)} 处")
    result = []
    for h in hits:
        team = h - off.team_manager_ptr_off
        if not type_id_ok(mem, base, team, off.team_type_id):
            continue
        ttype = mem.read_u8(team + off.team_type_off)
        if ttype is None or ttype > MAX_TEAM_TYPE:
            continue
        team_uid = mem.read_u32(team + off.team_uid_off)
        if not uid_sane(team_uid):
            continue
        club = mem.read_ptr(team + off.team_club_ptr_off)
        if not club or not type_id_ok(mem, base, club, off.club_type_id):
            _log(
                log,
                f"[scan] 候选 TEAM 0x{team:X}（uid={team_uid}, type={ttype}）"
                "无有效俱乐部链接（可能是国家队）",
            )
            continue
        uid = read_club_uid(mem, off, club)
        if not uid_sane(uid):
            continue
        result.append(
            ClubInfo(
                uid=uid,
                name=read_club_name(mem, off, club),
                team_addr=team,
                club_addr=club,
                person_addr=person,
            )
        )
    return result


def detect_user_clubs(session: GameSession, log: LogFn = None) -> List[ClubInfo]:
    """检测用户（人控经理）执教的俱乐部，返回去重列表（通常 1 项）。"""
    persons = human_persons(session, log=log)
    found = []
    for person in persons:
        for info in _clubs_for_person(session, person, log=log):
            if all(info.uid != x.uid for x in found):
                found.append(info)
    if not found:
        raise DetectionError(
            "找到了人控经理，但没有找到他执教的俱乐部（可能正处于失业状态，或指针反查未命中）。"
        )
    return found
