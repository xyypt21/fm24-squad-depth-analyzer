"""Read the squad directly from the running FM24 game memory."""

import datetime
from typing import Optional

from fm24_probe import calc_age, club_name, club_squad, refresh_game_date
from fm_memory import FmMemory


def calc_age_from_ymd(year, doy):
    """由出生年/年内第几天算出精确周岁（按游戏内日期）。"""
    if not year or not (1900 < year < 2100):
        return None
    try:
        bd = datetime.date(year, 1, 1) + datetime.timedelta(days=doy - 1)
        return calc_age(bd)
    except Exception:
        return None


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
        roster.append(
            {"name": p["name"], "age": age, "position": p["position"], "ca": p["ca"], "pa": p["pa"]}
        )
    return roster


def read_squad_from_memory(club_uid: int) -> tuple:
    """
    一次扫描返回 (队名, roster)。CLI/GUI 主入口（避免名字+阵容各扫一遍全内存）。
    """
    mem = FmMemory.attach(r"^(fm|footballmanager)\.exe$")
    with mem:
        refresh_game_date(mem)
        name, players = club_squad(mem, club_uid)
    return name, _to_roster(players)


def club_name_from_memory(club_uid: int) -> Optional[str]:
    """按俱乐部 uid 从内存反查名字；找不到返回 None。"""
    mem = FmMemory.attach(r"^(fm|footballmanager)\.exe$")
    with mem:
        return club_name(mem, club_uid)
