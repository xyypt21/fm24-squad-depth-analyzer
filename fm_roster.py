"""Parse the RTF roster table exported by Football Manager, or read it from memory."""

import datetime
import sys
import warnings
from pathlib import Path
from typing import Dict, List, Optional

from fm_config import load_config
from fm_positions import parse_position_tokens


def _ensure_probe_path():
    """Make probe/ importable when this file runs from the repo root."""
    probe = Path(__file__).parent / "probe"
    if str(probe) not in sys.path:
        sys.path.insert(0, str(probe))


def calc_age_from_ymd(year, doy):
    """由出生年/年内第几天算出精确周岁（按游戏内日期）。"""
    if not year or not (1900 < year < 2100):
        return None
    try:
        _ensure_probe_path()
        from fm24_probe import calc_age
        bd = datetime.date(year, 1, 1) + datetime.timedelta(days=doy - 1)
        return calc_age(bd)
    except Exception:
        return None


def _clean(text: str) -> str:
    """Strip zero-width spaces and surrounding whitespace (FM exports carry \\u200b)."""
    return "".join(c for c in text if c != "\u200b").strip()


def _num(text: str) -> int:
    """Extract the digits out of a cell."""
    return int("".join(c for c in text if c.isdigit()))


def _parse_columns(headers: List[str]) -> Dict[str, int]:
    """Locate each column from the header row (ability is exported twice; take the 2nd as CA)."""
    col_index: Dict[str, int] = {}
    ca_count = 0
    for i, header in enumerate(headers):
        if header == "姓名":
            col_index["name"] = i
        elif header == "年龄":
            col_index["age"] = i
        elif header == "位置":
            col_index["pos"] = i
        elif header == "能力":
            ca_count += 1
            if ca_count == 2:
                col_index["ca"] = i
        elif header == "潜力":
            col_index["pa"] = i
    return col_index


def read_roster_from_rtf(path: Optional[Path] = None) -> List[dict]:
    """
    Read the RTF table exported by FM and return the list of players.
    Each player has: name, age, position, ca, pa.
    path: RTF file path; defaults to the path in the config file.
    """
    path = Path(path) if path else Path(load_config()["rtf_path"])
    path = path.expanduser()
    if not path.exists():
        return []

    content = path.read_text(encoding="utf-8", errors="replace")
    lines = [line for line in content.split("\n") if "|" in line and "---" not in line]
    if not lines:
        return []

    # Parse the header row to locate each column.
    headers = [_clean(cell) for cell in lines[0].split("|")]
    col_index = _parse_columns(headers)

    # Parse player data line by line.
    players: List[dict] = []
    for line in lines[1:]:
        cells = [_clean(cell) for cell in line.split("|")]
        try:
            name = cells[col_index["name"]]
            age = _num(cells[col_index["age"]])
            pos = cells[col_index["pos"]]
            ca = _num(cells[col_index["ca"]])
            pa = _num(cells[col_index["pa"]])
        except (ValueError, KeyError, IndexError):
            continue
        if age <= 0 or not pos or ca <= 0:
            continue
        if not parse_position_tokens(pos):
            warnings.warn(f"Unrecognized position for {name}: {pos}", stacklevel=2)
        players.append({"name": name, "age": age, "position": pos, "ca": ca, "pa": pa})

    return players


def _to_roster(players):
    """把读到的球员 dict 列表转换成 analyze 所需的 {name,age,position,ca,pa}。

    名字不在此处汉化：只在 analyze 选中 XI 后，对出现在网页里的球员翻译。
    """
    roster = []
    for p in players:
        if not p.get("position") or p["position"] == "-":
            continue
        age = calc_age_from_ymd(p["year"], p["doy"])
        if age is None:
            continue
        roster.append({"name": p["name"], "age": age, "position": p["position"],
                       "ca": p["ca"], "pa": p["pa"]})
    return roster


def read_squad_from_memory(club_uid: int) -> tuple:
    """
    一次扫描返回 (队名, roster)。CLI/GUI 主入口（避免名字+阵容各扫一遍全内存）。
    """
    _ensure_probe_path()
    from fm24_probe import club_squad, refresh_game_date
    from fm_memory import FmMemory

    mem = FmMemory.attach(r"^(fm|footballmanager)\.exe$")
    with mem:
        refresh_game_date(mem)
        name, players = club_squad(mem, club_uid)
    return name, _to_roster(players)


def read_roster_from_memory(club_uid: int) -> List[dict]:
    """
    Read the squad directly from the running FM24 game memory.

    Each player has: name, age, position, ca, pa.
    club_uid: the club id to filter the squad by (matches config 'club_uid').
    Raises RuntimeError if the game cannot be attached.
    """
    _name, roster = read_squad_from_memory(club_uid)
    return roster


def club_name_from_memory(club_uid: int) -> Optional[str]:
    """按俱乐部 uid 从内存反查名字；找不到返回 None。"""
    _ensure_probe_path()
    from fm24_probe import club_name
    from fm_memory import FmMemory

    mem = FmMemory.attach(r"^(fm|footballmanager)\.exe$")
    with mem:
        return club_name(mem, club_uid)
