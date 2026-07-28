"""Render simple LLM-described diagrams as deterministic SVG assets."""

import html
import json
import re
from pathlib import Path


def _svg_vector(diagram: dict) -> str:
    xmin, xmax, ymin, ymax = diagram.get("bounds", [-5, 5, -5, 5])
    width, height, pad = 700, 500, 35

    def point(x, y):
        return (pad + (x - xmin) / (xmax - xmin) * (width - 2 * pad),
                height - pad - (y - ymin) / (ymax - ymin) * (height - 2 * pad))

    def line(x1, y1, x2, y2, attrs=""):
        return f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" {attrs}/>'

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}">',
             '<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="#28a9e0"/></marker></defs>',
             '<rect width="100%" height="100%" fill="white"/>']
    if xmin <= 0 <= xmax:
        x, _ = point(0, 0); parts.append(line(x, pad, x, height-pad, 'stroke="#999" stroke-width="1"'))
    if ymin <= 0 <= ymax:
        _, y = point(0, 0); parts.append(line(pad, y, width-pad, y, 'stroke="#999" stroke-width="1"'))
    for vector in diagram.get("vectors", []):
        start, end = vector.get("start", [0, 0]), vector.get("end", [0, 0])
        x1, y1 = point(*start); x2, y2 = point(*end)
        parts.append(line(x1, y1, x2, y2, 'stroke="#28a9e0" stroke-width="3" marker-end="url(#arrow)"'))
        if vector.get("name"):
            parts.append(f'<text x="{x2+6:.1f}" y="{y2-6:.1f}" font-family="sans-serif" font-size="16" fill="#222">{html.escape(str(vector["name"]))}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def render_diagrams(markdown: str, asset_dir: str | Path, stem: str) -> str:
    """Replace supported diagram-json blocks with relative SVG embeds."""
    asset_dir = Path(asset_dir)
    asset_dir.mkdir(parents=True, exist_ok=True)
    counter = 0

    def replace(match):
        nonlocal counter
        try:
            diagram = json.loads(match.group(1))
            if diagram.get("type") != "vector_diagram":
                return match.group(0)
            counter += 1
            filename = f"{stem}-diagram-{counter:03d}.svg"
            (asset_dir / filename).write_text(_svg_vector(diagram), encoding="utf-8")
            return f"![{diagram.get('caption', 'Diagram')}]({asset_dir.name}/{filename})"
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return match.group(0)

    return re.sub(r"```diagram-json\s*\n(.*?)\n```", replace, markdown, flags=re.DOTALL)
