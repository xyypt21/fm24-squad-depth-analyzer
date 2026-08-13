"""Read the squad directly from the running FM24 game memory."""

import datetime
from typing import Optional

import fm24_probe
from fm24_probe import calc_age, club_name, club_squad, merged_squad, refresh_game_date
from fm_memory import FmMemory


def get_game_date():
    """返回最近一次 refresh_game_date 后的游戏内日期（datetime.date 或 None）。"""
    return fm24_probe.GAME_DATE


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

    名字已在 read_player 里优先用游戏显示名（common_name 短名），无短名时
    才是拼出的 名+姓。不在此处汉化：只在 analyze 选中 XI 后，对出现在
    网页里的球员翻译。
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


def read_merged_squad_from_memory(club1: int, club2: int) -> tuple:
    """一次扫描返回 (club1_name, club2_name, roster)。

    合并规则见 fm24_probe.merged_squad：club1 自有球员 + 租到 club2 的
    club1 球员 + club2 自有球员；租到其他俱乐部的排除。
    """
    mem = FmMemory.attach(r"^(fm|footballmanager)\.exe$")
    with mem:
        refresh_game_date(mem)
        name1, name2, players = merged_squad(mem, club1, club2)
    return name1, name2, _to_roster(players)


def read_squad_from_memory(club_uid: int) -> tuple:
    """
    一次扫描返回 (队名, roster)。CLI 主入口（避免名字+阵容各扫一遍全内存）。
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
