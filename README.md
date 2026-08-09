# FM24 Squad Depth Analyzer

Analyzes the squad depth of your Football Manager 2024 team, picks the best and second-best starting XIs for a 4-2-3-1 formation by CA and by EA, and generates a visual HTML report.

## Features

- Reads the squad directly from the running FM24 game memory (no RTF export needed)
  - Filters by club UID (configurable), keeping only players whose contract and current club both match (excludes loanees in and out)
  - Reads game date from memory and computes precise ages
  - Automatically translates selected player names to Chinese via Google Translate (through a local proxy)
- Computes Expected Ability (EA) for every player:
  - `age < growth_until_age` → `EA = CA + (growth_until_age - age) × growth_per_year` (capped at PA)
  - `age >= growth_until_age` → `EA = CA`
  - `growth_until_age` (default 21) and `growth_per_year` (default 20) are configurable
- `min_age` filter (default 17) drops players too young to be considered
- Picks the best and second-best starting XIs using the Hungarian algorithm, for both CA and EA
- Outputs a visual HTML report with four pitch diagrams
- Both a GUI (tkinter) and a command-line interface

## Requirements

- Python 3.8+
- Windows (memory reading uses the Windows API)
- Dependencies: `numpy`, `scipy`

```bash
pip install numpy scipy
```

## Usage

### GUI

```bash
python fm_analysis_gui.py
```

In the window:
1. Enter the club ID (defaults to the value saved in `config.json`)
2. Adjust the EA formula parameters (minimum age, growth until age, growth per year)
3. Optionally click "查询名字" to look up the club name from memory
4. Click "开始分析" (Start analysis); the HTML report opens automatically when done

### Command line

```bash
python fm_cli.py
```

Reads the club ID from `config.json` (default `club_uid` 920) and writes the result to `fm_analysis.html`, then opens it in the browser.

To analyze a specific club:

```bash
python fm_cli.py --club <uid>
```

To find your club's UID, use the probe tool:

```bash
python probe/fm24_probe.py --roster
```

### As a module

```python
from fm_roster import read_squad_from_memory
from fm_analysis import analyze

name, roster = read_squad_from_memory(club_uid=920)
html = analyze(roster, min_age=17, growth_until_age=21, growth_per_year=20)
```

## Configuration

`config.json` stores the GUI/CLI defaults and is read/written automatically:

```json
{
  "club_uid": 920,
  "min_age": 17,
  "growth_until_age": 21,
  "growth_per_year": 20
}
```

- `club_uid`: the FM club UID to analyze
- `min_age`: only players aged >= this are considered (default 17)
- `growth_until_age`: age at which EA growth stops (default 21)
- `growth_per_year`: EA growth per year of age (default 20)

If the file is missing or corrupt, defaults are used automatically.

## Project Structure

```
fm_analysis_gui.py   tkinter GUI
fm_cli.py            Command-line entry point
fm_config.py         Config file loading/saving
fm_positions.py      Formation slots and position parsing
fm_roster.py         Memory reading: squad and club name
fm_analysis.py       Core logic: EA calculation, lineup selection, name translation
fm_report.py         HTML report rendering (reads templates from templates/)
fm_names.py          Online player-name translation (Google, via local proxy)
fm_analysis.html     Generated report output (ignored by git)
config.json          Configuration file
templates/style.css   Report stylesheet
templates/report.html Report HTML skeleton (placeholder-based)
probe/fm_memory.py   Generic read-only memory reader (ctypes, Windows API)
probe/fm24_probe.py  FM24-specific offsets, scanning and squad extraction
```

## Name Translation

Names that make it into the final XIs are translated to Chinese in one concurrent batch via Google Translate (`deep-translator`), going through a local proxy (default `http://127.0.0.1:7897`, overridable with the `FM_PROXY` environment variable). Failures keep the original name; nothing is written to disk.

## Notes

- The game must be running with a save loaded. If the game runs as administrator, run this tool as administrator too.
- Offsets in `probe/fm24_probe.py` are locked to the current FM24 build (Epic version). If a game update breaks them, re-observe the offsets (see the comments in the file).
