# FM24 Squad Depth Analyzer

Analyzes the squad depth of your Football Manager 2024 team, picks the best and second-best starting XIs for a 4-2-3-1 formation, and generates a depth chart with reinforcement suggestions.

## Features

- Parses RTF roster tables exported from FM (supports Chinese headers: 姓名/年龄/位置/能力/潜力)
- Computes Expected Ability (EA) for every player:
  - `age < growth_until_age` → `EA = CA + (growth_until_age - age) × growth_per_year` (capped at PA)
  - `age >= growth_until_age` → `EA = CA`
  - `growth_until_age` (default 21) and `growth_per_year` (default 20) are configurable
- Picks the best and second-best starting XIs using the Hungarian algorithm
- Generates a depth chart with a reinforcement score per position (higher = needs strengthening)
- Outputs a visual HTML report (pitch diagrams + depth chart)
- Both a GUI (tkinter) and a command-line interface
- `rtf_path` supports `~` as the user home directory for easy portability

## Requirements

- Python 3.8+
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
1. Pick or enter the RTF roster file path (defaults to the path saved in `config.json`)
2. Adjust the EA formula parameters (growth until age, growth per year)
3. Click "开始分析" (Start analysis); the HTML report opens automatically when done

### Command line

```bash
python fm_analysis.py
```

Reads the path saved in `config.json` (default `~\Documents\Sports Interactive\Football Manager 2024\team.rtf`) and writes the result to `fm_analysis.html`.

### As a module

```python
import fm_analysis as fa

roster = fa.read_roster_from_rtf(r"path\to\team.rtf")
html = fa.analyze(roster, growth_until_age=21, growth_per_year=20)
```

## Configuration

`config.json` stores the GUI defaults and is read/written automatically:

```json
{
  "rtf_path": "~\\Documents\\Sports Interactive\\Football Manager 2024\\team.rtf",
  "growth_until_age": 21,
  "growth_per_year": 20
}
```

- `rtf_path`: roster file path (`~` expands to the user home directory)
- `growth_until_age`: age at which EA growth stops
- `growth_per_year`: EA growth per year of age

If the file is missing or corrupt, defaults are used automatically.

## RTF File Requirements

Export the squad view from FM as RTF. The table must include these Chinese columns:

| 姓名 | 年龄 | 位置 | 能力 | 潜力 |
| ---- | ---- | ---- | ---- | ---- |

The `能力` (Ability) column is exported twice (current/potential); the parser takes the second one as the current Ability (CA).

## Project Structure

```
fm_analysis.py        Core logic: RTF parsing, EA calculation, lineup selection
fm_report.py          HTML report rendering (reads templates from templates/)
fm_analysis_gui.py    tkinter GUI
fm_analysis.html      Generated report output (ignored by git)
config.json           Configuration file
templates/style.css   Report stylesheet
templates/report.html Report HTML skeleton (placeholder-based)
```

## Reinforcement Score

- Max 4 points per position
- A weak starter in that position: +2
- A weak backup (second XI) in that position: +1
- Ratio of weak players in the depth chart for that position (1 decimal place): +ratio
