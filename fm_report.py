"""
FM2024 4-2-3-1 squad depth analysis - HTML report rendering.

Renders the analysis results as a visual HTML report (pitch diagrams + depth chart).
"""

from pathlib import Path

def compute_average(xi, sort_key):
    """Compute the average score of a starting XI."""
    return int(sum(p[sort_key] for _, p in xi) / len(xi)) if xi else 0


def is_weak(player_ea, ref):
    return player_ea < ref * 0.9


def render_player_slot(slot_name, player, sort_key, reference_value):
    """Render the HTML for a single player slot."""
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
    Render the 11-man pitch diagram.
    Row layout: STC / AML,AMC,AMR / DMC,DMC / DL,DC,DC,DR / GK
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
    """Render a full pitch card."""
    average = compute_average(xi, sort_key)
    if not xi:
        return (
            "<div class='pitch' style='display:flex;align-items:center;"
            "justify-content:center;color:rgba(255,255,255,0.5);font-size:14px;'>"
            "球员不足</div>"
        )
    return f"<div class='pitch'>{render_pitch(xi, sort_key, reference or average)}</div>"


def generate_full_html(ca_first, ca_second, ea_first, ea_second):
    template_dir = Path(__file__).parent / "templates"
    template = (template_dir / "report.html").read_text(encoding="utf-8")
    css = (template_dir / "style.css").read_text(encoding="utf-8")
    return (
        template.replace("{{CSS_STYLE}}", css)
        .replace("{{PITCH_CA_BEST}}", render_pitch_card(ca_first, "ca"))
        .replace("{{PITCH_CA_SECOND}}", render_pitch_card(ca_second, "ca"))
        .replace("{{PITCH_EA_BEST}}", render_pitch_card(ea_first, "ea"))
        .replace("{{PITCH_EA_SECOND}}", render_pitch_card(ea_second, "ea"))
        .replace("{{AVG_CA_BEST}}", str(compute_average(ca_first, "ca")))
        .replace("{{AVG_CA_SECOND}}", str(compute_average(ca_second, "ca")))
        .replace("{{AVG_EA_BEST}}", str(compute_average(ea_first, "ea")))
        .replace("{{AVG_EA_SECOND}}", str(compute_average(ea_second, "ea")))
    )
