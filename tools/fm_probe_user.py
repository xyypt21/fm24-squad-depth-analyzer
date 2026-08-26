"""用户俱乐部检测诊断脚本 —— 游戏运行并载入存档后使用。

用已知正确的俱乐部 uid（默认取 config.json 的 club_uid）做基准，逐环节验证：
  1. 人控经理链（mgr_hnp -> 向量）
  2. 基准俱乐部的 TEAM 记录与其 manager_ptr 实际指向
  3. 两者交集（检测算法的核心假设）

用法：
    python fm_probe_user.py                # 用 config.json 的 club_uid
    python fm_probe_user.py --club 920     # 显式指定
"""

import argparse
import contextlib
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.pycache_prefix = str(Path(tempfile.gettempdir()) / "fm24-club-detector")

from clubs import read_club_name, read_club_uid, type_id_ok  # noqa: E402
from offsets import _int  # noqa: E402
from session import GameSession  # noqa: E402
from user_club import (  # noqa: E402
    MANAGER_BLOCK,
    _manager_block_hit,
    _match_clubs,
    human_manager_ptrs,
    iter_teams,
    resolve_manager,
)

for stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(Exception):
        stream.reconfigure(encoding="utf-8")

MIN_PTR = 0x10000


def load_config():
    import json

    try:
        return json.loads(
            (Path(__file__).resolve().parent.parent / "config.json").read_text(encoding="utf-8")
        )
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


def team_of_club(mem, session, off, club_uid):
    """在球员记录扫描出的球队里找父俱乐部 uid 匹配的第一支。"""
    for t in iter_teams(session):
        club = mem.read_ptr(t + off.team_club_ptr_off)
        if not club or not type_id_ok(mem, session.base, club, off.club_type_id):
            continue
        if read_club_uid(mem, off, club) == club_uid:
            return t
    return None


def probe_truth(mem, session, off, club_uid, pset):
    """环节 2：基准俱乐部的 TEAM 与 manager_ptr 实际指向。"""
    print(f"\n[2] 基准俱乐部 {club_uid} 的球队记录")
    team_entry = team_of_club(mem, session, off, club_uid)
    if not team_entry:
        print("    !! 没找到该俱乐部对应的球队（uid 是否正确/存档是否已载入？）")
        return
    name = None
    club_addr = mem.read_ptr(team_entry + off.team_club_ptr_off)
    if club_addr:
        name = read_club_name(mem, off, club_addr)
    print(f"    TEAM 条目地址 = 0x{team_entry:X}  俱乐部名: {name or '?'}")
    dump_words(mem, team_entry, 0x90, marks=(club_uid,), label="TEAM 头部")
    mp_raw = mem.read_ptr(team_entry + off.team_manager_ptr_off)
    mp_s = f"0x{mp_raw:X}" if mp_raw else "None"
    print(f"    manager_ptr(+0x80) = {mp_s}")
    if not mp_raw:
        return
    dump_words(mem, mp_raw, 0x60, label="manager 对象头部")
    head_u32 = mem.read_u32(mp_raw)
    entity_tag = _int(off.raw["vtable_rvas"]["entity_hnp"]) | 0x40000000
    print(f"    对象头 u32={head_u32:#010x}（entity_hnp tag 应为 {entity_tag:#010x}）")
    mgr = resolve_manager(mem, session, team_entry)
    print(f"    resolve_manager -> {hex(mgr) if mgr else None}")
    if mgr is None:
        return

    hit = _manager_block_hit(mem, mgr, pset)
    if hit:
        person, block_off = hit
        print(
            f"    教练对象块内命中人控指针: 0x{person:X} @块+0x{block_off:X}"
            " —— 该队应被判为用户球队"
        )
    elif mgr in pset:
        print("    教练对象即人控向量元素（直接相等）—— 该队应被判为用户球队")
    else:
        print(f"    教练对象头部 {MANAGER_BLOCK:#x} 字节内未发现人控向量指针")
        refs = mem.find_qword(mgr, max_hits=64)
        print(f"    教练对象地址的全内存引用 {len(refs)} 处:")
        for h in refs[:16]:
            print(f"      0x{h:X}")

    _refs_of_vector_ptr(mem, session, off, pset, mgr)


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
        inside = [h for h in refs if (b <= h < e) or (mgr and mgr <= h < mgr + MANAGER_BLOCK)]
        others = [h for h in refs if h not in inside]
        in_mgr = [hex(h - mgr) for h in refs if mgr and mgr <= h < mgr + MANAGER_BLOCK]
        where = f"向量/教练块内 {len(inside)} 处"
        if in_mgr:
            where += f"（教练块偏移: {in_mgr[:3]}）"
        print(f"    人控指针 0x{p:X}: {where}，其他引用 {len(others)} 处")
        for h in others[:8]:
            print(f"      引用@ 0x{h:X}")


def probe_intersection(mem, session, off, pset):
    """环节 3：检测算法本体试算。"""
    print("\n[3] 全量交集试算（检测算法本体）")
    matches = _match_clubs(session, pset, iter_teams(session), log=print)
    print(f"    结果: {[(m.uid, m.name) for m in matches]}")


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
        probe_intersection(mem, session, off, pset)


if __name__ == "__main__":
    main()
