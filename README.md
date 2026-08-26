# FM24 Club ID Detector (tmp branch)

Reads the running FM24 process memory (read-only) and auto-detects the club
managed by the human player, showing its UID and name in a small GUI.

## Usage

```bash
pythonw fm_club_gui.pyw
```

1. Start FM24 and load a save.
2. Click "连接游戏并检测" (Connect & detect).
3. Pick a candidate club if several are listed; the UID fills into the box.
4. "保存为默认俱乐部 ID" writes it back to `config.json`.

Diagnostics (if detection fails or after a game update):

```bash
python fm_probe_user.py --club 920
```

## Layout

```
fm_club_gui.pyw       GUI entry
fmlib/memory.py       read-only cross-process memory primitives (ctypes)
fmlib/offsets.py      fm_offsets_info.json loader, version selection
fmlib/session.py      game session: attach, exe version, in-game date
fmlib/clubs.py        CLUB record readers (uid, name)
fmlib/user_club.py    detection: human-manager vector ∩ team manager pointers
fm_probe_user.py      diagnostic script
fm_offsets_info.json  offset reference table (FM Scouting Tool data)
config.json           persisted settings (club_uid, ...)
```

Detection principle: `[exe+mgr_hnp_rva]` → vector of the user-controlled
manager objects; player-record signature scan enumerates all teams; each team's
`manager_ptr` (+0x80) that equals (or references) a vector entry marks the
user's team → parent club UID + name.
