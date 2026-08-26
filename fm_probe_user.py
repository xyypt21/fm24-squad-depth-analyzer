"""用户俱乐部检测诊断脚本 —— 游戏运行并载入存档后使用。

用已知正确的俱乐部 uid（默认取 config.json 的 club_uid）做基准，逐环节验证：
  1. 人控经理链（mgr_hnp -> 向量）
  2. 基准俱乐部的 TEAM 记录与其 manager_ptr 实际指向
  3. 两者是否相交（检测算法的核心假设）
  4. 存档单例对象（savegame_id 链）附近是否有俱乐部 uid 字段

用法：
    python fm_probe_user.py                # 用 config.json 的 club_uid
    python fm_probe_user.py --club 920     # 显式指定
"""

import argparse
import contextlib
import sys

from fmlib.session import GameSession
from fmlib.user_club import (
    _manager_block_hit,
    _match_clubs,
    human_manager_ptrs,
    iter_teams,
    resolve_manager,
    walk_team_table,
)

for stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(Exception):
        stream.reconfigure(encoding="utf-8")

MIN_PTR = 0x10000


def load_config():
    import json
    from pathlib import Path

    try:
        return json.loads((Path(__file__).parent / "config.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def dump_words(mem, addr, length, marks=(), label=""):
    """按 u32 网格 dump，命中 marks 的值加注释；同时标出疑似指针。"""
    data = mem.read_bytes(addr, length)
    if not data:
        print(f"  <读取失败 @0x{addr:X}> {label}")
        return
    print(f"  -- {label} @0x{addr:X} ({length} 字节) --")
    for row in range(0, len(data) - 3, 32):
        cells = []
        notes = []
        for col in range(0, min(32, len(data) - row), 4):
            v = int.from_bytes(data[row + col : row + col + 4], "little")
            note = ""
            if v in marks:
                note = f"<=={v}"
            elif v >= MIN_PTR:
                note = "ptr?"
            cells.append(f"{v:08X}")
            if note:
                notes.append(f"+0x{row + col:X}:{note}")
        line = f"  +0x{row:04X}: " + " ".join(cells)
        if notes:
            line += "  | " + " ".join(notes)
        print(line)


def probe_chain(mem, session, base, off):
    """环节 1：人控经理向量。"""
    print("\n[1] 人控经理链")
    try:
        return human_manager_ptrs(session, log=lambda s: print("   ", s))
    except Exception as exc:
        print(f"    !! {exc}")
        ctx = mem.read_ptr(base + off.mgr_hnp_rva)
        if ctx:
            dump_words(mem, ctx, 0x40, label="ctx 头部")
        return set()


def _hnp_tag_ok(session, addr):
    tag = session.offsets.hnp_type_id
    return bool(tag) and session.mem.read_u32(addr) == tag


def probe_truth(mem, session, off, club_uid, pset):
    """环节 2：基准俱乐部的 TEAM 与 manager_ptr 实际指向。"""
    import fm24_probe

    print(f"\n[2] 基准俱乐部 {club_uid} 的球队记录")
    name, players = fm24_probe.club_squad(mem, club_uid)
    print(f"    club_squad: name={name} players={len(players)}")
    team_entry = next((p["club_entry"] for p in players if p.get("club_entry")), None)
    if not team_entry:
        print("    !! 没拿到 club_entry（TEAM 地址），无法继续基准验证")
        return
    print(f"    TEAM 条目地址 = 0x{team_entry:X}")
    dump_words(mem, team_entry, 0x90, marks=(club_uid,), label="TEAM 头部")
    mp_raw = mem.read_ptr(team_entry + off.team_manager_ptr_off)
    mp_s = f"0x{mp_raw:X}" if mp_raw else "None"
    print(f"    manager_ptr(+0x80) = {mp_s}")
    if not mp_raw:
        return
    dump_words(mem, mp_raw, 0x60, label="manager 对象头部")
    inner = mem.read_ptr(mp_raw)
    ok_direct = _hnp_tag_ok(session, mp_raw)
    ok_inner = bool(inner and inner >= MIN_PTR and _hnp_tag_ok(session, inner))
    print(f"    类型标记: 直接命中={ok_direct} 解引用一层命中={ok_inner}")
    mgr = resolve_manager(mem, session, team_entry)
    print(f"    resolve_manager -> {hex(mgr) if mgr else None}")
    if mgr is None:
        return
    if pset:
        hit = _manager_block_hit(mem, mgr, pset)
        if hit:
            person, block_off = hit
            print(
                f"    教练对象块内命中人控指针: 0x{person:X} @块+0x{block_off:X}"
                " —— 该队应被判为用户球队"
            )
        else:
            print(f"    教练对象头部 {0x400:#x} 字节内未发现人控向量指针")
        _refs_of_vector_ptr(mem, session, off, pset, mgr)
        if not hit:
            refs = mem.find_qword(mgr, max_hits=64)
            print(f"    教练对象地址的全内存引用 {len(refs)} 处:")
            for h in refs[:16]:
                print(f"      0x{h:X}")


def _refs_of_vector_ptr(mem, session, off, pset, mgr=None):
    """反查：人控指针本身被哪些地址引用（找 person<->entity 关联层）。"""
    ctx2 = mem.read_ptr(session.base + off.mgr_hnp_rva)
    if not ctx2:
        return
    b = mem.read_ptr(ctx2 + off.hmgr_start_off)
    e = mem.read_ptr(ctx2 + off.hmgr_end_off)
    if not (b and e):
        return
    for p in sorted(pset)[:4]:
        refs = mem.find_qword(p, max_hits=64)
        inside = [h for h in refs if (b <= h < e) or (mgr and mgr <= h < mgr + 0x400)]
        others = [h for h in refs if h not in inside]
        where = f"向量/教练块内 {len(inside)} 处" + (
            f"（教练块偏移: {[hex(h - mgr) for h in refs if mgr and mgr <= h < mgr + 0x400][:3]}）"
            if any(mgr and mgr <= h < mgr + 0x400 for h in refs)
            else ""
        )
        print(f"    人控指针 0x{p:X}: {where}，其他引用 {len(others)} 处")
        for h in others[:8]:
            print(f"      引用@ 0x{h:X}")


def probe_intersection(mem, session, off, pset):
    """环节 3：检测算法本体试算（球员记录来源）。"""
    from fmlib.clubs import read_club_name, read_club_uid

    print("\n[3] 全量交集试算（球员记录扫描来源）")
    teams = iter_teams(session, log=lambda s: print("   ", s))
    hits = []
    for t in teams:
        m = resolve_manager(mem, session, t)
        if m and m in pset:
            hits.append((t, m))
    print(f"    教练在人控集合中的球队: {len(hits)} 支")
    for t, m in hits[:20]:
        club = mem.read_ptr(t + off.team_club_ptr_off)
        uid = read_club_uid(mem, off, club) if club else None
        nm = read_club_name(mem, off, club) if club else None
        print(f"      TEAM=0x{t:X} coach=0x{m:X} club={uid} {nm or '?'}")
    return set(teams)


def probe_team_table(mem, session, off, pset, scanned_teams):
    """环节 5：全局球队表遍历 + 与球员扫描交叉验证 + 仅用表的检测试算。"""
    from fmlib.clubs import read_club_name, read_club_uid

    print("\n[5] 全局球队表 [exe+team_root] -> 容器 -> 表")
    try:
        records, info = walk_team_table(session, log=lambda s: print("   ", s))
    except Exception as exc:
        print(f"    !! 全局球队表失败: {exc}")
        return
    if info.notes:
        for n in info.notes:
            print(f"    note: {n}")

    # 样例行：uid / 类型 / 父俱乐部 uid / 名字
    print("    前 8 行样例:")
    for t in records[:8]:
        uid_t = mem.read_u32(t + off.team_uid_off)
        ttype = mem.read_u8(t + off.team_type_off)
        club = mem.read_ptr(t + off.team_club_ptr_off)
        cuid = read_club_uid(mem, off, club) if club else None
        nm = read_club_name(mem, off, club) if club else None
        print(f"      0x{t:X} uid={uid_t} type={ttype} club={cuid} {nm or '?'}")

    # 与球员记录扫描的球队集合求交（同一对象则地址应重合）
    if scanned_teams:
        overlap = len(set(records) & set(scanned_teams))
        only_scan = len(set(scanned_teams) - set(records))
        print(
            f"    与球员记录扫描球队地址交集: {overlap}"
            f"（仅扫描有: {only_scan}，表: {len(records)}，扫描: {len(scanned_teams)}）"
        )

    matches = _match_clubs(session, pset, records)
    print(f"    仅用球队表的检测结果: {[m.uid for m in matches]}")


def probe_savegame_singleton(mem, base, off, club_uid):
    """环节 4：存档单例对象附近找俱乐部 uid 字段。"""
    from fmlib.offsets import _int

    print("\n[4] savegame 单例对象找俱乐部 uid 字段")
    sg_rva = _int(off.raw["savegame_id_chain"]["savegame_id_rva"])
    sg = mem.read_ptr(base + sg_rva)
    if not sg:
        print("    q1 读取失败")
        return
    print(f"    q1 = 0x{sg:X}")
    dump_words(mem, sg, 0x140, marks=(club_uid,), label="q1 头部")


def main():
    ap = argparse.ArgumentParser(description="用户俱乐部检测诊断")
    ap.add_argument("--club", type=int, default=None, help="基准俱乐部 uid（默认 config.json）")
    args = ap.parse_args()
    club_uid = args.club or int(load_config().get("club_uid", 920))

    session = GameSession.attach()
    with session:
        off = session.offsets
        mem = session.mem
        base = session.base
        hnp_tag = hex(off.hnp_type_id) if off.hnp_type_id else "无"
        print(
            f"[game] PID={mem.pid} 文件版本={session.file_version} "
            f"产品版本={session.product_version}"
        )
        print(f"[game] 偏移表={off.fm_version} hnp_tag={hnp_tag}")
        gd = session.game_date()
        print(f"[game] 游戏日期={gd}")

        pset = probe_chain(mem, session, base, off)
        probe_truth(mem, session, off, club_uid, pset)
        scanned_teams = probe_intersection(mem, session, off, pset)
        probe_team_table(mem, session, off, pset, scanned_teams)
        probe_savegame_singleton(mem, base, off, club_uid)


if __name__ == "__main__":
    main()
