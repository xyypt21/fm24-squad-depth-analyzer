"""球队/俱乐部记录读取。

数据模型（见 fm_offsets_info.json）：TEAM 记录首字段是 tagged 类型 id
（vtb_team），TEAM+club_ptr 指向 CLUB 记录（tag vtb_club），CLUB 内有
uid 与名字缓冲指针。
"""

from typing import Optional

from memory import FmMemory
from offsets import Offsets

MAX_UID = 1 << 31


def type_id_ok(mem: FmMemory, base: int, addr: int, tagged_id: int) -> bool:
    """校验记录头类型标记。

    存储形式为 低u32 = tagged_id；兼容个别版本存真实 vtable 指针
    （base+rva）的情况。
    """
    v = mem.read_u64(addr)
    if v is None:
        return False
    if (v & 0xFFFFFFFF) == tagged_id:
        return True
    rva = tagged_id & 0x3FFFFFFF
    return v == base + rva


def read_len_str(mem: FmMemory, ptr: int, max_len=120) -> Optional[str]:
    """读名字缓冲：[u32 len][utf-8 串]；ptr 也可能是持有者(->[0]->串)。"""
    if not ptr:
        return None
    for _ in range(3):
        b = mem.read_bytes(ptr, max_len + 12)
        if not b:
            return None
        ln = int.from_bytes(b[0:4], "little")
        if 0 < ln <= max_len:
            raw = b[4 : 4 + ln]
            if all(32 <= c < 127 or c > 0x7F for c in raw):
                return raw.decode("utf-8", "replace")
        v = int.from_bytes(b[0:8], "little")
        if v and 0x10000 <= v < 0x7FF000000000:
            ptr = v
            continue
        return None
    return None


def uid_sane(uid) -> bool:
    return uid is not None and 0 < uid < MAX_UID


def read_club_uid(mem: FmMemory, off: Offsets, club_addr: int) -> Optional[int]:
    """CLUB 记录 -> 俱乐部 uid。"""
    if not club_addr:
        return None
    return mem.read_u32(club_addr + off.club_uid_off)


def read_club_name(mem: FmMemory, off: Offsets, club_addr: int) -> Optional[str]:
    """CLUB 记录 -> 全名：[[club+name_off] 名字缓冲]。"""
    if not club_addr:
        return None
    nptr = mem.read_ptr(club_addr + off.club_name_off)
    return read_len_str(mem, nptr)
