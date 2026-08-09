"""Command-line entry point: analyze the roster from the configured RTF path."""

import os
import sys

from fm_analysis import OUTPUT, analyze
from fm_config import load_config
from fm_roster import read_roster_from_rtf


def main() -> None:
    config = load_config()
    roster = read_roster_from_rtf(config["rtf_path"])
    html = analyze(
        roster,
        growth_until_age=config["growth_until_age"],
        growth_per_year=config["growth_per_year"],
    )
    if not html:
        sys.exit(1)
    os.startfile(OUTPUT.resolve())


if __name__ == "__main__":
    main()
