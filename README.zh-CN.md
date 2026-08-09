# FM24 阵容深度分析器

分析 Football Manager 2024 球队的阵容深度，为 4-2-3-1 阵型按 CA 和 EA 分别选出最佳与次佳 11 人，并生成可视化 HTML 报告。

## 功能

- 直接从运行中的 FM24 游戏内存读取阵容（无需导出 RTF）
  - 按俱乐部 UID 过滤（可配置），仅保留合同与当前俱乐部都匹配的球员（排除外租与租入）
  - 从内存读取游戏日期，精确计算球员年龄
  - 自动把入选阵容的球员英文名翻译成中文（Google 翻译，经本地代理）
- 计算每个球员的 EA（预期能力）：
  - `年龄 < 成长至年龄` → `EA = CA + (成长至年龄 - 年龄) × 每年成长`（不超过 PA）
  - `年龄 ≥ 成长至年龄` → `EA = CA`
  - 成长至年龄（默认 21）与每年成长值（默认 20）均可调整
- `min_age` 最小年龄过滤（默认 17），剔除过年轻球员
- 用匈牙利算法分配 CA / EA 的最佳 11 人、次佳 11 人
- 输出可视化 HTML 报告（四个球场阵型图）
- 图形界面（tkinter）与命令行两种使用方式

## 环境要求

- Python 3.8+
- Windows（内存读取依赖 Windows API）
- 依赖：`numpy`、`scipy`

```bash
pip install numpy scipy
```

## 使用方法

### 图形界面

```bash
python fm_analysis_gui.py
```

在界面中：
1. 输入俱乐部 ID（默认读取 `config.json` 中保存的值）
2. 调整 EA 公式参数（最小年龄、成长至年龄、每年成长）
3. 可点击"查询名字"从内存反查俱乐部名
4. 点击"开始分析"，完成后自动打开 HTML 报告

### 命令行

```bash
python fm_cli.py
```

从 `config.json` 读取俱乐部 ID（默认 `club_uid` 920），结果写入 `fm_analysis.html` 并自动打开。

指定俱乐部：

```bash
python fm_cli.py --club <uid>
```

查找自己俱乐部的 UID：

```bash
python probe/fm24_probe.py --roster
```

### 作为模块调用

```python
from fm_roster import read_squad_from_memory
from fm_analysis import analyze

name, roster = read_squad_from_memory(club_uid=920)
html = analyze(roster, min_age=17, growth_until_age=21, growth_per_year=20)
```

## 配置文件

`config.json` 保存 GUI/CLI 的默认设置，运行时可自动读写：

```json
{
  "club_uid": 920,
  "min_age": 17,
  "growth_until_age": 21,
  "growth_per_year": 20
}
```

- `club_uid`：要分析的 FM 俱乐部 UID
- `min_age`：仅考虑年龄不小于该值的球员（默认 17）
- `growth_until_age`：EA 成长停止年龄（默认 21）
- `growth_per_year`：EA 每岁成长值（默认 20）

文件缺失或损坏时自动回退到默认值。

## 项目结构

```
fm_analysis_gui.py   tkinter 图形界面
fm_cli.py            命令行入口
fm_config.py         配置文件读写
fm_positions.py      阵型槽位与位置解析
fm_roster.py         内存读取：阵容与俱乐部名
fm_analysis.py       核心逻辑：EA 计算、阵容分配、球员名翻译
fm_report.py         HTML 报告渲染（从 templates/ 读取模板）
fm_names.py          球员名在线翻译（Google 翻译，经本地代理）
fm_analysis.html     生成的报告输出（被 gitignore 忽略）
config.json          配置文件
templates/style.css      报告样式表
templates/report.html    报告 HTML 骨架（占位符替换）
probe/fm_memory.py   通用只读内存读取层（ctypes，Windows API）
probe/fm24_probe.py  FM24 专属偏移、内存扫描与阵容提取
```

## 球员名翻译

入选最终阵容的球员名字会用 Google 翻译（`deep-translator`）并发批量翻译成中文，经本地代理（默认 `http://127.0.0.1:7897`，可用环境变量 `FM_PROXY` 覆盖）。翻译失败保留原名，不写任何文件。

## 注意事项

- 游戏需已启动并载入存档。若游戏以管理员权限运行，本工具也需以管理员权限运行。
- `probe/fm24_probe.py` 中的偏移锁定当前 FM24 构建（Epic 版）。游戏更新导致失效时，需按文件内注释重新观察偏移。
