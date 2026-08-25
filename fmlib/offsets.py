"""fm_offsets_info.json 偏移表的加载与按游戏版本选择。

数据来源：FM Scouting Tool 参考偏移表（见仓库根目录 fm_offsets_info.json）。
类型标记（tagged type id）约定：记录首字段存的是 vtable RVA | 0x40000000，
例如球队记录头 u32 = 0x45A78848 = 0x40000000 | 0x5A78848（vtb_team）。
"""

import json
import re
from pathlib import Path
from typing import Any, Dict, Optional

OFFSETS_PATH = Path(__file__).parent.parent / "fm_offsets_info.json"

TAG_FLAG = 0x40000000


def _int(value) -> int:
    """把 '0x123' / '0x123'(int) / 数字 解析成 int。"""
    if isinstance(value, int):
        return value
    return int(str(value), 0)


def type_tag(rva: int) -> int:
    """vtable RVA -> 记录头存储的 tagged id（低 u32）。"""
    return (rva & 0x3FFFFFFF) | TAG_FLAG


class Offsets:
    """一个游戏版本的常用偏移（只挑本项目需要的字段，值已转成 int）。"""

    def __init__(self, raw: Dict[str, Any]):
        self.raw = raw
        self.fm_version: str = raw["fm_version"]

        self.game_date_rva = _int(raw["game_date_chain"]["game_date_rva"])

        hm = raw["human_manager_chain"]
        self.mgr_hnp_rva = _int(hm["mgr_hnp_rva"])
        # HNP person 类型标记：FM24 块放在 human_manager_chain.hnp_vtable_rva，
        # 26.x 块只有 vtb_* 三项、无 person 表，此时为 None（检测时跳过强校验）
        vt = raw["vtable_rvas"]
        hnp_rva = hm.get("hnp_vtable_rva") or vt.get("ptr_person_hnp")
        self.hnp_type_id = type_tag(_int(hnp_rva)) if hnp_rva else None
        self.hmgr_start_off = _int(hm["hmgr_start_off"])
        self.hmgr_end_off = _int(hm["hmgr_end_off"])

        self.team_type_id = type_tag(_int(vt["vtb_team"]))
        self.club_type_id = type_tag(_int(vt["vtb_club"]))

        team = raw["team_offsets"]
        self.team_uid_off = _int(team["uid"])
        self.team_type_off = _int(team["team_type"])
        self.team_club_ptr_off = _int(team["club_ptr"])
        self.team_manager_ptr_off = _int(team["manager_ptr"])

        club = raw["club_offsets"]
        self.club_uid_off = _int(club["uid"])
        self.club_name_off = _int(club["name_full"])

        # regen 球员 uid 阈值：高于它的 uid 属于新生球员（用于合法性校验）
        self.regen_uid_threshold = _int(raw.get("regen_uid_threshold", 2002068000))

    def __repr__(self):
        return f"<Offsets {self.fm_version}>"


def load_offsets(path=None, version_key: Optional[str] = None) -> Offsets:
    """加载偏移表。

    version_key: 形如 "24.4" 的主.次版本号；None 时取表里第一个版本。
    匹配不到时抛 ValueError（附上已知版本列表）。
    """
    path = Path(path) if path else OFFSETS_PATH
    data = json.loads(path.read_text(encoding="utf-8"))
    versions = data.get("versions") or []
    if not versions:
        raise ValueError(f"{path} 中没有 versions 数据")
    block = None
    if version_key is None:
        block = versions[0]
    else:
        for cand in versions:
            m = re.match(r"(\d+)\.(\d+)", str(cand.get("fm_version", "")))
            if m and f"{m.group(1)}.{m.group(2)}" == version_key:
                block = cand
                break
    if block is None:
        known = [str(v.get("fm_version")) for v in versions]
        raise ValueError(f"偏移表不含版本 {version_key}。已知版本: {', '.join(known)}")
    return Offsets(block)
