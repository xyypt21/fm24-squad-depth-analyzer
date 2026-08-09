"""
FM24 内存阵容分析 —— 直接从游戏内存读球员，复用 fm_analysis 生成阵容厚度 HTML。

用法：
    python fm_mem_analysis.py                      # 先列俱乐部汇总找 uid
    python fm_mem_analysis.py --club <uid>         # 分析指定俱乐部
    python fm_mem_analysis.py --club <uid> --no-open

前置条件：
    - FM24 运行中且已载入存档
    - 位置字段已定位：先运行 fm24_probe.py --posprobe，把偏移填进
      fm24_probe.py 的 P_POSITION（当前仍为 None 时会提示）
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

from fm_memory import FmMemory
from fm_analysis import analyze, OUTPUT
from fm24_probe import collect_roster, read_position, _print_club_summary


def calc_age(year, doy):
    """由出生年/年内第几天算年龄，失败返回 None。"""
    if not year or not (1900 < year < 2100):
        return None
    try:
        bd = datetime.date(year, 1, 1) + datetime.timedelta(days=doy - 1)
        return (datetime.date.today() - bd).days // 365
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
            pos = read_position(mem, p["rec"])
            if pos is None:
                print("!! 位置字段未定位：请先运行 fm24_probe.py --posprobe 定位偏移，"
                      "再把偏移填进 fm24_probe.py 的 P_POSITION。")
                return
            age = calc_age(p["year"], p["doy"])
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
