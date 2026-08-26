"""全局数据表遍历：[exe+root_rva] -> 容器(+container_off) -> 表头 -> 数组。

fm_offsets_info.json 里 person/club/team/comp/currency 等表共用这套链式
布局（与 fm24_probe.run_chain 验证过的 person 表一致）。数组有两种可能：
  A. 内联记录：记录首字段是 tagged 类型 id，步长随类型固定（如 person 0x268）
  B. 指针数组：连续 8 字节指针指向别处的记录对象
本模块自动探测属于哪种，并枚举全部记录地址。
"""

import struct
from collections import Counter
from dataclasses import dataclass, field
from typing import List

from fmlib.memory import FmMemory

MIN_PTR = 0x10000
MAX_PTR = 0x7FF000000000
PROBE_BYTES = 0x40000  # 格式探测读取量
MAX_RECORDS = 500000


class TableError(RuntimeError):
    """表链走不通或格式无法识别（附诊断信息）。"""


@dataclass
class TableInfo:
    """遍历结果元信息（供日志/诊断）。"""

    mode: str  # 'inline' | 'ptr'
    stride: int
    start: int  # 数组基址（inline 为首记录地址；ptr 为首槽位地址）
    count: int  # 枚举到的记录数
    truncated: bool = False
    notes: List[str] = field(default_factory=list)


def _plausible(v: int) -> bool:
    return MIN_PTR <= v < MAX_PTR


def _probe_inline(buf: bytes, tag4: bytes):
    """在内联假设下找步长：tag 出现位置的相邻差众数。返回 (start_off, stride)。"""
    offs = [i for i in range(0, len(buf) - 3, 4) if buf[i : i + 4] == tag4]
    if len(offs) < 8:
        return None
    diffs = Counter(b - a for a, b in zip(offs, offs[1:]))
    stride, votes = diffs.most_common(1)[0]
    if not (16 <= stride <= 0x8000):
        return None
    if votes < 6 or votes * stride < len(buf) // 2:
        return None  # 覆盖率不足，不像整片规则记录
    return offs[0], stride


def _looks_like_ptr_array(mem: FmMemory, arr: int, tag: int) -> bool:
    """前 32 槽大多像指针，且抽样目标头部是 tag。"""
    qwords = []
    for i in range(32):
        v = mem.read_u64(arr + i * 8)
        if v is None:
            break
        qwords.append(v)
    if len(qwords) < 12:
        return False
    if sum(1 for v in qwords if _plausible(v)) < len(qwords) * 0.9:
        return False
    checked = 0
    for v in qwords[:8]:
        if not _plausible(v):
            continue
        head = mem.read_u32(v)
        if head is None:
            continue
        if head != tag:
            return False
        checked += 1
        if checked >= 3:
            break
    return checked >= 3


def _resolve_array(mem: FmMemory, base: int, root_rva: int, container_off: int):
    """走链 [exe+root] -> 容器(+off) -> 表头 -> 数组基址。返回 (arr, notes)。"""
    notes = []
    p2 = mem.read_ptr(base + root_rva)
    if not _plausible(p2 or 0):
        raise TableError(f"[exe+0x{root_rva:X}] 容器指针无效: {p2}")
    hdr = mem.read_ptr(p2 + container_off)
    if not _plausible(hdr or 0):
        raise TableError(f"容器+0x{container_off:X} 表头无效: {hdr} (容器 0x{p2:X})")
    arr = mem.read_ptr(hdr)
    if not _plausible(arr or 0):
        arr = hdr  # 兼容：表头本身即数组基址（少一层间接）
        notes.append("[hdr] 无效，改用表头本身作数组基址")
        if not _plausible(arr):
            raise TableError(f"表头 0x{hdr:X} 解不出数组基址")
    return arr, notes


def _detect_format(mem: FmMemory, arr: int, type_tag: int, tag4: bytes, notes: List[str]):
    """探测数组格式，返回 (mode, stride, start)。"""
    buf = mem.read_bytes(arr, PROBE_BYTES) or b""
    inline = _probe_inline(buf, tag4) if buf else None
    if inline is not None:
        start_off, stride = inline
        if start_off:
            notes.append(f"首记录偏移 +0x{start_off:X}")
        return "inline", stride, arr + start_off
    if _looks_like_ptr_array(mem, arr, type_tag):
        return "ptr", 8, arr
    raise TableError(
        f"数组 0x{arr:X} 既不像内联记录也不像指针数组（tag={tag4.hex()}，探测头 {len(buf)} 字节）"
    )


def walk_table(
    mem: FmMemory,
    base: int,
    root_rva: int,
    container_off: int,
    type_tag: int,
    max_records: int = MAX_RECORDS,
):
    """遍历一张全局记录表，返回 (record_addr_list, TableInfo)。

    record_addr_list 在 inline 模式下是各记录起始地址；
    在 ptr 模式下是被指向的记录对象地址。
    """
    if not type_tag:
        raise TableError("type_tag 为空（偏移表缺少该表的 vtable id）")
    tag4 = struct.pack("<I", type_tag)
    arr, notes = _resolve_array(mem, base, root_rva, container_off)
    mode, stride, start = _detect_format(mem, arr, type_tag, tag4, notes)

    # ── 枚举 ──
    if mode == "ptr":
        records, truncated = _enum_ptr(mem, start, stride, max_records)
    else:
        records, truncated = _enum_inline(mem, start, stride, tag4, max_records, notes)

    if not records:
        raise TableError(f"数组 0x{arr:X} 探测到 {mode} 格式但枚举出 0 条")
    return records, TableInfo(
        mode=mode,
        stride=stride,
        start=start,
        count=len(records),
        truncated=truncated,
        notes=notes,
    )


def _enum_ptr(mem: FmMemory, start: int, stride: int, max_records: int):
    """指针数组模式：读到第一个非法槽位为止，返回 (目标地址列表, 是否截断)。"""
    records = []
    slot = start
    while len(records) < max_records:
        v = mem.read_u64(slot)
        if not v or not _plausible(v):
            break
        records.append(v)
        slot += stride
    return records, len(records) >= max_records


def _enum_inline(
    mem: FmMemory,
    start: int,
    stride: int,
    tag4: bytes,
    max_records: int,
    notes: List[str],
):
    """内联记录模式：按步长批量读，遇到头字段非 tag 即停。"""
    records = []
    pos = 0
    CHUNK = 2048
    while pos < max_records:
        n = min(CHUNK, max_records - pos)
        data = mem.read_bytes(start + pos * stride, n * stride)
        if not data:
            notes.append(f"+{pos} 条处批量读失败")
            break
        k = len(data) // stride
        stopped = False
        for i in range(k):
            o = i * stride
            if data[o : o + 4] != tag4:
                stopped = True
                break
            records.append(start + (pos + i) * stride)
        pos += k
        if stopped or k < n:
            break
    return records, pos >= max_records
