"""fmlib —— FM24 内存读取重构层（tmp 分支实验）。

分层：
    memory    通用只读跨进程内存读取（Windows API，ctypes）
    offsets   fm_offsets_info.json 偏移表的加载与按版本选择
    session   附加游戏进程：进程/模块/文件版本/游戏内日期
    clubs     俱乐部/球队记录读取（uid、队名）
    user_club 用户俱乐部自动检测（人控经理链 + 指针反查）
"""

from fmlib.clubs import read_club_name, read_team_club
from fmlib.memory import FmMemory
from fmlib.offsets import Offsets, load_offsets
from fmlib.session import GameSession, attach_session
from fmlib.user_club import ClubInfo, detect_user_clubs

__all__ = [
    "ClubInfo",
    "FmMemory",
    "GameSession",
    "Offsets",
    "attach_session",
    "detect_user_clubs",
    "load_offsets",
    "read_club_name",
    "read_team_club",
]
