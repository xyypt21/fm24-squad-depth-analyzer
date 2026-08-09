"""Formation slots and FM position parsing."""

import re
from typing import Dict, List, Set

# 4-2-3-1 starting XI, ordered top-to-bottom as the pitch is drawn.
SLOTS: List[str] = ["GK", "DL", "DC", "DC", "DR", "DMC", "DMC", "AML", "AMC", "AMR", "STC"]

# Depth-chart slots: SLOTS is the 11-man formation (with repeated DC/DMC), whereas
# the reinforcement scores and depth chart are grouped by *position*, so this map
# translates a position into its indices in the 11-man lists. E.g. DEPTH_SLOT_MAP["DC"] == [2, 3]
# means the two centre-backs sit at indices 2 and 3. DEPTH_SLOTS lists positions in
# first-seen order.
DEPTH_SLOT_MAP: Dict[str, List[int]] = {}
for i, slot in enumerate(SLOTS):
    DEPTH_SLOT_MAP.setdefault(slot, []).append(i)
DEPTH_SLOTS: List[str] = list(DEPTH_SLOT_MAP)

# Valid FM role names and side letters.
VALID_ROLES: Set[str] = {"GK", "D", "WB", "DM", "M", "AM", "ST"}
VALID_SIDES: Set[str] = {"C", "L", "R"}

# Full valid position token set (role + side combinations).
VALID_POSITION_TOKENS: Set[str] = {
    "GK",
    "WBL",
    "WBR",
    "DMC",
    "STC",
    *(r + s for r in ("D", "M", "AM") for s in VALID_SIDES),
}


def parse_position_tokens(position_text: str) -> List[str]:
    """
    Split an FM position string into "role+side" tokens, keeping only valid ones.
    Roles without parentheses are treated as centre (+C), e.g. "DM" -> ["DMC"].

    Examples:
      "M (L), AM (RLC)"     -> ["ML", "AMR", "AML", "AMC"]
      "D/WB (R)"            -> ["DR", "WBR"]
      "ST (C)"              -> ["STC"]
      "DM"                  -> ["DMC"]
      "GK"                  -> ["GK"]
    """
    tokens: List[str] = []
    # Grab each "role(sides)" chunk in one regex pass, skipping commas/spaces.
    # E.g. "M (L), AM (RLC)" yields (M, L) then (AM, RLC)
    for roles_text, sides_text in re.findall(
        r"([A-Z/]+)\s*(?:\(([A-Z]+)\))?", position_text.upper()
    ):
        # Handle compound roles like D/WB, taking D and WB one by one.
        for role in roles_text.split("/"):
            if role not in VALID_ROLES:
                continue
            if sides_text:
                # Sides in parentheses: validate them, then build one token each.
                if all(s in VALID_SIDES for s in sides_text):
                    tokens.extend(role + s for s in sides_text)
            else:
                # No parentheses: GK stays GK, other roles treated as centre,
                # e.g. DM -> DMC.
                tokens.append("GK" if role == "GK" else role + "C")
    # Safety filter: drop any token not in the valid position table.
    return [t for t in tokens if t in VALID_POSITION_TOKENS]


def player_can_play(position_text: str, slot_name: str) -> bool:
    """Check whether a player can fill a formation slot."""
    return slot_name in parse_position_tokens(position_text)
