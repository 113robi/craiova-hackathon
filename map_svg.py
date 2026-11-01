"""Harta terminalului ca SVG, desenata la scara din datele din airport_core:
  1. cladirile: contururile reale (OpenStreetMap), trecerile prin pereti, zona fara acces, securitatea
  2. traseul: polyline prin nodurile din `route` (pe drumul real, care se curbeaza)
  3. locurile, `highlight` (portocaliu), "Esti aici" (rosu)

Doua orientari, desenate amandoua in acelasi SVG (textele raman drepte in ambele):
  <g class="acm-orient" data-flip="0">  intrarea jos (nordul sus): cum vezi holul cand intri din parcare
  <g class="acm-orient" data-flip="1">  aceeasi harta rotita cu 180 de grade (intrarea sus), pentru pasagerul
                                        intors spre intrare / parcare
Cu o harta fixa, pasagerul intors spre intrare ar vedea totul in oglinda: merge spre dreapta, iar punctul
"Esti aici" se misca spre stanga. live_map.py arata orientarea potrivita dupa busola telefonului (sau dupa
butonul "Intoarce harta"). Fara JavaScript se vede doar prima orientare (a doua are display="none").

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

W, H = VIEWBOX


class _View:
    """Proiectia (s, d) in metri -> (x, y) pe harta, intr-una din cele doua orientari.

    flip=True = rotire cu 180 de grade in jurul centrului hartii: x -> W - x, y -> H - y.
    Aceeasi formula e si in live_map.py, pentru punctul live.
    """

    def __init__(self, flip=False):
        self.flip = flip
        self.id = "s" if flip else "n"   # sufix pentru id-urile din <defs>, unice in tot SVG-ul

    def x(self, s):
        x = s_to_x(s)
        return round(W - x, 1) if self.flip else x

    def y(self, d):
        y = d_to_y(d)
        return round(H - y, 1) if self.flip else y

    def text_y(self, d, size):
        """Linia de baza a unui text pus in punctul d, ca textul sa ocupe acelasi loc si dupa rotire."""
        return round(self.y(d) + 0.7 * size, 1) if self.flip else self.y(d)

    def node(self, node_id):
        n = NODES[node_id]
        return self.x(n["s"]), self.y(n["d"])


def _pts(v, outline):
    return " ".join(f"{v.x(s)},{v.y(d)}" for s, d in outline)


def _rect(v, s0, s1, d0, d1):
    xa, xb, ya, yb = v.x(s0), v.x(s1), v.y(d0), v.y(d1)
    return f'x="{min(xa, xb)}" y="{min(ya, yb)}" width="{round(abs(xb - xa), 1)}" height="{round(abs(yb - ya), 1)}"'


def _gap(v, p0, p1, color):
    """O trecere printr-un perete: o linie de culoarea podelei peste conturul peretelui."""
    (s0, d0), (s1, d1) = p0, p1
    return (f'<line x1="{v.x(s0)}" y1="{v.y(d0)}" x2="{v.x(s1)}" y2="{v.y(d1)}" '
            f'stroke="{color}" stroke-width="7" stroke-linecap="butt"/>')


def _caption(v, s, d, text, size=18):
    return (f'<text x="{v.x(s)}" y="{v.text_y(d, size)}" font-size="{size}" font-weight="700" letter-spacing="1.5" '
            f'text-anchor="middle" fill="{COLORS["muted"]}">{escape(text)}</text>')


def _building(v):
    c = COLORS
    wall = f'stroke="{c["wall"]}" stroke-linejoin="round"'
    clip_old, clip_new = f"acm-clip-old-{v.id}", f"acm-clip-new-{v.id}"
    # Sagetile arata spre pista (nord) si spre parcare (sud): se intorc odata cu harta.
    to_planes, to_parking = ("↓", "↑") if v.flip else ("↑", "↓")
    ex, ey = v.node("entrance")
    # in afara cladirii, langa intrare (cu harta intoarsa: deasupra etichetei "Esti aici")
    parking_y = round(ey - 60, 1) if v.flip else round(ey + 72, 1)
    no_access_x, no_access_y = v.x(WEST_NO_ACCESS_S - 2), v.y(5)
    out = [
        f'<defs><clipPath id="{clip_old}"><polygon points="{_pts(v, OUTLINE_OLD_HALL)}"/></clipPath>'
        f'<clipPath id="{clip_new}"><polygon points="{_pts(v, OUTLINE_NEW_TERMINAL)}"/></clipPath></defs>',
        # Cladirile, din contururile reale
        f'<polygon points="{_pts(v, OUTLINE_OTHER)}" fill="{c["other_building"]}" stroke="#94a3b8" stroke-width="1.5"/>',
        f'<polygon points="{_pts(v, OUTLINE_NEW_TERMINAL)}" fill="{c["airside"]}" {wall} stroke-width="3"/>',
        f'<polygon points="{_pts(v, OUTLINE_OLD_HALL)}" fill="{c["landside"]}" {wall} stroke-width="3"/>',
        f'<polygon points="{_pts(v, OUTLINE_TOILET_ANNEX)}" fill="{c["landside"]}" {wall} stroke-width="2"/>',
        # Partea din stanga a holului, fara acces pentru pasageri
        f'<rect {_rect(v, DRAW_S_MIN - 1, WEST_NO_ACCESS_S, 16, -6)} fill="{c["no_access"]}" opacity="0.7" '
        f'clip-path="url(#{clip_old})"/>',
        f'<text x="{no_access_x}" y="{no_access_y}" font-size="15" text-anchor="middle" '
        f'fill="{c["muted"]}" transform="rotate(-90 {no_access_x} {no_access_y})">fara acces</text>',
        # Securitatea: banda rosiatica, in interiorul cladirii alipite
        f'<rect {_rect(v, SECURITY_ZONE_S[0], SECURITY_ZONE_S[1], 20, -10)} fill="{c["security"]}" '
        f'clip-path="url(#{clip_new})"/>',
        # Trecerile prin pereti: usa de la intrare, spre toaleta, spre securitate
        _gap(v, (-2.2, -5.13), (2.2, -5.05), c["ground"]),
        _gap(v, (-5.5, 15.9), (-2.5, 15.9), c["landside"]),
        _gap(v, (-5.5, 14.2), (-2.5, 14.2), c["landside"]),
        _gap(v, (39.0, 9.4), (41.8, 6.6), c["landside"]),
        _gap(v, (39.9, 9.4), (42.9, 6.6), c["landside"]),
        # Numele zonelor si repere exterioare
        _caption(v, 20, -2.5, "HOL PUBLIC"),
        _caption(v, 49.3, 21.5, "SECURITATE"),
        _caption(v, 66, 15, "ZONA PLECARI"),
        f'<text x="{v.x(66)}" y="{v.text_y(23, 17)}" font-size="17" text-anchor="middle" fill="{c["muted"]}">'
        f'{to_planes} spre avioane</text>',
        f'<text x="{ex}" y="{parking_y}" font-size="17" text-anchor="middle" '
        f'fill="{c["muted"]}">{to_parking} parcare, autobuz</text>',
    ]
    # Pe unde se poate merge: drumul principal (se curbeaza) si drumurile spre locurile din lateral.
    # Securitatea (sens unic) e desenata punctat.
    for a, b, _meters, both_ways in EDGES:
        (x1, y1), (x2, y2) = v.node(a), v.node(b)
        dash = '' if both_ways else ' stroke-dasharray="4 6"'
        out.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{c["corridor"]}" '
                   f'stroke-width="9" stroke-linecap="round"{dash}/>')
    return out


def _route(v, route):
    route = [n for n in route if n in NODES]
    if len(route) < 2:
        return []
    points = " ".join(f"{x},{y}" for x, y in map(v.node, route))
    common = 'fill="none" stroke-linecap="round" stroke-linejoin="round"'
    return [
        # contur alb sub linie, ca traseul sa se vada peste orice fundal
        f'<polyline points="{points}" {common} stroke="#ffffff" stroke-width="12"/>',
        f'<polyline class="acm-route" points="{points}" {common} stroke="{COLORS["route"]}" stroke-width="7"/>',
    ]


def _places(v, highlight):
    out = []
    for node_id, n in NODES.items():
        if n["kind"] == "waypoint":
            continue
        x, y = v.node(node_id)
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
            # Eticheta se pune pe ecran (sub loc / la dreapta lui), deci ramane lizibila in ambele orientari.
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


def _position(v, position):
    if position not in NODES:
        return []
    x, y = v.node(position)
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


def _title():
    c = COLORS
    return [
        f'<text x="24" y="40" font-size="26" font-weight="700" fill="{c["text"]}" paint-order="stroke" '
        f'stroke="{c["ground"]}" stroke-width="8" stroke-linejoin="round">Aeroportul Craiova - Terminal</text>',
    ]


def _legend():
    y = H - 10
    # contur in culoarea fundalului: cu harta intoarsa, legenda sta peste cladiri
    t = f'font-size="18" fill="{COLORS["text"]}" paint-order="stroke" stroke="{COLORS["ground"]}" stroke-width="5"'
    return [
        f'<line x1="24" y1="{y - 6}" x2="56" y2="{y - 6}" stroke="{COLORS["route"]}" stroke-width="7" stroke-linecap="round"/>',
        f'<text x="64" y="{y}" {t}>Traseu</text>',
        f'<circle cx="146" cy="{y - 6}" r="8" fill="{COLORS["position"]}"/>',
        f'<text x="160" y="{y}" {t}>Esti aici</text>',
        f'<circle cx="252" cy="{y - 6}" r="8" fill="{COLORS["highlight"]}"/>',
        f'<text x="266" y="{y}" {t}>Destinatie</text>',
        f'<text x="{W - 20}" y="{y}" font-size="16" text-anchor="end" fill="{COLORS["muted"]}" paint-order="stroke" '
        f'stroke="{COLORS["ground"]}" stroke-width="5">Contur: OpenStreetMap; locuri masurate pe teren; KFC simulat (demo)</text>',
    ]


def render_map(position: str, route: list[str], highlight: list[str]) -> str:
    route = route or []
    highlight = [n for n in (highlight or []) if n in NODES]
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="100%" '
        f'role="img" aria-label="Harta terminalului Aeroportului Craiova" '
        f'style="max-width:100%;height:auto;overflow:hidden;font-family:system-ui,Segoe UI,sans-serif;'
        f'background:{COLORS["ground"]};border-radius:12px">',
        _STYLE,
    ]
    for v in (_View(flip=False), _View(flip=True)):
        hidden = ' display="none"' if v.flip else ''
        parts += [
            f'<g class="acm-orient" data-flip="{int(v.flip)}"{hidden}>',
            *_building(v),
            *_route(v, route),
            *_places(v, highlight),
            *_position(v, position),
            '</g>',
        ]
    parts += [*_title(), '<g class="acm-live"></g>', *_legend(), "</svg>"]
    return "\n".join(parts)


if __name__ == "__main__":
    import os
    import tempfile

    from airport_core import get_route, new_session

    def _example(title, position, target):
        s = new_session()
        s["position"] = position
        return title, position, get_route(s, target).get("path", []), [target]

    def _both(svg):
        """Acelasi SVG de doua ori: o data cu orientarea normala, o data cu harta intoarsa."""
        flipped = (svg.replace('data-flip="0">', 'data-flip="0" display="none">', 1)
                      .replace('data-flip="1" display="none">', 'data-flip="1">', 1))
        return f"{svg}<p>Harta intoarsa (pasager cu fata spre intrare):</p>{flipped}"

    examples = [
        ("Intrare -> Poarta 1", "entrance", "gate_1"),
        ("Intrare -> Toaleta (la stanga)", "entrance", "toilet"),
        ("Dupa securitate -> KFC", "security_out", "kfc"),
    ]
    html = "".join(f"<h3>{escape(t)}</h3>{_both(render_map(*_example(t, p, d)[1:]))}" for t, p, d in examples)
    html += f"<h3>Id-uri necunoscute (nu trebuie sa crape)</h3>{render_map('nu_exista', ['nu_exista', 'kfc'], ['nu_exista'])}"
    path = os.path.join(tempfile.gettempdir(), "airport_map_preview.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f'<!doctype html><meta charset="utf-8"><body style="max-width:1000px;margin:auto">{html}</body>')
    print(path)
