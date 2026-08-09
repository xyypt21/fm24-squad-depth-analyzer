"""Command-line entry point: read the squad from FM24 memory and analyze it."""

import argparse
import os
import sys

from fm_analysis import OUTPUT, analyze, load_config
from fm_roster import read_squad_from_memory


def main() -> None:
    ap = argparse.ArgumentParser(description="FM24 阵容厚度分析（直接从游戏内存读取）")
    ap.add_argument(
        "--club", type=int, default=None, help="俱乐部 ID（默认取 config.json 的 club_uid）"
    )
    args = ap.parse_args()

    config = load_config()
    club_uid = args.club if args.club is not None else config["club_uid"]

    print("[cli] 读取阵容（内存直读，单次扫描）...")
    name, roster = read_squad_from_memory(club_uid)
    print(f"[cli] 目标俱乐部: {club_uid} {name or ''}")
    print(f"[cli] 读到 {len(roster)} 名球员")
    html = analyze(
        roster,
        min_age=config["min_age"],
        growth_until_age=config["growth_until_age"],
        growth_per_year=config["growth_per_year"],
    )
    if not html:
        sys.exit(1)
    os.startfile(OUTPUT.resolve())


if __name__ == "__main__":
    main()
