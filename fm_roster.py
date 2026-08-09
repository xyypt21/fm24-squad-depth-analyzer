"""Parse the RTF roster table exported by Football Manager."""

import warnings
from pathlib import Path
from typing import Dict, List, Optional

from fm_config import load_config
from fm_positions import parse_position_tokens


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
