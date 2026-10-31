"""Harta terminalului ca SVG, in 3 straturi:
  1. fundal:  ZONES (dreptunghiuri) + coridoare (muchiile din EDGES, gri)
  2. traseu:  polyline prin nodurile din `route`
  3. marcaje: locuri, `highlight` (portocaliu), "Esti aici" (rosu)

Contract:
    render_map(position, route, highlight) -> str (SVG)

Id-urile necunoscute sunt ignorate (fara KeyError). Toate textele trec prin html.escape.
Previzualizare fara Streamlit:  python map_svg.py   -> scrie un .html si afiseaza calea
"""

from html import escape

from airport_core import EDGES, NODES, VIEWBOX, ZONES

ICONS = {
    "entrance": "\U0001F6AA",  # usa
    "checkin": "\U0001F9F3",   # bagaj
    "security": "\U0001F6C2",  # control
    "food": "\U0001F37D",      # farfurie
    "toilet": "\U0001F6BB",    # toaleta
    "shop": "\U0001F6CD",      # pungi
    "gate": "✈",          # avion
}

COLORS = {
    "route": "#2563eb",
    "highlight": "#f59e0b",
    "position": "#dc2626",
    "corridor": "#cbd5e1",
    "node": "#ffffff",
    "node_stroke": "#475569",
    "text": "#0f172a",
    "muted": "#64748b",
}

# Nume scurte pe harta, ca textul sa ramana lizibil pe un ecran de telefon.
SHORT_NAMES = {
    "entrance": "Intrare",
    "checkin": "Check-in",
    "cafe_public": "Cafenea",
    "toilet_a": "Toaleta",
    "toilet_b": "Toaleta",
    "kfc": "KFC",
    "shop_duty_free": "Duty Free",
    "gate_1": "Poarta 1",
    "gate_2": "Poarta 2",
}
ZONE_LABELS_HIDDEN = {"checkin_area"}  # ghiseul are deja eticheta "Check-in"

# Clasele au prefixul "acm-" ca sa nu se ciocneasca cu CSS-ul paginii.
# Daca <style> e eliminat, harta ramane corecta, doar fara animatie.
_STYLE = """
<style>
  .acm-route { stroke-dasharray: 14 10; animation: acm-flow 1s linear infinite; }
  .acm-pulse { animation: acm-pulse 1.6s ease-out infinite; transform-box: fill-box; transform-origin: center; }
  @keyframes acm-flow { to { stroke-dashoffset: -24; } }
  @keyframes acm-pulse { from { transform: scale(1); opacity: .6; } to { transform: scale(2.4); opacity: 0; } }
</style>
"""


def _xy(node_id):
    n = NODES[node_id]
    return n["x"], n["y"]


def _background():
    out = [
        f'<text x="24" y="44" font-size="28" font-weight="700" fill="{COLORS["text"]}">'
        f'Aeroportul Craiova - Terminal</text>'
    ]
    for z in ZONES:
        out.append(
            f'<rect x="{z["x"]}" y="{z["y"]}" width="{z["w"]}" height="{z["h"]}" rx="14" '
            f'fill="{z["fill"]}" stroke="#94a3b8" stroke-width="1.5"/>'
        )
        if z["id"] in ZONE_LABELS_HIDDEN:
            continue
        out.append(
            f'<text x="{z["x"] + 12}" y="{z["y"] + 30}" font-size="24" font-weight="600" '
            f'fill="{COLORS["muted"]}">{escape(z["label"])}</text>'
        )
    # Coridoarele: pe unde se poate merge. Securitatea (sens unic) e desenata punctat.
    for a, b, _meters, both_ways in EDGES:
        (x1, y1), (x2, y2) = _xy(a), _xy(b)
        dash = '' if both_ways else ' stroke-dasharray="4 6"'
        out.append(
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{COLORS["corridor"]}" '
            f'stroke-width="10" stroke-linecap="round"{dash}/>'
        )
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
        if is_hl:
            out.append(f'<circle cx="{x}" cy="{y}" r="27" fill="{COLORS["highlight"]}" opacity="0.9"/>')
        out.append(
            f'<circle cx="{x}" cy="{y}" r="19" fill="{COLORS["node"]}" '
            f'stroke="{COLORS["highlight"] if is_hl else COLORS["node_stroke"]}" stroke-width="2"/>'
        )
        icon = ICONS.get(n["kind"], "")
        if icon:
            out.append(f'<text x="{x}" y="{y + 7}" font-size="20" text-anchor="middle">{icon}</text>')
        if n["kind"] == "security":
            continue  # cele doua noduri sunt prea apropiate; zona "Securitate" are deja eticheta
        label_y = y - 32 if n.get("side", 0) > 0 else y + 48  # latura de nord: eticheta deasupra
        # Nume scurte pe harta (textul trebuie sa se citeasca pe telefon); numele complet ramane in chat.
        out.append(
            f'<text x="{x}" y="{label_y}" font-size="22" text-anchor="middle" '
            f'font-weight="{700 if is_hl else 500}" fill="{COLORS["text"]}" '
            f'paint-order="stroke" stroke="#ffffff" stroke-width="5">{escape(SHORT_NAMES.get(node_id, n["name"]))}</text>'
        )
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
    t = f'font-size="19" fill="{COLORS["text"]}"'
    return [
        f'<line x1="24" y1="{y - 6}" x2="56" y2="{y - 6}" stroke="{COLORS["route"]}" stroke-width="7" stroke-linecap="round"/>',
        f'<text x="64" y="{y}" {t}>Traseu</text>',
        f'<circle cx="150" cy="{y - 6}" r="8" fill="{COLORS["position"]}"/>',
        f'<text x="164" y="{y}" {t}>Esti aici</text>',
        f'<circle cx="262" cy="{y - 6}" r="8" fill="{COLORS["highlight"]}"/>',
        f'<text x="276" y="{y}" {t}>Destinatie</text>',
        f'<text x="{w - 20}" y="{y}" font-size="17" text-anchor="end" fill="{COLORS["muted"]}">'
        f'Coridor masurat; magazine si toalete simulate</text>',
    ]


def render_map(position: str, route: list[str], highlight: list[str]) -> str:
    route = route or []
    highlight = [n for n in (highlight or []) if n in NODES]
    w, h = VIEWBOX
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="100%" '
        f'role="img" aria-label="Harta terminalului Aeroportului Craiova" '
        f'style="max-width:100%;height:auto;font-family:system-ui,Segoe UI,sans-serif;background:#f8fafc;border-radius:12px">',
        _STYLE,
        *_background(),
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
        _example("Intrare -> Poarta 1", "entrance", "gate_1"),
        _example("Dupa securitate -> KFC", "security_out", "kfc"),
        ("Id-uri necunoscute (nu trebuie sa crape)", "nu_exista", ["nu_exista", "kfc"], ["nu_exista"]),
    ]
    html = "".join(f"<h3>{escape(t)}</h3>{render_map(p, r, hl)}" for t, p, r, hl in examples)
    path = os.path.join(tempfile.gettempdir(), "airport_map_preview.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f'<!doctype html><meta charset="utf-8"><body style="max-width:1000px;margin:auto">{html}</body>')
    print(path)
