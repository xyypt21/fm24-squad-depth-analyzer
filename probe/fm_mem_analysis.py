"""
FM24 内存阵容分析 —— 直接从游戏内存读球员，复用 fm_analysis 生成阵容厚度 HTML。

用法：
    python fm_mem_analysis.py                      # 先列俱乐部汇总找 uid
    python fm_mem_analysis.py --club <uid>         # 分析指定俱乐部
    python fm_mem_analysis.py --club <uid> --no-open

前置条件：
    - FM24 运行中且已载入存档
"""

import argparse
import datetime
import os
import sys

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

# 允许以 python probe/fm_mem_analysis.py 运行：probe/ 里的脚本能 import
# 根目录的 fm_analysis / fm_positions 等模块。
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from fm_memory import FmMemory
from fm_analysis import analyze, OUTPUT
from fm24_probe import (collect_roster, _print_club_summary, calc_age,
                        refresh_game_date, GAME_DATE)


def calc_age_from_ymd(year, doy):
    """由出生年/年内第几天算出精确周岁（按游戏内日期，与 FM Scouting Tool 一致）。"""
    if not year or not (1900 < year < 2100):
        return None
    try:
        bd = datetime.date(year, 1, 1) + datetime.timedelta(days=doy - 1)
        return calc_age(bd)
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser(description="FM24 内存阵容分析")
    ap.add_argument("--club", type=int, default=None,
                    help="俱乐部 uid（不填则输出俱乐部汇总）")
    ap.add_argument("--no-open", action="store_true", help="生成 HTML 后不打开浏览器")
    args = ap.parse_args()

    mem = FmMemory.attach(r"^(fm|footballmanager)\.exe$")
    with mem:
        print("[mem-analysis] 读取游戏日期 ...")
        gd = refresh_game_date(mem)
        print(f"    游戏日期: {gd or '读取失败'}" + ("" if gd else f"（沿用默认 {GAME_DATE}）"))

        print("[mem-analysis] 收集球员记录 ...")
        players = collect_roster(mem)
        print(f"    共 {len(players)} 名有合同球员")

        if args.club is None:
            _print_club_summary(mem, players)
            print("    用 --club <uid> 指定要分析的俱乐部。")
            return

        roster = []
        for p in players:
            if p["contract"] != args.club:
                continue
            pos = p.get("position") or "-"
            if not pos or pos == "-":
                print("!! 位置未读到（偏移失效？）")
                return
            age = calc_age_from_ymd(p["year"], p["doy"])
            if age is None:
                continue
            roster.append(dict(name=p["name"], age=age, position=pos,
                               ca=p["ca"], pa=p["pa"]))

        if not roster:
            print(f"俱乐部 {args.club} 没有读到球员（uid 是否正确？）")
            return

        roster.sort(key=lambda x: -(x["ca"] or 0))
        print(f"    俱乐部 {args.club} 读到 {len(roster)} 名球员，开始分析 ...")
        html = analyze(roster)
        if html and not args.no_open:
            os.startfile(str(OUTPUT))
        print(f"    已写出 {OUTPUT}")


if __name__ == "__main__":
    main()
