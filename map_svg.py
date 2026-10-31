"""Harta terminalului ca SVG, desenata la scara din datele din airport_core:
  1. cladirile: contururile reale (OpenStreetMap), trecerile prin pereti, zona fara acces, securitatea
  2. traseul: polyline prin nodurile din `route` (pe drumul real, care se curbeaza)
  3. locurile, `highlight` (portocaliu), "Esti aici" (rosu)

Pe locuri se poate apasa: fiecare e un <g class="acm-place" data-node="<id>">; live_map.py
asculta click-urile si trimite id-ul la Python, care deseneaza traseul.

Contract:
    render_map(position, route, highlight) -> str (SVG)

Id-urile necunoscute sunt ignorate (fara KeyError). Toate textele trec prin html.escape.
Previzualizare fara Streamlit:  python map_svg.py   -> scrie un .html si afiseaza calea
"""

from html import escape

from airport_core import (CLICKABLE, DRAW_S_MIN, EDGES, NODES, OUTLINE_NEW_TERMINAL, OUTLINE_OLD_HALL, OUTLINE_OTHER,
                          OUTLINE_TOILET_ANNEX, SECURITY_ZONE_S, VIEWBOX, WEST_NO_ACCESS_S, d_to_y, s_to_x)

ICONS = {
    "entrance": "\U0001F6AA",       # usa
    "checkin": "\U0001F9F3",        # bagaj
    "security": "\U0001F6C2",       # control
    "food": "\U0001F37D",           # farfurie
    "toilet": "\U0001F6BB",         # toaleta
    "gate": "✈",               # avion
    "info": "ℹ️",         # informatii
}
NODE_ICONS = {"cafe": "☕", "kfc": "\U0001F357"}   # cafea, pulpa de pui

# Nume scurte pe harta, ca textul sa ramana lizibil pe un ecran de telefon.
SHORT_NAMES = {
    "entrance": "Intrare",
    "toilet": "Toaleta",
    "checkin": "Check-in",
    "info": "Informatii",
    "cafe": "Cafenea",
    "kfc": "KFC",
    "gate_1": "Poarta 1",
}

COLORS = {
    "route": "#2563eb",
    "highlight": "#f59e0b",
    "position": "#dc2626",
    "corridor": "#cbd5e1",
    "wall": "#475569",
    "landside": "#e0f2fe",
    "airside": "#dcfce7",
    "security": "#fecaca",
    "no_access": "#cbd5e1",
    "other_building": "#e5e7eb",
    "node": "#ffffff",
    "node_stroke": "#475569",
    "text": "#0f172a",
    "muted": "#64748b",
    "ground": "#f8fafc",
}

# Clasele au prefixul "acm-" ca sa nu se ciocneasca cu CSS-ul paginii.
# Daca <style> e eliminat, harta ramane corecta, doar fara animatie si fara efect la hover.
_STYLE = """
<style>
  .acm-route { stroke-dasharray: 14 10; animation: acm-flow 1s linear infinite; }
  .acm-pulse { animation: acm-pulse 1.6s ease-out infinite; transform-box: fill-box; transform-origin: center; }
  .acm-place { cursor: pointer; }
  .acm-place:focus { outline: none; }
  .acm-place:hover .acm-dot, .acm-place:focus-visible .acm-dot { stroke: #2563eb; stroke-width: 4; }
  @keyframes acm-flow { to { stroke-dashoffset: -24; } }
  @keyframes acm-pulse { from { transform: scale(1); opacity: .6; } to { transform: scale(2.4); opacity: 0; } }
  @media (prefers-reduced-motion: reduce) { .acm-route, .acm-pulse { animation: none; } }
</style>
"""


def _xy(node_id):
    n = NODES[node_id]
    return n["x"], n["y"]


def _pts(outline):
    return " ".join(f"{s_to_x(s)},{d_to_y(d)}" for s, d in outline)


def _rect(s0, s1, d_top, d_bottom):
    x0, x1, y0, y1 = s_to_x(s0), s_to_x(s1), d_to_y(d_top), d_to_y(d_bottom)
    return f'x="{x0}" y="{y0}" width="{round(x1 - x0, 1)}" height="{round(y1 - y0, 1)}"'


def _gap(p0, p1, color):
    """O trecere printr-un perete: o linie de culoarea podelei peste conturul peretelui."""
    (s0, d0), (s1, d1) = p0, p1
    return (f'<line x1="{s_to_x(s0)}" y1="{d_to_y(d0)}" x2="{s_to_x(s1)}" y2="{d_to_y(d1)}" '
            f'stroke="{color}" stroke-width="7" stroke-linecap="butt"/>')


def _caption(s, d, text, size=18):
    return (f'<text x="{s_to_x(s)}" y="{d_to_y(d)}" font-size="{size}" font-weight="700" letter-spacing="1.5" '
            f'text-anchor="middle" fill="{COLORS["muted"]}">{escape(text)}</text>')


def _building():
    c = COLORS
    wall = f'stroke="{c["wall"]}" stroke-linejoin="round"'
    out = [
        f'<defs><clipPath id="acm-clip-old"><polygon points="{_pts(OUTLINE_OLD_HALL)}"/></clipPath>'
        f'<clipPath id="acm-clip-new"><polygon points="{_pts(OUTLINE_NEW_TERMINAL)}"/></clipPath></defs>',
        # Cladirile, din contururile reale
        f'<polygon points="{_pts(OUTLINE_OTHER)}" fill="{c["other_building"]}" stroke="#94a3b8" stroke-width="1.5"/>',
        f'<polygon points="{_pts(OUTLINE_NEW_TERMINAL)}" fill="{c["airside"]}" {wall} stroke-width="3"/>',
        f'<polygon points="{_pts(OUTLINE_OLD_HALL)}" fill="{c["landside"]}" {wall} stroke-width="3"/>',
        f'<polygon points="{_pts(OUTLINE_TOILET_ANNEX)}" fill="{c["landside"]}" {wall} stroke-width="2"/>',
        # Partea din stanga a holului, fara acces pentru pasageri
        f'<rect {_rect(DRAW_S_MIN - 1, WEST_NO_ACCESS_S, 16, -6)} fill="{c["no_access"]}" opacity="0.7" '
        f'clip-path="url(#acm-clip-old)"/>',
        f'<text x="{s_to_x(WEST_NO_ACCESS_S - 2)}" y="{d_to_y(5)}" font-size="15" text-anchor="middle" '
        f'fill="{c["muted"]}" transform="rotate(-90 {s_to_x(WEST_NO_ACCESS_S - 2)} {d_to_y(5)})">fara acces</text>',
        # Securitatea: banda rosiatica, in interiorul cladirii alipite
        f'<rect {_rect(SECURITY_ZONE_S[0], SECURITY_ZONE_S[1], 20, -10)} fill="{c["security"]}" '
        f'clip-path="url(#acm-clip-new)"/>',
        # Trecerile prin pereti: usa de la intrare, spre toaleta, spre securitate
        _gap((-2.2, -5.13), (2.2, -5.05), c["ground"]),
        _gap((-5.5, 15.9), (-2.5, 15.9), c["landside"]),
        _gap((-5.5, 14.2), (-2.5, 14.2), c["landside"]),
        _gap((39.0, 9.4), (41.8, 6.6), c["landside"]),
        _gap((39.9, 9.4), (42.9, 6.6), c["landside"]),
        # Numele zonelor si repere exterioare
        _caption(20, -2.5, "HOL PUBLIC"),
        _caption(49.3, 21.5, "SECURITATE"),
        _caption(66, 15, "ZONA PLECARI"),
        f'<text x="{s_to_x(66)}" y="{d_to_y(23)}" font-size="17" text-anchor="middle" fill="{c["muted"]}">'
        f'↑ spre avioane</text>',
        f'<text x="{s_to_x(0)}" y="{round(d_to_y(-5) + 72, 1)}" font-size="17" text-anchor="middle" '
        f'fill="{c["muted"]}">↓ parcare, autobuz</text>',
        # Titlul, deasupra cladirilor
        f'<text x="24" y="40" font-size="26" font-weight="700" fill="{c["text"]}" paint-order="stroke" '
        f'stroke="{c["ground"]}" stroke-width="8" stroke-linejoin="round">Aeroportul Craiova - Terminal</text>',
    ]
    # Pe unde se poate merge: drumul principal (se curbeaza) si drumurile spre locurile din lateral.
    # Securitatea (sens unic) e desenata punctat.
    for a, b, _meters, both_ways in EDGES:
        (x1, y1), (x2, y2) = _xy(a), _xy(b)
        dash = '' if both_ways else ' stroke-dasharray="4 6"'
        out.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{c["corridor"]}" '
                   f'stroke-width="9" stroke-linecap="round"{dash}/>')
    return out


def _route(route):
    route = [n for n in route if n in NODES]
    if len(route) < 2:
        return []
    points = " ".join(f"{x},{y}" for x, y in map(_xy, route))
    common = 'fill="none" stroke-linecap="round" stroke-linejoin="round"'
    return [
        # contur alb sub linie, ca traseul sa se vada peste orice fundal
        f'<polyline points="{points}" {common} stroke="#ffffff" stroke-width="12"/>',
        f'<polyline class="acm-route" points="{points}" {common} stroke="{COLORS["route"]}" stroke-width="7"/>',
    ]


def _places(highlight):
    out = []
    for node_id, n in NODES.items():
        if n["kind"] == "waypoint":
            continue
        x, y = n["x"], n["y"]
        is_hl = node_id in highlight
        clickable = node_id in CLICKABLE
        name = SHORT_NAMES.get(node_id, n["name"])
        if clickable:
            out.append(f'<g class="acm-place" data-node="{escape(node_id)}" role="button" tabindex="0" '
                       f'aria-label="Traseu spre {escape(n["name"])}">')
            # zona de atingere mare (pentru deget), invizibila
            out.append(f'<circle cx="{x}" cy="{y}" r="34" fill="#ffffff" fill-opacity="0.001"/>')
        if is_hl:
            out.append(f'<circle cx="{x}" cy="{y}" r="27" fill="{COLORS["highlight"]}" opacity="0.9"/>')
        out.append(
            f'<circle class="acm-dot" cx="{x}" cy="{y}" r="19" fill="{COLORS["node"]}" '
            f'stroke="{COLORS["highlight"] if is_hl else COLORS["node_stroke"]}" stroke-width="2"/>'
        )
        icon = NODE_ICONS.get(node_id) or ICONS.get(n["kind"], "")
        if icon:
            out.append(f'<text x="{x}" y="{y + 7}" font-size="20" text-anchor="middle">{icon}</text>')
        if n["kind"] != "security":   # cele doua noduri sunt prea apropiate; zona are deja nume
            if n["kind"] == "entrance" or n["side"] == 0:
                lx, ly, anchor = x, y + 44, "middle"      # sub loc
            else:
                lx, ly, anchor = x + 27, y + 7, "start"   # la dreapta locului
            out.append(
                f'<text x="{lx}" y="{ly}" font-size="21" text-anchor="{anchor}" '
                f'font-weight="{700 if is_hl else 500}" fill="{COLORS["text"]}" '
                f'paint-order="stroke" stroke="#ffffff" stroke-width="5">{escape(name)}</text>'
            )
        if clickable:
            out.append('</g>')
    return out


def _position(position):
    if position not in NODES:
        return []
    x, y = _xy(position)
    c = COLORS["position"]
    # Grupul are clasa "acm-static-pos": cand exista localizare live, JS-ul il ascunde
    # si deseneaza punctul live in <g class="acm-live">.
    return [
        '<g class="acm-static-pos">',
        f'<circle class="acm-pulse" cx="{x}" cy="{y}" r="14" fill="{c}" opacity="0.5"/>',
        f'<circle cx="{x}" cy="{y}" r="14" fill="{c}" stroke="#ffffff" stroke-width="3"/>',
        f'<text x="{x}" y="{y - 26}" font-size="22" font-weight="700" text-anchor="middle" '
        f'fill="{c}" paint-order="stroke" stroke="#ffffff" stroke-width="5">Esti aici</text>',
        '</g>',
    ]


def _legend():
    w, h = VIEWBOX
    y = h - 10
    t = f'font-size="18" fill="{COLORS["text"]}"'
    return [
        f'<line x1="24" y1="{y - 6}" x2="56" y2="{y - 6}" stroke="{COLORS["route"]}" stroke-width="7" stroke-linecap="round"/>',
        f'<text x="64" y="{y}" {t}>Traseu</text>',
        f'<circle cx="146" cy="{y - 6}" r="8" fill="{COLORS["position"]}"/>',
        f'<text x="160" y="{y}" {t}>Esti aici</text>',
        f'<circle cx="252" cy="{y - 6}" r="8" fill="{COLORS["highlight"]}"/>',
        f'<text x="266" y="{y}" {t}>Destinatie</text>',
        f'<text x="{w - 20}" y="{y}" font-size="16" text-anchor="end" fill="{COLORS["muted"]}">'
        f'Contur: OpenStreetMap; locuri masurate pe teren; KFC simulat (demo)</text>',
    ]


def render_map(position: str, route: list[str], highlight: list[str]) -> str:
    route = route or []
    highlight = [n for n in (highlight or []) if n in NODES]
    w, h = VIEWBOX
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="100%" '
        f'role="img" aria-label="Harta terminalului Aeroportului Craiova" '
        f'style="max-width:100%;height:auto;overflow:hidden;font-family:system-ui,Segoe UI,sans-serif;'
        f'background:{COLORS["ground"]};border-radius:12px">',
        _STYLE,
        *_building(),
        *_route(route),
        *_places(highlight),
        *_position(position),
        '<g class="acm-live"></g>',
        *_legend(),
        "</svg>",
    ]
    return "\n".join(parts)


if __name__ == "__main__":
    import os
    import tempfile

    from airport_core import get_route, new_session

    def _example(title, position, target):
        s = new_session()
        s["position"] = position
        return title, position, get_route(s, target).get("path", []), [target]

    examples = [
        ("Intrare -> Poarta 1", "entrance", "gate_1"),
        ("Intrare -> Toaleta (la stanga)", "entrance", "toilet"),
        ("Dupa securitate -> KFC", "security_out", "kfc"),
    ]
    html = "".join(f"<h3>{escape(t)}</h3>{render_map(*_example(t, p, d)[1:])}" for t, p, d in examples)
    html += f"<h3>Id-uri necunoscute (nu trebuie sa crape)</h3>{render_map('nu_exista', ['nu_exista', 'kfc'], ['nu_exista'])}"
    path = os.path.join(tempfile.gettempdir(), "airport_map_preview.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f'<!doctype html><meta charset="utf-8"><body style="max-width:1000px;margin:auto">{html}</body>')
    print(path)
