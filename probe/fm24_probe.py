"""
FM24 内存探测工具 —— 在游戏运行时使用。

用途：
  1. 附加到 Football Manager 2024 进程
  2. 列出模块，定位主程序 / game_plugin.dll
  3. 读取主模块文件版本（确认是哪一版游戏，决定偏移表）
  4. 在内存里找"锚点字符串"（存档名 / 球员名 / 俱乐部名）
     —— 找到后就能反向定位它所属的对象结构，这是逆向偏移的第一步

用法：
    python fm24_probe.py                  # 自动用最新存档名当锚点
    python fm24_probe.py --name "哈兰德"   # 指定一个球员/俱乐部名当锚点
    python fm24_probe.py --module          # 只列模块，不扫内存

前置条件：FM24 已启动且已载入存档。
"""

import argparse
import ctypes
import glob
import os
import re
import sys
from ctypes import wintypes as wt
from pathlib import Path

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

from fm_memory import FmMemory

FM_DIR = Path(r"C:\Users\xyy\Documents\Sports Interactive\Football Manager 2024")


# ── 已验证入口链（FM24 Epic 版, 相对 fm.exe 模块基址）────
# 来源：对 FM Scouting Tool 26 做运行时钩子观察（frida 钩 ReadProcessMemory）
#       + 用 ctypes 对运行中的游戏复现验证。
# 注意：这些偏移锁定当前 FM24 构建（Epic 版）。游戏更新后可能变化，
#       若偏移失效，用同样手段重新观察即可。
FM24_ENTRY_A = 0x642ED38   # [base+A] -> p2 ；[p2+0x80] -> p3
FM24_ENTRY_A_NEXT = 0x80   # 从 p2 到 p3 的偏移
FM24_ENTRY_B = 0x63659C0   # [base+B] -> q1 ；[q1+0x90] -> 版本探测值
FM24_TABLE_STRIDE = 0x268  # 大表条目间距（读迹中 8 字节指针的间隔）

# ── --roster 用：球员记录（1000 字节等距数组，实测验证）────────
# 球员记录以特征签名 0x45A4E958 开头，间距固定 0x3E8（1000 字节）。
# 记录内相对偏移（相对记录起始地址）：
PLAYER_SIGNATURE = b"\x58\xE9\xA4\x45"      # 记录头 u32 = 0x45A4E958
PLAYER_STRIDE = 0x3E8
P_ENTITY_ID = 0x284          # +644  person_uid / entity_id
P_CA = 0x200                 # +512  u16 当前能力
P_PA = 0x202                 # +514  u16 潜在能力
P_BIRTH_DOY = 0x2BC          # +700  u16 出生年内的第几天（1 起）
P_BIRTH_YEAR = 0x2BE         # +702  u16 出生年
P_NAME_FULL = 0x2C0          # +704  完整名串指针（可空）
P_NAME_FIRST = 0x2D0         # +720  名持有者指针（->[0]->名串）
P_NAME_LAST = 0x2D8          # +728  姓持有者指针（->[0]->姓串）
P_CLUB_CUR = 0x130           # +304  当前俱乐部条目指针（56 字节条目, [+0xC]=uid）
# 合同俱乐部：+832  -> S(180B) -> [S+0x10] -> 俱乐部条目(56B) -> [+0xC]=uid
P_CONTRACT_S = 0x340         # +832  合同结构指针
# 俱乐部条目（56 字节）：
CLUB_ENTRY_TAG = 0x45A78848  # 条目头
CLUB_ENTRY_UID = 0xC         # 条目内 club_uid
CLUB_ENTRY_STRUCT = 0x30     # 条目内 -> 俱乐部结构
# 俱乐部结构（stride 0x100）：
CLUB_STRUCT_UID = 0xC        # 结构内 club_uid
CLUB_STRUCT_NAME = 0xC0      # 结构内 -> 名称缓冲([0]=len,[4]=串)


# ── 中文名（音译）逆向结论（2026-08-03 整理）──────────────────
# 目标：让 --roster 输出球员中文名（如 Adrián Bernabé García → 阿德里安·贝尔纳贝·加西亚）。
# 已确认事实：
#   1) 球员记录(P_NAME_FULL / P_NAME_FIRST / P_NAME_LAST)只存拉丁/西文变体，无中文槽。
#   2) 中文名来自独立音译字典，在内存中完整存在（姆巴佩/福登/内马尔/德布劳内等都在）。
#   3) 音译表 = 无 id 的纯中文字符串池，条目格式：
#         [u32 len+9][u32 0][u32 type][u32 len][utf8 len 字节]   （16+len 字节，紧凑连续）
#      type 含义（疑似语言/分量类别）：1=中文全名/名，2=名变体，6=姓，3/4=其他。
#      例：'杰内·姆巴佩'@0xF050F470、'德赫·库瓦希'@0xF050F440、'贝尔纳贝'@0xEFEBA960。
#      按汉字拼音序分块连续存储（…迪/德…，…贝/伯… 各一块）。
#   4) 球员名字对象（如 Bernabé 姓对象 0xE30BB800）结构 = 变体槽数组，每槽 0x20：
#         [+0x0]=串ptr(->[len][utf8]) [+0x8]=name_id [+0xC]=0x000600AA(类别) [+0x10]=flags
#      同一球员的姓有多个变体（Bernabé→…→Sánchez Rodríguez），name_id 连续递增(步进4)。
#   5) 阻塞点：球员名字对象的 name_id（如 0x071534=Bernabé）【不落在音译表里】——
#      全内存搜该 id 只在名字对象内命中；音译表条目本身不携带任何 id/指针/拉丁原文。
#      因此无法用指针或 id 把球员记录精确关联到音译条目。
# 结论/后续候选路径：
#   A. 解析音译表前缀树/哈希桶（表按拼音序，可能只是线性池，需找表头 count/bucket）。
#   B. 用"拉丁姓氏→中文"近似匹配（发音/拼音），可能误配，实现成本低。
#   C. 外部词典方案：内置常见球员中文名 + 规则音译，稳定但覆盖有限。
# 注意：存档重载后堆地址会变（旧地址 0xF050E000/0xE30BB800 等仅当次有效），
#       偏移/结构不变，但每次运行需重新扫描定位。


def run_chain(mem, exe_mod):
    """复现已验证的入口指针链：游戏基址 -> 数据大表。"""
    base = exe_mod[0]
    print(f"[chain] fm.exe base = 0x{base:012X}")

    p2 = mem.read_ptr(base + FM24_ENTRY_A)
    print(f"  [base+0x{FM24_ENTRY_A:X}] -> p2 = 0x{p2:012X}" if p2 else f"  [base+0x{FM24_ENTRY_A:X}] 读取失败")
    if not p2:
        print("  偏移失效（游戏版本不匹配？）")
        return
    p3 = mem.read_ptr(p2 + FM24_ENTRY_A_NEXT)
    print(f"  [p2+0x{FM24_ENTRY_A_NEXT:X}] -> p3 = 0x{p3:012X}" if p3 else "  [p2+0x80] 读取失败")
    if not p3:
        return
    table = mem.read_ptr(p3)
    table2 = mem.read_ptr(p3 + 8)
    print(f"  [p3]      -> 大表 = 0x{table:012X}" if table else "  [p3] 读取失败")
    print(f"  [p3+8]    -> 第二表 = 0x{table2:012X}" if table2 else "  [p3+8] 读取失败")

    q1 = mem.read_ptr(base + FM24_ENTRY_B)
    print(f"  [base+0x{FM24_ENTRY_B:X}] -> q1 = 0x{q1:012X}" if q1 else f"  [base+0x{FM24_ENTRY_B:X}] 读取失败")
    if q1:
        q2 = mem.read_u32(q1 + 0x90)
        print(f"  [q1+0x90] -> 版本探测 = {q2} (0x{q2:X})" if q2 is not None else "  [q1+0x90] 读取失败")

    if table:
        print(f"  大表头 8 项（间距 0x{FM24_TABLE_STRIDE:X}）:")
        for i in range(8):
            v = mem.read_ptr(table + i * FM24_TABLE_STRIDE)
            if v:
                print(f"    +{i * FM24_TABLE_STRIDE:#06X}: 0x{v:012X}")
            else:
                print(f"    +{i * FM24_TABLE_STRIDE:#06X}: <读失败/0>")
    print("  [chain] 完成。这些偏移是 FM Scouting Tool 26 实际使用的入口。")


# ── --roster：球员/俱乐部枚举 ────────────────────────────
def _read_len_str(mem, ptr):
    """读取名字缓冲：[u32 len][utf-8 串]；ptr 也可能是持有者(->[0]->串)，最多追两层。"""
    if not ptr:
        return None
    for _ in range(3):
        b = mem.read_bytes(ptr, 132)
        if not b:
            return None
        ln = int.from_bytes(b[0:4], "little")
        if 0 < ln < 120:
            raw = b[4:4 + ln]
            if all(32 <= c < 127 or c > 0x7F for c in raw):
                return raw.decode("utf-8", "replace")
        v = int.from_bytes(b[0:8], "little")
        # 名字缓冲地址范围随游戏更新而变（见 scan_player_records 注释），
        # 放宽校验只要求落在已分配堆范围的上界内。
        if v and v < 0x7FF000000000 and v >= 0x66000000:
            ptr = v
            continue
        return None
    return None


def _club_entry_uid(mem, ptr):
    """56 字节俱乐部条目 -> [+0xC] 队 uid（注意：青年队是独立队 uid）；校验头类型。"""
    if not ptr:
        return None
    b = mem.read_bytes(ptr, 0x14)
    if not b or len(b) < 0x14:
        return None
    if int.from_bytes(b[0:4], "little") != CLUB_ENTRY_TAG:
        return None
    return int.from_bytes(b[CLUB_ENTRY_UID:CLUB_ENTRY_UID + 4], "little")


def _club_parent(mem, ptr):
    """俱乐部条目 -> 结构(+0x30) -> [+0xC] 父俱乐部 uid。

    条目代表"队"（一线/预备/青年队各有独立 uid），struct 才是"俱乐部"本体；
    用父 uid 才能区分真正的外租（struct 指向对方俱乐部）与在本队青年队（struct 仍为本俱乐部）。
    """
    if not ptr:
        return None
    b = mem.read_bytes(ptr, 0x40)
    if not b or len(b) < 0x40:
        return None
    if int.from_bytes(b[0:4], "little") != CLUB_ENTRY_TAG:
        return None
    # 条目内指针均为 64 位（游戏堆可能在 4GB 以上），不能按 u32 截断读
    cs = int.from_bytes(b[CLUB_ENTRY_STRUCT:CLUB_ENTRY_STRUCT + 8], "little")
    st = mem.read_bytes(cs, 0x14) if cs else None
    if not st or len(st) < 0x14:
        return None
    return int.from_bytes(st[CLUB_STRUCT_UID:CLUB_STRUCT_UID + 4], "little")


def _club_name(mem, club_entry):
    """由俱乐部条目取名字：条目->[+0x30]结构->[+0xC0]名缓冲。"""
    if not club_entry:
        return None
    b = mem.read_bytes(club_entry, 0x40)
    if not b or len(b) < 0x40:
        return None
    # 条目/结构内指针均为 64 位，不能按 u32 截断读
    cs = int.from_bytes(b[CLUB_ENTRY_STRUCT:CLUB_ENTRY_STRUCT + 8], "little")
    if not cs:
        return None
    st = mem.read_bytes(cs, CLUB_STRUCT_NAME + 8)
    if not st or len(st) < CLUB_STRUCT_NAME + 8:
        return None
    np = int.from_bytes(st[CLUB_STRUCT_NAME:CLUB_STRUCT_NAME + 8], "little")
    return _read_len_str(mem, np)


def scan_player_records(mem):
    """全量扫描球员记录，返回记录起始地址列表（已按地址排序）。

    球员记录堆的基址随游戏更新/存档重载而变化（历史上见过 0xD..-0x13..、
    0xE.. 等），因此扫全内存而非锁定固定区间。签名 0x45A4E958 足够特异，
    全内存命中即为球员记录（实测 0x45A4E958 命中即 0x3E8 间距的记录）。
    """
    return mem.scan_pattern(PLAYER_SIGNATURE, max_hits=400000)


def read_player(mem, rec):
    """读取一条球员记录 -> dict（entity_id/name/ca/pa/birth/contract/current）。"""
    # 记录内指针均为 64 位（游戏堆可能在 4GB 以上），用 read_u64 读指针
    full = _read_len_str(mem, mem.read_u64(rec + P_NAME_FULL))
    first = _read_len_str(mem, mem.read_u64(rec + P_NAME_FIRST))
    last = _read_len_str(mem, mem.read_u64(rec + P_NAME_LAST))
    name = full or ((first or "") + " " + (last or "")).strip() or None
    ca = mem.read_u16(rec + P_CA)
    pa = mem.read_u16(rec + P_PA)
    year = mem.read_u16(rec + P_BIRTH_YEAR)
    doy = mem.read_u16(rec + P_BIRTH_DOY)
    # 合同俱乐部：+832 -> S -> [S+0x10] -> 条目 -> struct [+0xC]
    contract = None
    club_entry = None
    s_ptr = mem.read_u64(rec + P_CONTRACT_S)
    if s_ptr:
        s = mem.read_bytes(s_ptr, 0x18)
        if s and len(s) >= 0x18:
            s10 = int.from_bytes(s[0x10:0x18], "little")
            club_entry = s10
            contract = _club_parent(mem, s10)
    # 当前俱乐部：+304 -> 条目 -> struct [+0xC]（父俱乐部，青年队自动归回本队）
    current = _club_parent(mem, mem.read_u64(rec + P_CLUB_CUR))
    return {
        "entity_id": mem.read_u32(rec + P_ENTITY_ID),
        "name": name,
        "ca": ca,
        "pa": pa,
        "year": year,
        "doy": doy,
        "contract": contract,
        "current": current,
        "club_entry": club_entry,
        "rec": rec,
    }


def doy_to_date(year, doy):
    try:
        import datetime
        return (datetime.date(year, 1, 1) + datetime.timedelta(days=doy - 1)).isoformat()
    except Exception:
        return f"{year}-{doy}"


def collect_roster(mem):
    """扫描并解析所有有俱乐部合同的球员，返回 player dict 列表。"""
    players = []
    for r in scan_player_records(mem):
        p = read_player(mem, r)
        if p["contract"] is None:
            continue
        players.append(p)
    return players


def read_position(mem, rec):
    """读取球员位置字段。

    位置偏移尚未定位（P_POSITION 未定义），返回 None。
    定位方法：运行 fm24_probe.py --posprobe，把偏移填进 P_POSITION。
    """
    return None


def _print_club_summary(mem, players):
    """输出所有俱乐部的球员数量汇总。"""
    import collections
    club_names = {}
    for p in players:
        if p["club_entry"]:
            club_names.setdefault(p["contract"], _club_name(mem, p["club_entry"]))
    counts = collections.Counter(p["contract"] for p in players)
    print(f"\n[roster] 全部俱乐部球员数（前 40，uid/名字/人数）:")
    rows = sorted(((uid, club_names.get(uid), c) for uid, c in counts.items()),
                  key=lambda x: -x[2])
    for uid, name, c in rows[:40]:
        print(f"    {uid:>10}  {c:>4}  {name or '?'}")
    print(f"\n    共 {len(counts)} 家俱乐部。要找自己的俱乐部 uid 后加 --club <uid>。")


def run_roster(mem, club_uid):
    """扫描所有球员，按合同俱乐部过滤并输出名单。club_uid 为 None 时输出俱乐部汇总。"""
    print("[roster] 扫描球员记录 ...")
    recs = scan_player_records(mem)
    print(f"    命中 {len(recs)} 条球员记录，逐条解析 ...")
    players = []
    club_names = {}
    for r in recs:
        p = read_player(mem, r)
        if p["contract"] is None:
            continue
        players.append(p)
        if p["club_entry"]:
            club_names.setdefault(p["contract"], _club_name(mem, p["club_entry"]))
    print(f"    解析 {len(players)} 名有俱乐部合同的球员，涉及 {len(club_names)} 家俱乐部")

    if club_uid is None:
        # 汇总：所有俱乐部 + 球员数
        _print_club_summary(mem, players)
        return

    mine = [p for p in players if p["contract"] == club_uid]
    mine.sort(key=lambda p: (-(p["ca"] or 0), -(p["pa"] or 0)))
    print(f"\n[roster] 俱乐部 {club_uid} {club_names.get(club_uid) or '?'} 球员 {len(mine)} 人：")
    print(f"    {'姓名':<26}{'年龄':>4}{'生日':<12}{'CA':>4}{'PA':>4}  {'状态'}")
    for p in mine:
        loan = (p["current"] is not None and p["current"] != club_uid)
        age = None
        if p["year"] and 1900 < p["year"] < 2100:
            try:
                import datetime
                bd = datetime.date(p["year"], 1, 1) + datetime.timedelta(days=p["doy"] - 1)
                age = (datetime.date.today() - bd).days // 365
            except Exception:
                age = None
        dob = doy_to_date(p["year"], p["doy"]) if p["year"] else "-"
        status = f"租出->{p['current']}" if loan else ""
        print(f"    {str(p['name'] or '?')[:26]:<26}{str(age or '-'):>4}{dob:<12}"
              f"{p['ca'] or '-':>4}{p['pa'] or '-':>4}  {status}")
    n_loan = sum(1 for p in mine if p["current"] is not None and p["current"] != club_uid)
    n_here = len(mine) - n_loan
    print(f"\n    在队 {n_here} 人，租出 {n_loan} 人。")


# ── 文件版本读取（识别 FM 版本/更新档）────────────────────
def file_version(path):
    """返回 (fixed_info_str, product_version_str) 或 None。"""
    version_dll = ctypes.WinDLL("version.dll", use_last_error=True)
    version_dll.GetFileVersionInfoSizeW.restype = wt.DWORD
    version_dll.GetFileVersionInfoSizeW.argtypes = [wt.LPCWSTR, ctypes.POINTER(wt.DWORD)]
    version_dll.GetFileVersionInfoW.restype = wt.BOOL
    version_dll.GetFileVersionInfoW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p]
    version_dll.VerQueryValueW.restype = wt.BOOL
    version_dll.VerQueryValueW.argtypes = [ctypes.c_void_p, wt.LPCWSTR,
                                           ctypes.POINTER(ctypes.c_void_p),
                                           ctypes.POINTER(wt.UINT)]

    if not os.path.isfile(path):
        return None
    handle = wt.DWORD(0)
    size = version_dll.GetFileVersionInfoSizeW(path, ctypes.byref(handle))
    if not size:
        return None
    buf = ctypes.create_string_buffer(size)
    if not version_dll.GetFileVersionInfoW(path, 0, size, buf):
        return None

    fixed = ""
    ptr = ctypes.c_void_p()
    ln = wt.UINT(0)
    if version_dll.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(ln)):
        v = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_uint32))
        ms, ls = v[0], v[1]
        fixed = f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"

    product = ""
    if version_dll.VerQueryValueW(buf, "\\StringFileInfo\\040904b0\\ProductVersion",
                                  ctypes.byref(ptr), ctypes.byref(ln)):
        try:
            product = ctypes.wstring_at(ptr.value, ln.value).split("\x00")[0]
        except Exception:
            product = ""
    return fixed, product


# ── 主流程 ─────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="FM24 内存探测")
    ap.add_argument("--name", default=None,
                    help="要在内存里查找的锚点字符串（默认：最新存档名）")
    ap.add_argument("--module", action="store_true", help="只列模块不扫描")
    ap.add_argument("--chain", action="store_true",
                    help="复现已验证的入口指针链（游戏基址->数据大表）")
    ap.add_argument("--roster", action="store_true",
                    help="枚举球员记录并输出名单（可加 --club 过滤）")
    ap.add_argument("--club", type=int, default=None,
                    help="与 --roster 配合：只显示该俱乐部(uid)的球员；不填则输出俱乐部汇总")
    args = ap.parse_args()

    # 1. 附加
    print("[1] 查找 Football Manager 进程 ...")
    mem = FmMemory.attach(r"^(fm|footballmanager)\.exe$")
    with mem:
        print(f"    附加成功 PID={mem.pid}")

        # 2. 模块
        print("[2] 模块信息 ...")
        mods = mem.modules()
        print(f"    共 {len(mods)} 个模块")
        exe_mod = next((m for m in mods if m[2].lower().endswith(".exe")), None)
        plugin = mem.module(r"game_plugin\.dll$")
        for label, m in (("主程序", exe_mod), ("game_plugin", plugin)):
            if m:
                print(f"    {label}: base=0x{m[0]:012X}  size=0x{m[1]:X}  {m[2]}")
        if not exe_mod:
            print("    !! 没找到主程序模块，可能版本不同")
            return

        # 3. 文件版本
        print("[3] 文件版本（确认 FM 更新档） ...")
        ver = file_version(exe_mod[3])
        if ver:
            print(f"    {exe_mod[2]} 固定版本={ver[0]}  产品版本={ver[1]}")
        else:
            print(f"    读取 {exe_mod[3]} 版本信息失败")

        if args.chain:
            run_chain(mem, exe_mod)
            return

        if args.roster:
            run_roster(mem, args.club)
            return

        if args.module:
            return

        # 4. 锚点字符串
        if args.name:
            anchors = [args.name]
        else:
            saves = sorted(glob.glob(str(FM_DIR / "games" / "*.fm")),
                           key=os.path.getmtime, reverse=True)
            anchor = Path(saves[0]).stem if saves else None
            anchors = [anchor] if anchor else []
            print(f"    [自动] 最新存档名: {anchor}")
            if args.name:
                anchors.append(args.name)
            else:
                anchors.append("Football Manager")
                anchors.append("footballmanager")

        for text in anchors:
            print(f"[4] 内存查找锚点: {text!r}")
            hits = mem.find_utf16(text, max_hits=8)
            if hits:
                print(f"    命中 {len(hits)} 处:")
                for h in hits[:8]:
                    mod = mem.module("")
                    print(f"      0x{h:012X}")
            else:
                print("    未命中（尝试用 --name 指定球员名/俱乐部名）")

        # 5. 大模块内存概况（估算数据存放区）
        print("[5] 已提交内存区域概况 ...")
        total = 0
        biggest = []
        for addr, size in mem.iter_regions():
            total += size
            biggest.append((size, addr))
        biggest.sort(reverse=True)
        print(f"    已提交可读区域合计 {total / 1024 / 1024:.1f} MB")
        print("    最大的几个区域（可能是游戏数据区）:")
        for size, addr in biggest[:5]:
            print(f"      0x{addr:012X}  size=0x{size:X} ({size / 1024 / 1024:.1f} MB)")

    print("\n完成。下一步用找到的锚点地址做结构定位（见注释）。")


if __name__ == "__main__":
    main()
