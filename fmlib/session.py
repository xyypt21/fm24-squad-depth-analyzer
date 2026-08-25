"""游戏会话：附加 FM 进程，提供基址、文件版本、偏移表与游戏内日期。"""

import ctypes
import datetime
import re
from typing import Optional

from fmlib.memory import FmMemory
from fmlib.offsets import Offsets, load_offsets

PROC_PATTERN = r"^(fm|footballmanager)\.exe$"

# 游戏日期编码（与 fm24_probe.py 一致）：
#   u32 = (年 << 16) | (时间字节 << 8) | 年内第几天(低8位)
#   时间字节 bit0 = doy 进位（第 256 天标志），bit1-7 是时间（8 单位/小时）
GAME_DATE_TIME_OFF = 46  # 分钟 = (时间字节 + 46) * 7.5
GAME_DATE_UNIT_MIN = 7.5


def file_version(path):
    """读取 exe 文件版本，返回 (fixed, product) 字符串元组或 None。"""
    version_dll = ctypes.WinDLL("version.dll", use_last_error=True)
    version_dll.GetFileVersionInfoSizeW.restype = ctypes.c_uint32
    version_dll.GetFileVersionInfoSizeW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.POINTER(ctypes.c_uint32),
    ]
    version_dll.GetFileVersionInfoW.restype = ctypes.c_bool
    version_dll.GetFileVersionInfoW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    version_dll.VerQueryValueW.restype = ctypes.c_bool
    version_dll.VerQueryValueW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_uint32),
    ]
    try:
        if not path:
            return None
        handle = ctypes.c_uint32(0)
        size = version_dll.GetFileVersionInfoSizeW(str(path), ctypes.byref(handle))
        if not size:
            return None
        buf = ctypes.create_string_buffer(size)
        if not version_dll.GetFileVersionInfoW(str(path), 0, size, buf):
            return None
        fixed = ""
        ptr = ctypes.c_void_p()
        ln = ctypes.c_uint32(0)
        if version_dll.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(ln)):
            v = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_uint32))
            ms, ls = v[0], v[1]
            fixed = f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
        product = ""
        if version_dll.VerQueryValueW(
            buf, "\\StringFileInfo\\040904b0\\ProductVersion", ctypes.byref(ptr), ctypes.byref(ln)
        ):
            product = ctypes.wstring_at(ptr.value, ln.value).split("\x00")[0]
        return fixed, product
    except Exception:
        return None


class GameSession:
    """一次游戏附加会话（只读）。用完 close() 或 with。

    通常用 GameSession.attach() 创建；也可在已有 FmMemory 上用
    attach_from()（不接管 mem 的生命周期）。
    """

    def __init__(
        self,
        mem: FmMemory,
        offsets: Offsets,
        file_version_str: Optional[str] = None,
        product_version: Optional[str] = None,
    ):
        self.mem = mem
        self.offsets = offsets
        exe = mem.exe_module
        if not exe:
            raise RuntimeError("进程里找不到主可执行模块 (fm.exe)。")
        self.base = exe[0]
        self.exe_path = exe[3]
        self.file_version = file_version_str
        self.product_version = product_version

    @classmethod
    def attach(cls, version_key: Optional[str] = None) -> "GameSession":
        """附加到运行中的游戏并加载对应偏移，返回会话（接管 mem）。"""
        mem = FmMemory.attach(PROC_PATTERN)
        try:
            return cls.attach_from(mem, version_key)
        except Exception:
            mem.close()
            raise

    @classmethod
    def attach_from(cls, mem: FmMemory, version_key: Optional[str] = None) -> "GameSession":
        """在已打开的 FmMemory 上构建会话（不接管 mem 的生命周期）。

        version_key: 指定偏移版本（如 "24.4"）；None 时先从 exe 文件版本
        推断主.次版本号，推断失败则用偏移表第一个版本兜底。
        """
        exe = mem.exe_module
        if not exe:
            raise RuntimeError("进程里找不到主可执行模块 (fm.exe)。")
        ver = file_version(exe[3])
        fixed = ver[0] if ver else None
        product = ver[1] if ver else None

        if version_key is None and product:
            m = re.match(r"(\d+)\.(\d+)", product)
            if m:
                version_key = f"{m.group(1)}.{m.group(2)}"
        try:
            offsets = load_offsets(version_key=version_key)
        except ValueError:
            if version_key is not None:
                raise
            # 版本探测失败：用表内第一版兜底（可能不匹配，调用方应提示）
            offsets = load_offsets(version_key=None)
        return cls(mem, offsets, file_version_str=fixed, product_version=product)

    def close(self):
        self.mem.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def read_game_datetime(self) -> Optional[datetime.datetime]:
        """读游戏内当前日期+时间；失败返回 None。编码见模块注释。"""
        v = self.mem.read_u32(self.base + self.offsets.game_date_rva)
        if v is None:
            return None
        year = v >> 16
        tb = (v >> 8) & 0xFF
        doy = (v & 0xFF) + (tb & 1) * 256
        time_unit = tb & 0xFE
        try:
            d = datetime.date(year, 1, 1) + datetime.timedelta(days=doy - 1)
        except ValueError:
            return None
        if not (2000 <= year <= 2100 and 1 <= doy <= 366):
            return None
        mins = int(round((time_unit + GAME_DATE_TIME_OFF) * GAME_DATE_UNIT_MIN))
        hour, minute = divmod(mins, 60)
        return datetime.datetime.combine(d, datetime.time(hour % 24, minute))

    def game_date(self) -> Optional[datetime.date]:
        dtm = self.read_game_datetime()
        return dtm.date() if dtm else None


def attach_session(version_key: Optional[str] = None) -> GameSession:
    """便捷入口：附加游戏并返回会话。"""
    return GameSession.attach(version_key)
