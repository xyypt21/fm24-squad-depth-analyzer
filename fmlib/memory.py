"""通用只读跨进程内存读取层（Windows API / ctypes）。

与游戏版本无关：找进程 -> 打开只读句柄 -> 列模块 -> 按地址读内存 -> 模式扫描。
不注入、不修改游戏。

用法：
    mem = FmMemory.attach(r"^(fm|footballmanager)\\.exe$")
    buf = mem.read_bytes(base + rva, 64)
    hits = mem.scan_pattern(pattern, writable_only=True)
"""

import ctypes
import re
import struct
from ctypes import wintypes as wt

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# ── 常量 ──────────────────────────────────────────────
TH32CS_SNAPPROCESS = 0x00000002
TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010

MEM_COMMIT = 0x1000

PAGE_NOACCESS = 0x01
PAGE_GUARD = 0x100

MAX_PATH = 260


# ── Win32 结构 ────────────────────────────────────────
class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("cntUsage", wt.DWORD),
        ("th32ProcessID", wt.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wt.DWORD),
        ("cntThreads", wt.DWORD),
        ("th32ParentProcessID", wt.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wt.DWORD),
        ("szExeFile", wt.WCHAR * MAX_PATH),
    ]


class MODULEENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("th32ModuleID", wt.DWORD),
        ("th32ProcessID", wt.DWORD),
        ("GlblcntUsage", wt.DWORD),
        ("ProccntUsage", wt.DWORD),
        ("modBaseAddr", ctypes.c_void_p),
        ("modBaseSize", wt.DWORD),
        ("hModule", ctypes.c_void_p),
        ("szModule", wt.WCHAR * 256),
        ("szExePath", wt.WCHAR * MAX_PATH),
    ]


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    """x64 布局（含 PartitionId），见 winnt.h MEMORY_BASIC_INFORMATION。"""

    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wt.DWORD),
        ("PartitionId", wt.WORD),
        ("_pad", wt.WORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wt.DWORD),
        ("Protect", wt.DWORD),
        ("Type", wt.DWORD),
    ]


# ── API 原型 ──────────────────────────────────────────
kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
kernel32.Process32FirstW.restype = wt.BOOL
kernel32.Process32NextW.restype = wt.BOOL
kernel32.Module32FirstW.restype = wt.BOOL
kernel32.Module32NextW.restype = wt.BOOL
kernel32.OpenProcess.restype = ctypes.c_void_p
kernel32.ReadProcessMemory.restype = wt.BOOL
kernel32.VirtualQueryEx.restype = ctypes.c_size_t
kernel32.CloseHandle.restype = wt.BOOL

kernel32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
kernel32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.Module32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MODULEENTRY32W)]
kernel32.Module32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MODULEENTRY32W)]
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.ReadProcessMemory.argtypes = [
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
kernel32.VirtualQueryEx.argtypes = [
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.POINTER(MEMORY_BASIC_INFORMATION),
    ctypes.c_size_t,
]
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]


def last_error_text():
    return ctypes.FormatError(ctypes.get_last_error())


# ── 进程枚举 ──────────────────────────────────────────
def list_processes():
    """枚举所有进程，返回 [(pid, exe_name), ...]"""
    result = []
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == ctypes.c_void_p(-1).value or not snap:
        return result
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if kernel32.Process32FirstW(snap, ctypes.byref(entry)):
            while True:
                result.append((entry.th32ProcessID, entry.szExeFile))
                if not kernel32.Process32NextW(snap, ctypes.byref(entry)):
                    break
    finally:
        kernel32.CloseHandle(snap)
    return result


def find_pid(name_pattern):
    """按 exe 名字（支持正则、忽略大小写）找进程 PID。"""
    rx = re.compile(name_pattern, re.IGNORECASE)
    for pid, exe in list_processes():
        if rx.search(exe):
            return pid
    return None


# ── 主类 ──────────────────────────────────────────────
class FmMemory:
    """附加到某进程并只读其内存。"""

    def __init__(self, pid, handle):
        self.pid = pid
        self.handle = handle
        self._modules = None

    # -- 附加 -------------------------------------------------
    @classmethod
    def attach(cls, name_pattern=r"^(fm|footballmanager)\.exe$"):
        """按进程名附加；找不到抛 RuntimeError。"""
        pid = find_pid(name_pattern)
        if pid is None:
            raise RuntimeError(
                f"找不到匹配 '{name_pattern}' 的进程。"
                f"请先启动游戏并载入存档。若以管理员运行了游戏，"
                f"本程序也需要以管理员身份运行。"
            )
        handle = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
        if not handle:
            raise RuntimeError(
                f"OpenProcess({pid}) 失败: {last_error_text()}。"
                f"游戏可能以更高权限运行，请右键本脚本 -> 以管理员身份运行。"
            )
        return cls(pid, handle)

    def close(self):
        if self.handle:
            kernel32.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- 模块 -------------------------------------------------
    def modules(self):
        """返回 [(base, size, name, path), ...]，已按基址排序。"""
        if self._modules is not None:
            return self._modules
        result = []
        for flags in (TH32CS_SNAPMODULE, TH32CS_SNAPMODULE32):
            snap = kernel32.CreateToolhelp32Snapshot(flags, self.pid)
            if snap == ctypes.c_void_p(-1).value or not snap:
                continue
            try:
                entry = MODULEENTRY32W()
                entry.dwSize = ctypes.sizeof(MODULEENTRY32W)
                if not kernel32.Module32FirstW(snap, ctypes.byref(entry)):
                    continue
                seen = set()
                while True:
                    key = entry.modBaseAddr
                    if key not in seen:
                        seen.add(key)
                        result.append((key, entry.modBaseSize, entry.szModule, entry.szExePath))
                    if not kernel32.Module32NextW(snap, ctypes.byref(entry)):
                        break
            finally:
                kernel32.CloseHandle(snap)
        self._modules = sorted(result, key=lambda m: m[0])
        return self._modules

    def module(self, name_pattern):
        """按名字正则找模块，返回 (base, size, name, path) 或 None。"""
        rx = re.compile(name_pattern, re.IGNORECASE)
        for mod in self.modules():
            if rx.search(mod[2]):
                return mod
        return None

    @property
    def exe_module(self):
        """主可执行模块 (base, size, name, path)；找不到为 None。"""
        return next((m for m in self.modules() if m[2].lower().endswith(".exe")), None)

    @property
    def base(self):
        """主可执行模块基址；找不到为 None。"""
        exe = self.exe_module
        return exe[0] if exe else None

    # -- 读内存 ------------------------------------------------
    def read_bytes(self, address, size):
        """读 size 字节，返回 bytes；失败返回 None。"""
        buf = ctypes.create_string_buffer(size)
        read = ctypes.c_size_t(0)
        ok = kernel32.ReadProcessMemory(
            self.handle, ctypes.c_void_p(address), buf, size, ctypes.byref(read)
        )
        if not ok:
            return None
        return buf.raw[: read.value]

    def read_ptr(self, address):
        b = self.read_bytes(address, 8)
        return int.from_bytes(b, "little") if b else None

    def read_u8(self, address):
        b = self.read_bytes(address, 1)
        return b[0] if b else None

    def read_u16(self, address):
        b = self.read_bytes(address, 2)
        return int.from_bytes(b, "little") if b else None

    def read_u32(self, address):
        b = self.read_bytes(address, 4)
        return int.from_bytes(b, "little") if b else None

    def read_u64(self, address):
        b = self.read_bytes(address, 8)
        return int.from_bytes(b, "little") if b else None

    # -- 内存区域遍历（供模式扫描用） --------------------------
    def iter_regions(self, writable_only=False, min_size=0):
        """遍历已提交、可读的内存区域，产出 (addr, size)。

        writable_only: 只看可写堆区（PAGE_READWRITE/WRITECOPY），跳过代码页、
                       只读数据页——球员/球队等数据都在这类区域，能显著加快扫描。
        min_size: 过滤小于该字节数的碎片区域（否则 syscall 过多）。
        """
        PAGE_READWRITE = 0x04
        PAGE_WRITECOPY = 0x08
        addr = 0
        while True:
            mbi = MEMORY_BASIC_INFORMATION()
            n = kernel32.VirtualQueryEx(
                self.handle, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)
            )
            if not n:
                break
            region_addr = mbi.BaseAddress or addr
            region_size = mbi.RegionSize or 0
            if region_size == 0:
                break
            protect = mbi.Protect
            readable = not (protect & PAGE_NOACCESS) and not (protect & PAGE_GUARD)
            ok = mbi.State == MEM_COMMIT and readable
            if ok and min_size and region_size < min_size:
                ok = False
            if ok and writable_only and not (protect & (PAGE_READWRITE | PAGE_WRITECOPY)):
                ok = False
            if ok:
                yield region_addr, region_size
            next_addr = region_addr + region_size
            if next_addr <= addr:
                next_addr = addr + 0x1000
            addr = next_addr

    # -- 模式扫描 ------------------------------------------------
    @staticmethod
    def _compile_pattern(pattern):
        """把模式编译成 (pat, mask)。字符串支持 '??' 通配符，bytes 视为全匹配。"""
        if isinstance(pattern, str):
            parts = pattern.split()
            pat = bytes(int(x, 16) for x in parts if x != "??")
            mask = bytes(0xFF if x != "??" else 0x00 for x in parts)
        else:
            pat = bytes(pattern)
            mask = b"\xff" * len(pat)
        return pat, mask

    @staticmethod
    def _make_finder(pat, mask):
        """返回一个在 data 中产出命中偏移的生成器函数。"""
        plen = len(pat)
        if mask == b"\xff" * plen:

            def _find(data):
                pos = 0
                while True:
                    i = data.find(pat, pos)
                    if i < 0:
                        return
                    yield i
                    pos = i + 1

            return _find
        rx = re.compile(bytes(pat[j] if mask[j] else 0x2E for j in range(plen)), re.DOTALL)

        def _find(data):
            for m in rx.finditer(data):
                yield m.start()

        return _find

    def scan_pattern(
        self,
        pattern,
        chunk=0x100000,
        max_hits=64,
        writable_only=False,
        min_size=0,
    ):
        """全内存扫描字节模式，返回命中地址列表（进程绝对地址）。

        pattern: 支持 '??' 通配符的字节串，也接受 bytes。
        优化：大块读取(1MB)；无通配符用 bytes.find，带通配符用 bytes 正则；
        只扫可写堆（writable_only=True）可显著减小扫描体积。
        """
        pat, mask = self._compile_pattern(pattern)
        if not pat:
            return []
        finder = self._make_finder(pat, mask)

        hits = []
        for addr, rsize in self.iter_regions(writable_only=writable_only, min_size=min_size):
            off = 0
            while off < rsize:
                n = min(chunk, rsize - off)
                data = self.read_bytes(addr + off, n)
                if data:
                    for i in finder(data):
                        hits.append(addr + off + i)
                        if len(hits) >= max_hits:
                            return hits
                off += n
        return hits

    def find_qword(self, value, max_hits=256):
        """在可写内存中找一个 8 字节小端值（指针反查），返回命中地址列表。"""
        return self.scan_pattern(struct.pack("<Q", value), max_hits=max_hits, writable_only=True)

    def hexdump(self, address, size, width=16):
        """调试用：读取并格式化 hexdump 字符串。"""
        data = self.read_bytes(address, size)
        if data is None:
            return f"<读取失败 0x{address:X}>"
        lines = []
        for off in range(0, len(data), width):
            chunk = data[off : off + width]
            hexs = " ".join(f"{b:02X}" for b in chunk)
            ascii_ = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
            lines.append(f"0x{address + off:012X}  {hexs:<{width * 3}}  {ascii_}")
        return "\n".join(lines)
