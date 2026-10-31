"""Harta terminalului ca SVG, desenata la scara din datele din airport_core:
  1. cladirea: holul (zone colorate + pereti), anexa cu toaleta, usa de la intrare, repere exterioare
  2. traseul: polyline prin nodurile din `route`
  3. locurile, `highlight` (portocaliu), "Esti aici" (rosu)

Pe locuri se poate apasa: fiecare e un <g class="acm-place" data-node="<id>">; live_map.py
asculta click-urile si trimite id-ul la Python, care deseneaza traseul.

Contract:
    render_map(position, route, highlight) -> str (SVG)

Id-urile necunoscute sunt ignorate (fara KeyError). Toate textele trec prin html.escape.
Previzualizare fara Streamlit:  python map_svg.py   -> scrie un .html si afiseaza calea
"""

from html import escape

from airport_core import (CLICKABLE, CORRIDOR_Y, DRAW_S_MAX, DRAW_S_MIN, EDGES, HALL_BOTTOM_Y, HALL_TOP_Y, NODES,
                          VIEWBOX, ZONES, s_to_x)

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


def _building():
    w, _h = VIEWBOX
    hall_x0, hall_x1 = s_to_x(DRAW_S_MIN), s_to_x(DRAW_S_MAX)
    zone_fill = {z["id"]: z["fill"] for z in ZONES}
    out = [
        f'<text x="24" y="40" font-size="26" font-weight="700" fill="{COLORS["text"]}">'
        f'Aeroportul Craiova - Terminal</text>'
    ]
    # Zonele colorate (holul si anexa cu toaleta)
    for z in ZONES:
        stroke = f' stroke="{COLORS["wall"]}" stroke-width="2"' if z["id"] == "toilet_annex" else ""
        out.append(f'<rect x="{z["x"]}" y="{z["y"]}" width="{z["w"]}" height="{z["h"]}" fill="{z["fill"]}"{stroke}/>')
    # Peretii holului
    out.append(f'<rect x="{hall_x0}" y="{HALL_TOP_Y}" width="{round(hall_x1 - hall_x0, 1)}" '
               f'height="{round(HALL_BOTTOM_Y - HALL_TOP_Y, 1)}" rx="6" fill="none" '
               f'stroke="{COLORS["wall"]}" stroke-width="3"/>')
    # Trecerea din hol spre anexa cu toaleta (golul din peretele de sus)
    tx = NODES["toilet"]["x"]
    out.append(f'<line x1="{tx - 17}" y1="{HALL_TOP_Y}" x2="{tx + 17}" y2="{HALL_TOP_Y}" '
               f'stroke="{zone_fill["landside"]}" stroke-width="6"/>')
    # Usa de la intrare (golul din peretele de jos)
    ex = NODES["entrance"]["x"]
    out.append(f'<line x1="{ex - 22}" y1="{HALL_BOTTOM_Y}" x2="{ex + 22}" y2="{HALL_BOTTOM_Y}" '
               f'stroke="{COLORS["ground"]}" stroke-width="6"/>')
    # Numele zonelor, deasupra holului; securitatea e ingusta, asa ca numele ei sta sub hol
    for z in ZONES:
        if not z["label"]:
            continue
        cx = round(z["x"] + z["w"] / 2, 1)
        y = HALL_BOTTOM_Y + 26 if z["id"] == "security_area" else HALL_TOP_Y - 12
        out.append(f'<text x="{cx}" y="{y}" font-size="18" font-weight="700" letter-spacing="1.5" '
                   f'text-anchor="middle" fill="{COLORS["muted"]}">{escape(z["label"].upper())}</text>')
    # Repere exterioare: drumul (sub intrare) si platforma avioanelor (dincolo de zona de plecari)
    airside = next(z for z in ZONES if z["id"] == "airside")
    out.append(f'<text x="{round(airside["x"] + airside["w"] / 2, 1)}" y="{HALL_TOP_Y - 40}" font-size="17" '
               f'text-anchor="middle" fill="{COLORS["muted"]}">↑ spre avioane</text>')
    out.append(f'<text x="{ex}" y="{HALL_BOTTOM_Y + 72}" font-size="17" text-anchor="middle" '
               f'fill="{COLORS["muted"]}">↓ parcare, autobuz</text>')
    # Pe unde se poate merge: axa holului si drumurile spre locurile de pe pereti.
    # Securitatea (sens unic) e desenata punctat.
    for a, b, _meters, both_ways in EDGES:
        (x1, y1), (x2, y2) = _xy(a), _xy(b)
        dash = '' if both_ways else ' stroke-dasharray="4 6"'
        out.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{COLORS["corridor"]}" '
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
        f'Hol masurat pe teren; KFC simulat (demo)</text>',
    ]


def render_map(position: str, route: list[str], highlight: list[str]) -> str:
    route = route or []
    highlight = [n for n in (highlight or []) if n in NODES]
    w, h = VIEWBOX
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="100%" '
        f'role="img" aria-label="Harta terminalului Aeroportului Craiova" '
        f'style="max-width:100%;height:auto;font-family:system-ui,Segoe UI,sans-serif;'
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
