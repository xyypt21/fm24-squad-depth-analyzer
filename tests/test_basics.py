"""基础单元测试：不依赖游戏进程，只测纯逻辑与自进程内存原语。

运行：python -m pytest tests/  或  python tests/test_basics.py
"""

import os
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import memory
import user_club
from offsets import load_offsets, type_tag


def test_segmentize():
    segs = user_club._segmentize([0x1000, 0x1000 + 0x3E8, 0x1000 + 2 * 0x3E8, 0x50000])
    assert segs == [[0x1000, 3], [0x50000, 1]]


def test_type_tag():
    # vtb_team RVA -> 记录头 tagged id（fm24_probe 实测值交叉验证）
    assert type_tag(0x5A78848) == 0x45A78848


def test_load_offsets():
    off = load_offsets()  # 默认取第一个版本块（24.4）
    assert off.mgr_hnp_rva > 0 and off.team_manager_ptr_off == 0x80
    try:
        load_offsets(version_key="99.9")
        raise AssertionError("mismatch should raise")
    except ValueError:
        pass


class FakeMem:
    """基于本地缓冲的假内存，覆盖 read_bytes/read_u64。"""

    def __init__(self, buf, base):
        self.buf = bytes(buf)
        self.base = base

    def read_bytes(self, addr, size):
        off = addr - self.base
        if off < 0:
            return None
        return self.buf[off : off + size] or None

    def read_u64(self, addr):
        b = self.read_bytes(addr, 8)
        return int.from_bytes(b, "little") if b and len(b) == 8 else None


def test_read_len_str():
    from clubs import read_len_str

    name = b"Schalke"
    buf = struct.pack("<I", len(name)) + name + b"\x00" * 8
    base = 0x600000000000
    mem = FakeMem(buf, base)
    assert read_len_str(mem, base) == "Schalke"


def test_memory_self_process():
    """用本进程做内存读取冒烟：模块枚举 + PE 头。"""
    pid = os.getpid()
    h = memory.kernel32.OpenProcess(
        memory.PROCESS_QUERY_INFORMATION | memory.PROCESS_VM_READ, False, pid
    )
    mem = memory.FmMemory(pid, h)
    with mem:
        assert mem.base and mem.read_bytes(mem.base, 2) == b"MZ"


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    test_segmentize()
    print("segmentize OK")
    test_type_tag()
    print("type_tag OK")
    test_load_offsets()
    print("load_offsets OK")
    test_read_len_str()
    print("read_len_str OK")
    test_memory_self_process()
    print("memory self-process OK")
