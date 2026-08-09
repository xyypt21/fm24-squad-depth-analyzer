"""Configuration file loading and saving."""

import json
from pathlib import Path
from typing import Any, Dict, Optional

CONFIG_PATH = Path(__file__).parent / "config.json"
DEFAULT_CONFIG: Dict[str, Any] = {
    "club_uid": 920,
    "min_age": 17,
    "growth_until_age": 21,
    "growth_per_year": 20,
}


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    """Read the config file, falling back to defaults if missing or corrupt."""
    path = Path(path) if path else CONFIG_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return dict(DEFAULT_CONFIG)
    merged = {**DEFAULT_CONFIG, **data}
    return {
        "club_uid": int(merged["club_uid"]),
        "min_age": int(merged["min_age"]),
        "growth_until_age": int(merged["growth_until_age"]),
        "growth_per_year": int(merged["growth_per_year"]),
    }


def save_config(config: Dict[str, Any], path: Optional[Path] = None) -> None:
    """Write the config back to the file."""
    path = Path(path) if path else CONFIG_PATH
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
