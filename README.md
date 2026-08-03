# FM24 阵容深度分析器

分析 Football Manager 2024 球队阵容深度，为 4-2-3-1 阵型自动选出 EA 最佳与次佳 11 人，并生成深度图与补强建议。

## 功能

- 解析 FM 导出的 RTF 阵容表格（支持中文字段：姓名/年龄/位置/能力/潜力）
- 计算每个球员的 EA（预期能力）：
  - `年龄 < 成长至年龄` → `EA = CA + (成长至年龄 - 年龄) × 每年成长`（不超过 PA）
  - `年龄 ≥ 成长至年龄` → `EA = CA`
  - 成长至年龄（默认 21）与每年成长值（默认 20）均可调整
- 用匈牙利算法分配 EA 最佳 11 人、EA 次佳 11 人
- 生成深度图，每个位置给出补强分（分数越高越需要补强）
- 输出可视化 HTML 报告（球场阵型图 + 深度图）
- 图形界面（tkinter）与命令行两种使用方式

## 环境要求

- Python 3.8+
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
1. 选择或输入 RTF 阵容文件路径（默认读取 `config.json` 中保存的路径）
2. 调整 EA 公式参数（成长至年龄、每年成长）
3. 点击"开始分析"，完成后自动打开 HTML 报告

### 命令行

```bash
python fm_analysis.py
```

默认读取 `C:\Users\xyy\Documents\Sports Interactive\Football Manager 2024\team.rtf`，结果写入 `fm_analysis.html`。

### 作为模块调用

```python
import fm_analysis as fa

roster = fa.read_roster_from_rtf(r"path\to\team.rtf")
html = fa.analyze(roster, growth_until_age=21, growth_per_year=20)
```

## 配置文件

`config.json` 保存 GUI 的默认设置，运行时可自动读写：

```json
{
  "rtf_path": "C:\\Users\\xyy\\Documents\\Sports Interactive\\Football Manager 2024\\team.rtf",
  "growth_until_age": 21,
  "growth_per_year": 20
}
```

- `rtf_path`：阵容文件路径
- `growth_until_age`：EA 成长停止年龄
- `growth_per_year`：EA 每岁成长值

文件缺失或损坏时自动回退到默认值。

## RTF 文件要求

从 FM 内导出阵容视图为 RTF 格式，表格需包含以下中文字段：

| 姓名 | 年龄 | 位置 | 能力 | 潜力 |
| ---- | ---- | ---- | ---- | ---- |

`能力` 列导出两份（当前/潜力），解析器取第二份作为当前能力 CA。

## 项目结构

```
fm_analysis.py       核心逻辑：RTF 解析、EA 计算、阵容分配、HTML 生成
fm_analysis_gui.py   tkinter 图形界面
fm_analysis.html     生成的报告输出（被 gitignore 忽略）
config.json          配置文件
```

## EA 补强分说明

- 每个位置最多 4 分
- 首发该位置球员 EA 低于参考值（首 11 人平均 EA 的 90%）+2
- 次佳 11 人该位置球员弱 +1
- 深度图该位置弱球员比例（1 位小数）+相应比例
