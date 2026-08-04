"""
FM2024 4-2-3-1 阵容深度分析 - HTML 报告渲染。

将分析结果渲染为可视化 HTML 报告（球场阵型图 + 深度图）。
"""

from datetime import datetime
from pathlib import Path

from fm_analysis import DEPTH_SLOT_MAP, DEPTH_SLOTS


def compute_average(xi, sort_key):
    """计算一套首发阵容的平均分。"""
    return int(sum(p[sort_key] for _, p in xi) / len(xi)) if xi else 0


def is_weak(player_ea, ref):
    return player_ea < ref * 0.9


def render_player_slot(slot_name, player, sort_key, reference_value):
    """渲染单个球员槽位的 HTML。"""
    weak = " slot-weak" if is_weak(player[sort_key], reference_value) else ""
    return (
        f"<div class='slot{weak}'>"
        f"<div class='slot-label'>{slot_name}</div>"
        f"<div class='p-name'>{player['name']}</div>"
        f"<div class='p-stat'>{player['age']}岁 · CA{player['ca']} · EA{player['ea']:.0f}</div>"
        f"</div>"
    )


def render_pitch(xi, sort_key, reference_value):
    """
    渲染 11 人球场阵型图。
    行排列：STC / AML,AMC,AMR / DMC,DMC / DL,DC,DC,DR / GK
    """

    def slot_html(index):
        if index >= len(xi):
            return "<div class='slot' style='visibility:hidden;'></div>"
        slot_name, player = xi[index]
        return render_player_slot(slot_name, player, sort_key, reference_value)

    row_indices = [(10,), (7, 8, 9), (5, 6), (1, 2, 3, 4), (0,)]
    rows = [
        "<div class='f-row'>" + "".join(slot_html(i) for i in indices) + "</div>"
        for indices in row_indices
    ]
    return "\n".join(rows)


def render_pitch_card(xi, sort_key, reference=None):
    """渲染一张完整的球场卡片。"""
    average = compute_average(xi, sort_key)
    if not xi:
        return (
            "<div class='pitch' style='display:flex;align-items:center;"
            "justify-content:center;color:rgba(255,255,255,0.5);font-size:14px;'>"
            "球员不足</div>"
        )
    return f"<div class='pitch'>{render_pitch(xi, sort_key, reference or average)}</div>"


def compute_scores(ea_first, ea_second, depth_data, ref):
    """
    补强分：分数越高越需要补强，每个位置最多 4 分。
    首发该位置弱 +2，次佳该位置弱 +1，深度图中弱球员比例（保留 1 位小数）。
    """
    scores = {}
    for slot in DEPTH_SLOTS:
        # ea_first/ea_second 是按 SLOTS 顺序的列表（下标即槽位位置），
        # indices 是该位置在列表中的下标，用来检查首发/次佳该位置是否有弱球员
        indices = DEPTH_SLOT_MAP[slot]
        first_red = (
            2
            if any(i < len(ea_first) and is_weak(ea_first[i][1]["ea"], ref) for i in indices)
            else 0
        )
        second_red = (
            1
            if any(i < len(ea_second) and is_weak(ea_second[i][1]["ea"], ref) for i in indices)
            else 0
        )
        depth_players = depth_data.get(slot, [])
        red_ratio = (
            round(sum(1 for p in depth_players if is_weak(p["ea"], ref)) / len(depth_players), 1)
            if depth_players
            else 0
        )
        scores[slot] = first_red + second_red + red_ratio
    return scores


def render_depth_chart(depth_data, reference_value, scores=None):
    """渲染深度图（2 列纵向分区布局，按补强分降序排列）。"""
    slots_sorted = sorted(DEPTH_SLOTS, key=lambda s: -(scores.get(s, 0) if scores else 0))

    mid = (len(slots_sorted) + 1) // 2
    columns = [slots_sorted[:mid], slots_sorted[mid:]]

    def render_column(col_data):
        parts = []
        for slot in col_data:
            players = depth_data.get(slot, [])
            avg = int(sum(p["ea"] for p in players) / len(players)) if players else 0
            items = ""
            for p in players:
                weak = " dc-weak" if is_weak(p["ea"], reference_value) else ""
                items += (
                    f"<div class='dc-item{weak}'>"
                    f"<span class='dc-name'>{p['name']}</span>"
                    f"<span class='dc-age'>{p['age']}岁</span>"
                    f"<span class='dc-ca'>CA{p['ca']}</span>"
                    f"<span class='dc-ea'>EA{p['ea']:.0f}</span>"
                    f"</div>"
                )
            score = scores.get(slot, 0) if scores else 0
            score_cls = " sc-low" if score == 0 else (" sc-mid" if score < 2 else " sc-high")
            parts.append(
                f"<div class='dc-section'>"
                f"<div class='dc-header'>{slot}"
                f"<span class='dc-score{score_cls}'>{score}</span> · 均{avg}</div>"
                f"{items}"
                f"</div>"
            )
        return "<div class='dc-col'>" + "\n".join(parts) + "</div>"

    return render_column(columns[0]) + render_column(columns[1])


def generate_full_html(ea_first, ea_second, depth_chart, dc_unique_count):
    ref = compute_average(ea_first, "ea")
    scores = compute_scores(ea_first, ea_second, depth_chart, ref)
    template_dir = Path(__file__).parent / "templates"
    template = (template_dir / "report.html").read_text(encoding="utf-8")
    css = (template_dir / "style.css").read_text(encoding="utf-8")
    return (
        template.replace("{{CSS_STYLE}}", css)
        .replace("{{PITCH_BEST}}", render_pitch_card(ea_first, "ea"))
        .replace("{{AVG_BEST}}", str(compute_average(ea_first, "ea")))
        .replace("{{PITCH_SECOND}}", render_pitch_card(ea_second, "ea", ref))
        .replace("{{AVG_SECOND}}", str(compute_average(ea_second, "ea")))
        .replace("{{DEPTH_CHART}}", render_depth_chart(depth_chart, ref, scores))
        .replace("{{DC_COUNT}}", str(dc_unique_count))
        .replace("{{GENERATED_AT}}", datetime.now().strftime("%Y-%m-%d %H:%M"))
    )
