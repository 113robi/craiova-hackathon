"""Nucleul Airport Copilot: harta terminalului ca graf, etapele calatoriei, tool-uri, date.

Nu importa nimic din agent.py / map_svg.py / app.py. Toate celelalte depind de el.
Rulare autotest:  python airport_core.py   -> afiseaza OK

Ce e real si ce e simulat (spuneti asta in pitch):
  MASURAT pe teren (31.10.2026, Samsung S21, doua sesiuni + conturul cladirii din OpenStreetMap):
    forma holului si pozitiile intrarii, toaletei, check-in-ului, biroului de informatii,
    securitatii, cafenelei si portii.
  SIMULAT: KFC (pus doar pentru demo), starea live a zborurilor (intarzieri), regulile de imbarcare.
"""

import heapq
import math

# ---------------------------------------------------------------------------
# GEOMETRIA TERMINALULUI (masurata)
# Holul e drept, de la vest la est, ~20 m latime. Intrarea e pe peretele dinspre drum;
# la stanga intrarii holul mai continua ~8 m, la dreapta ajunge la securitate (~50 m)
# si la poarta (67 m).
# Fiecare loc are:
#   s     = metri de la intrare, de-a lungul holului (negativ = la stanga intrarii)
#   side  = 0 pe axa holului, +1 peretele opus intrarii (spre pista, sus pe harta),
#           -1 peretele cu intrarea (spre drum, jos pe harta)
#   spur  = metri de la axa holului pana la loc
# Calibrarea GPS e media celor doua sesiuni de masurare (au diferit cu ~7 m de-a lungul
# holului); butonul "Sunt la intrare" din aplicatie corecteaza diferenta din ziua respectiva.
# ---------------------------------------------------------------------------
GEO = {
    # doua puncte de pe axa holului: intrarea (s = 0) si capatul dinspre poarta (s = 67)
    "origin": (44.312463, 23.877547),
    "axis_end": (44.312479, 23.878388),
}
TERMINAL_LENGTH_M = 67      # intrare -> poarta 1, de-a lungul holului
HALL_HALF_WIDTH_M = 10      # holul are ~20 m latime
WEST_END_S = -8             # capatul din stanga al holului accesibil pasagerilor

# (id, nume, tip, zona, s, side, spur)
PLACES = [
    ("entrance",     "Intrare terminal",           "entrance", "landside", 0,  -1, HALL_HALF_WIDTH_M),
    ("toilet",       "Toaleta (hol public)",       "toilet",   "landside", -4, +1, 12),
    ("checkin",      "Ghisee check-in",            "checkin",  "landside", 14, +1, 8),
    ("info",         "Birou informatii",           "info",     "landside", 30, +1, 8),
    ("security_in",  "Intrare control securitate", "security", "landside", 47, 0,  0),
    ("security_out", "Iesire control securitate",  "security", "airside",  53, 0,  0),
    ("cafe",         "Cafenea (zona plecari)",     "food",     "airside",  55, -1, 8),
    ("kfc",          "KFC (zona plecari)",         "food",     "airside",  55, +1, 8),
    ("gate_1",       "Poarta 1",                   "gate",     "airside",  67, 0,  0),
]
SIMULATED_PLACES = {"kfc"}  # SIMULAT: nu exista in terminal, e pus doar pentru demo

# ---------------------------------------------------------------------------
# Desenul: harta 1000 x 520, holul la scara (aceeasi scara pe ambele axe).
# Distantele din graf vin din s si spur, NU din x/y.
# ---------------------------------------------------------------------------
VIEWBOX = (1000, 520)
MAP_MARGIN_X = 40
DRAW_S_MIN, DRAW_S_MAX = -10, 71    # peretii de capat ai holului
PX_PER_M = (VIEWBOX[0] - 2 * MAP_MARGIN_X) / (DRAW_S_MAX - DRAW_S_MIN)
MAP_X0 = MAP_MARGIN_X - DRAW_S_MIN * PX_PER_M   # x-ul intrarii (s = 0)
CORRIDOR_Y = 280                                 # y-ul axei holului
HALL_TOP_Y = round(CORRIDOR_Y - HALL_HALF_WIDTH_M * PX_PER_M, 1)      # peretele opus intrarii
HALL_BOTTOM_Y = round(CORRIDOR_Y + HALL_HALF_WIDTH_M * PX_PER_M, 1)   # peretele cu intrarea
SECURITY_ZONE_S = (46, 54)
TOILET_ANNEX_S = (-9, 1)   # anexa cu toaleta, in spatele peretelui opus
TOILET_ANNEX_DEPTH_M = 14.5


def s_to_x(s):
    return round(MAP_X0 + s * PX_PER_M, 1)


def offset_to_y(meters_toward_opposite_wall):
    return round(CORRIDOR_Y - meters_toward_opposite_wall * PX_PER_M, 1)


def _zone(zid, label, s0, s1, top_m, bottom_m, fill):
    """Dreptunghi intre s0..s1, de la top_m la bottom_m metri fata de axa (pozitiv = spre peretele opus)."""
    x0, x1 = s_to_x(s0), s_to_x(s1)
    y0, y1 = offset_to_y(top_m), offset_to_y(bottom_m)
    return {"id": zid, "label": label, "x": x0, "y": y0, "w": round(x1 - x0, 1), "h": round(y1 - y0, 1), "fill": fill}


ZONES = [
    _zone("landside", "Hol public", DRAW_S_MIN, SECURITY_ZONE_S[0], HALL_HALF_WIDTH_M, -HALL_HALF_WIDTH_M, "#e0f2fe"),
    _zone("security_area", "Securitate", SECURITY_ZONE_S[0], SECURITY_ZONE_S[1], HALL_HALF_WIDTH_M, -HALL_HALF_WIDTH_M, "#fecaca"),
    _zone("airside", "Zona plecari", SECURITY_ZONE_S[1], DRAW_S_MAX, HALL_HALF_WIDTH_M, -HALL_HALF_WIDTH_M, "#dcfce7"),
    _zone("toilet_annex", "", TOILET_ANNEX_S[0], TOILET_ANNEX_S[1], TOILET_ANNEX_DEPTH_M, HALL_HALF_WIDTH_M, "#e0f2fe"),
]
ZONE_BY_ID = {z["id"]: z for z in ZONES}


def _area(node_id, kind, zone):
    if node_id == "toilet":
        return "toilet_annex"
    if kind == "security":
        return "security_area"
    return zone


# ---------------------------------------------------------------------------
# T1 - NODES si EDGES, construite din PLACES
#   kind  = tipul locului; "waypoint" = punct pe axa holului, nu e loc pentru pasager
#   zone  = "landside" (inainte de securitate) / "airside" (dupa securitate)
#   area  = id-ul din ZONES in care e desenat nodul
# Pentru locurile de pe pereti exista un waypoint "wp_<id>" pe axa, in dreptul lor
# (locurile aflate la acelasi s, fata in fata, impart acelasi waypoint).
# ---------------------------------------------------------------------------
NODES = {
    "wp_west_end": {"name": "Capatul din stanga al holului", "kind": "waypoint", "zone": "landside",
                    "s": WEST_END_S, "side": 0, "x": s_to_x(WEST_END_S), "y": CORRIDOR_Y, "area": "landside"},
}
_WAYPOINT_AT_S = {}
_SPURS = []   # (waypoint, loc, metri)
for _id, _name, _kind, _zone_id, _s, _side, _spur in PLACES:
    NODES[_id] = {"name": _name, "kind": _kind, "zone": _zone_id, "s": _s, "side": _side,
                  "x": s_to_x(_s), "y": offset_to_y(_side * _spur), "area": _area(_id, _kind, _zone_id),
                  "simulated": _id in SIMULATED_PLACES}
    if _side != 0:
        if _s not in _WAYPOINT_AT_S:
            _WAYPOINT_AT_S[_s] = "wp_" + _id
            NODES["wp_" + _id] = {"name": f"Hol, in dreptul: {_name}", "kind": "waypoint", "zone": _zone_id,
                                  "s": _s, "side": 0, "x": s_to_x(_s), "y": CORRIDOR_Y, "area": _zone_id}
        _SPURS.append((_WAYPOINT_AT_S[_s], _id, _spur))

# Nodurile de pe axa holului, de la stanga la dreapta.
CORRIDOR = sorted((n for n, v in NODES.items() if v["side"] == 0), key=lambda n: NODES[n]["s"])

# (de la, la, metri, ambele_sensuri)
EDGES = []
for _a, _b in zip(CORRIDOR, CORRIDOR[1:]):
    # Securitatea: un singur sens. Dupa ea nu exista drum inapoi in zona publica.
    _both = not (_a == "security_in" and _b == "security_out")
    EDGES.append((_a, _b, NODES[_b]["s"] - NODES[_a]["s"], _both))
for _wp, _place, _m in _SPURS:
    EDGES.append((_wp, _place, _m, True))

WALK_M_PER_MIN = 60  # pas de pasager cu bagaj de mana

# Locuri pe care le poate cere pasagerul (fara waypoint-uri si fara nodurile tehnice de securitate).
PLACE_KINDS = ("food", "toilet", "gate", "checkin", "info")
KIND_LABELS = {"food": "mancare", "toilet": "toaleta", "gate": "poarta", "checkin": "check-in", "info": "informatii"}
# Locurile pe care se poate apasa pe harta (si iesirea, ca pasagerul sa gaseasca drumul spre ea).
CLICKABLE = [n for n, v in NODES.items() if v["kind"] in PLACE_KINDS or v["kind"] == "entrance"]


def _adjacency():
    """Lista de vecini directionala: {nod: [(vecin, metri), ...]}."""
    adj = {node_id: [] for node_id in NODES}
    for a, b, meters, both_ways in EDGES:
        adj[a].append((b, meters))
        if both_ways:
            adj[b].append((a, meters))
    return adj


ADJ = _adjacency()


# ---------------------------------------------------------------------------
# Localizare live: GPS (lat, lon) -> s (metri de la intrare) -> nod pe axa holului
# ---------------------------------------------------------------------------
def _geo_axis():
    lat0, lon0 = GEO["origin"]
    k = math.cos(math.radians(lat0))  # metri pe grad de longitudine scad cu latitudinea
    ex = (GEO["axis_end"][1] - lon0) * 111320 * k
    ny = (GEO["axis_end"][0] - lat0) * 111320
    length = math.hypot(ex, ny)
    return {"lat0": lat0, "lon0": lon0, "k": k, "ux": ex / length, "uy": ny / length}


GEO_AXIS = _geo_axis()  # trimis si in browser, ca JS sa calculeze identic


def geo_to_s(lat, lon):
    """Metri de la intrare, de-a lungul holului (proiectie pe axa)."""
    a = GEO_AXIS
    ex = (lon - a["lon0"]) * 111320 * a["k"]
    ny = (lat - a["lat0"]) * 111320
    return ex * a["ux"] + ny * a["uy"]


# ---------------------------------------------------------------------------
# T4 - Zboruri, reguli, program
# ---------------------------------------------------------------------------
# SIMULAT: starea "live" a zborurilor din demo (intarziere, poarta). Ora programata vine de pe
# biletul scanat, daca exista; altfel se foloseste "departure" de aici.
FLIGHTS = {
    "W6 1234": {"destination": "Londra Luton", "departure": "07:30", "delay_min": 0, "gate": "gate_1"},
    "W6 5678": {"destination": "Roma", "departure": "09:00", "delay_min": 120, "gate": "gate_1"},
}
DEFAULT_GATE = "gate_1"   # terminalul are o singura poarta

RULES = {
    # SIMULAT - folosite doar cand biletul nu are tiparite ora de imbarcare / inchiderea portii
    "boarding_opens_min_before": 40,
    "gate_closes_min_before": 15,
    # Reg. (CE) 261/2004 art. 6: asistenta (masa, racoritoare) de la 2h intarziere
    # pentru zboruri <= 1500 km. DE VERIFICAT pentru fiecare zbor; nu e despagubire in bani.
    "care_after_delay_min": 120,
}

# Programul de functionare nu e cunoscut pentru niciun loc: agentul nu afirma "deschis".
OPENING_HOURS = {}

# ---------------------------------------------------------------------------
# T3 - Starile calatoriei si sesiunea
# ---------------------------------------------------------------------------
STATES = ["ARRIVED", "CHECKED_IN", "THROUGH_SECURITY", "BOARDING"]


def new_session(flight="W6 1234", has_hold_bag=True, now="06:10"):
    return dict(state="ARRIVED", position="entrance", flight=flight, has_hold_bag=has_hold_bag, now=now,
                ticket=None)


def tool_advance_state(session, new_state):
    """Avanseaza starea (doar inainte) si muta pozitia. Modifica `session` pe loc.

    Folosit de butoanele din app.py, NU e expus LLM-ului: pasagerul confirma
    prin buton, nu prin chat, ca modelul sa nu "treaca" pe cineva de securitate.
    """
    if new_state not in STATES:
        return {"ok": False, "error": f"Stare necunoscuta: {new_state}"}
    current = STATES.index(session["state"])
    target = STATES.index(new_state)
    if target <= current:
        return {"ok": False, "error": "Starea calatoriei poate merge doar inainte."}
    if session["state"] == "ARRIVED" and new_state != "CHECKED_IN" and session["has_hold_bag"]:
        return {"ok": False, "error": "Ai bagaj de cala: trebuie predat intai la check-in."}

    session["state"] = new_state
    if new_state == "CHECKED_IN":
        session["position"] = "checkin"
    elif new_state == "THROUGH_SECURITY":
        session["position"] = "security_out"
    elif new_state == "BOARDING":
        session["position"] = FLIGHTS.get(session["flight"], {}).get("gate", DEFAULT_GATE)
    return {"ok": True, "state": session["state"], "position": session["position"]}


# ---------------------------------------------------------------------------
# Localizare live: s (metri de la intrare, din GPS) -> nod din graf
# ---------------------------------------------------------------------------
# Peste acest s, GPS-ul arata clar zona de dupa control (mijlocul securitatii + 3 m marja).
SECURITY_PASSED_S = (NODES["security_in"]["s"] + NODES["security_out"]["s"]) / 2 + 3


def locate(s, state):
    """Nodul de pe axa holului cel mai apropiat de s, tinand cont de etapa calatoriei.

    Inainte de control pasagerul ramane in zona publica (cel mult la intrarea in control);
    daca GPS-ul il arata clar dupa control, `suggest_security` = True si aplicatia il intreaba.
    Dupa control nu se mai poate intoarce: zgomotul GPS nu il muta inapoi in zona publica.
    """
    passed = STATES.index(state) >= STATES.index("THROUGH_SECURITY")
    zone = "airside" if passed else "landside"
    candidates = [n for n in CORRIDOR if NODES[n]["zone"] == zone]
    node = min(candidates, key=lambda n: abs(NODES[n]["s"] - s))
    return {"node": node, "suggest_security": (not passed) and s >= SECURITY_PASSED_S}


def apply_live_position(session, s):
    """Muta pasagerul pe nodul dat de GPS. Modifica `session` pe loc."""
    loc = locate(s, session["state"])
    changed = loc["node"] != session["position"]
    session["position"] = loc["node"]
    return {**loc, "changed": changed}


# ---------------------------------------------------------------------------
# Utilitare
# ---------------------------------------------------------------------------
def _to_min(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _to_hhmm(minutes):
    minutes %= 24 * 60
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _walk_min(meters):
    return max(1, math.ceil(meters / WALK_M_PER_MIN)) if meters else 0


def _is_open(node_id, now):
    """True/False daca programul e cunoscut, None daca nu."""
    hours = OPENING_HOURS.get(node_id)
    if not hours:
        return None
    return _to_min(hours[0]) <= _to_min(now) < _to_min(hours[1])


def _dijkstra(start):
    """Distante minime si predecesori de la `start`, pe graful directional."""
    dist = {start: 0}
    prev = {}
    heap = [(0, start)]
    while heap:
        d, node = heapq.heappop(heap)
        if d > dist[node]:
            continue
        for neighbor, meters in ADJ[node]:
            nd = d + meters
            if nd < dist.get(neighbor, math.inf):
                dist[neighbor] = nd
                prev[neighbor] = node
                heapq.heappush(heap, (nd, neighbor))
    return dist, prev


def _path_to(prev, start, target):
    path = [target]
    while path[-1] != start:
        path.append(prev[path[-1]])
    return path[::-1]


def _place_info(node_id, dist, now):
    node = NODES[node_id]
    info = {"id": node_id, "name": node["name"], "kind": node["kind"], "open_now": _is_open(node_id, now)}
    if node_id in OPENING_HOURS:
        info["hours"] = "-".join(OPENING_HOURS[node_id])
    if node_id in dist:
        info["distance_m"] = dist[node_id]
        info["walk_min"] = _walk_min(dist[node_id])
    return info


# ---------------------------------------------------------------------------
# T5 - Tool-uri (deterministe; primesc session + argumentele de la LLM)
# ---------------------------------------------------------------------------
def get_route(session, to):
    """Traseul cel mai scurt de la pozitia pasagerului.

    `to` poate fi un id de nod ("cafe") sau un tip ("toilet", "food", "gate").
    Pentru "gate" se alege poarta zborului pasagerului.
    """
    start = session["position"]
    dist, prev = _dijkstra(start)

    if to == "gate":
        to = FLIGHTS.get(session.get("flight"), {}).get("gate", DEFAULT_GATE)

    if to in NODES:
        target = to
    elif to in PLACE_KINDS:
        candidates = [n for n, v in NODES.items() if v["kind"] == to and n in dist]
        if not candidates:
            return {"reachable": False,
                    "reason": f"Nu exista niciun loc de tipul '{KIND_LABELS[to]}' accesibil din pozitia ta. "
                              "Intreaba personalul aeroportului."}
        target = min(candidates, key=lambda n: dist[n])
    else:
        return {"reachable": False, "reason": f"Loc necunoscut: '{to}'."}

    if target not in dist:
        reason = "Nu exista drum pana acolo din pozitia ta."
        if NODES[start]["zone"] == "airside" and NODES[target]["zone"] == "landside":
            reason = ("Dupa controlul de securitate nu te mai poti intoarce in zona publica "
                      "(check-in, toaleta, biroul de informatii, iesirea). Pentru probleme cu biletul sau bagajul, "
                      "cere ajutor personalului din zona de plecari.")
        return {"reachable": False, "target": target, "target_name": NODES[target]["name"], "reason": reason}

    return {
        "reachable": True,
        "target": target,
        "target_name": NODES[target]["name"],
        "path": _path_to(prev, start, target),
        "distance_m": dist[target],
        "walk_min": _walk_min(dist[target]),
        "open_now": _is_open(target, session["now"]),
    }


def list_places(session, kind=None):
    """Locurile din aeroport, separate dupa accesibilitate din pozitia curenta.

    available_now   = in zona pasagerului, se poate ajunge acum
    after_security  = in zona de plecari, accesibile abia dupa control
    not_accessible  = in zona publica, pasagerul e deja dupa control
    """
    start = session["position"]
    my_zone = NODES[start]["zone"]
    dist, _ = _dijkstra(start)
    result = {"position": start, "position_name": NODES[start]["name"],
              "available_now": [], "after_security": [], "not_accessible": []}

    for node_id, node in NODES.items():
        if node["kind"] not in PLACE_KINDS:
            continue
        if kind and node["kind"] != kind:
            continue
        info = _place_info(node_id, dist, session["now"])
        if node_id not in dist:
            result["not_accessible"].append(info)
        elif node["zone"] == my_zone:
            result["available_now"].append(info)
        else:
            result["after_security"].append(info)

    for key in ("available_now", "after_security", "not_accessible"):
        result[key].sort(key=lambda p: p.get("distance_m", math.inf))
    return result


def flight_status(session):
    """Starea zborului pasagerului.

    Ora programata vine de pe biletul scanat (session["ticket"]), daca exista pentru zborul ales;
    intarzierea vine din FLIGHTS (date live simulate). Ora de imbarcare si inchiderea portii
    vin de pe bilet daca sunt tiparite, altfel din RULES.
    """
    code = session.get("flight")
    live = FLIGHTS.get(code)
    ticket = session.get("ticket")
    if not ticket or ticket.get("flight") != code:
        ticket = None
    if not live and not ticket:
        return {"found": False, "reason": f"Nu am date despre zborul '{code}'. Verifica pe biletul tau sau la ghiseu."}

    departure = ticket["departure"] if ticket else live["departure"]
    delay = live["delay_min"] if live else None   # None = nu avem date live
    shift = delay or 0
    now = _to_min(session["now"])
    estimated = _to_min(departure) + shift
    if ticket and ticket.get("boarding"):
        boarding_opens = _to_min(ticket["boarding"]) + shift
    else:
        boarding_opens = estimated - RULES["boarding_opens_min_before"]
    if ticket and ticket.get("gate_closes"):
        gate_closes = _to_min(ticket["gate_closes"]) + shift
    else:
        gate_closes = estimated - RULES["gate_closes_min_before"]
    gate = live["gate"] if live else DEFAULT_GATE

    if delay is None:
        status = "FARA DATE LIVE"
    else:
        status = "INTARZIAT" if delay > 0 else "LA TIMP"
    ticket_times = bool(ticket and (ticket.get("boarding") or ticket.get("gate_closes")))
    result = {
        "found": True,
        "flight": code,
        "destination": (ticket or {}).get("destination") or (live or {}).get("destination") or "necunoscuta",
        "scheduled_departure": departure,
        "delay_min": delay,
        "status": status,
        "estimated_departure": _to_hhmm(estimated),
        "gate": gate,
        "gate_name": NODES[gate]["name"],
        "boarding_opens": _to_hhmm(boarding_opens),
        "gate_closes": _to_hhmm(gate_closes),
        "minutes_until_boarding": boarding_opens - now,
        "minutes_until_gate_closes": gate_closes - now,
        "now": session["now"],
        "care_assistance_possible": delay is not None and delay >= RULES["care_after_delay_min"],
        "schedule_source": "biletul scanat" if ticket else "date simulate",
        "boarding_times_source": "biletul scanat" if ticket_times else "reguli simulate",
        "live_data": "simulate pentru demo" if live else "indisponibile",
    }
    if ticket:
        result["ticket_date"] = ticket.get("date")
        result["seat"] = ticket.get("seat")
    return result


def next_step(session):
    """Ce are de facut pasagerul acum, in functie de stare."""
    state = session["state"]
    fs = flight_status(session)
    gate = fs["gate"] if fs.get("found") else DEFAULT_GATE

    if state == "ARRIVED":
        if session["has_hold_bag"]:
            step, go_to = "Mergi la check-in si preda bagajul de cala.", "checkin"
        else:
            step, go_to = "Fara bagaj de cala: daca ai check-in online, mergi direct la controlul de securitate.", "security_in"
    elif state == "CHECKED_IN":
        step, go_to = "Mergi la controlul de securitate.", "security_in"
    elif state == "THROUGH_SECURITY":
        if fs.get("found") and fs["minutes_until_boarding"] > 0:
            step = f"Ai timp liber pana la imbarcare (se deschide la {fs['boarding_opens']}). Poti merge la cafenea."
        else:
            step = "Imbarcarea e deschisa sau incepe acum: mergi la poarta."
        go_to = gate
    else:  # BOARDING
        step, go_to = "Esti la imbarcare. Pregateste biletul si actul de identitate.", gate

    result = {"state": state, "step": step, "go_to": go_to}
    if go_to in NODES:
        result["go_to_name"] = NODES[go_to]["name"]
    return result


# ---------------------------------------------------------------------------
# T6 - TOOLS (declaratii pentru LLM), DISPATCH, SYSTEM_PROMPT
# `session` NU apare in parametri: il adauga agent.py la executie.
# ---------------------------------------------------------------------------
_PLACE_IDS = [n for n, v in NODES.items() if v["kind"] in PLACE_KINDS]

TOOLS = [
    {
        "name": "get_route",
        "description": ("Calculeaza traseul pe jos de la pozitia actuala a pasagerului pana la o destinatie. "
                        "Spune si daca destinatia NU e accesibila (de ex. zona publica dupa securitate)."),
        "parameters": {
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "description": ("Id-ul locului (" + ", ".join(_PLACE_IDS) + ") sau un tip de loc ("
                                    + ", ".join(PLACE_KINDS) + "). Pentru 'gate' se foloseste poarta zborului pasagerului."),
                    "enum": _PLACE_IDS + list(PLACE_KINDS),
                }
            },
            "required": ["to"],
        },
    },
    {
        "name": "list_places",
        "description": ("Listeaza locurile din terminal (mancare, toaleta, poarta, check-in, informatii), separate in: "
                        "available_now (accesibile acum), after_security (doar dupa control), not_accessible. "
                        "Include distanta, minute de mers si open_now (null = program necunoscut)."),
        "parameters": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "description": "Optional: filtreaza dupa tip.",
                    "enum": list(PLACE_KINDS),
                }
            },
        },
    },
    {
        "name": "flight_status",
        "description": ("Starea zborului pasagerului: ora programata (de pe biletul scanat, daca exista), intarziere, "
                        "ora estimata de plecare, poarta, cand se deschide imbarcarea, cand se inchide poarta si cate "
                        "minute mai sunt."),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "next_step",
        "description": "Ce trebuie sa faca pasagerul acum, in functie de etapa calatoriei (check-in, securitate, imbarcare).",
        "parameters": {"type": "object", "properties": {}},
    },
]

DISPATCH = {
    "get_route": get_route,
    "list_places": list_places,
    "flight_status": flight_status,
    "next_step": next_step,
}

SYSTEM_PROMPT = """Esti Airport Copilot, asistentul pasagerilor din Aeroportul International Craiova.

Reguli:
1. Raspunzi in limba romana, scurt (2-4 propozitii), prietenos si concret.
2. Folosesti DOAR informatii din rezultatele tool-urilor. Nu inventezi ore, porti, reguli, preturi sau locuri.
3. Daca o informatie nu exista in rezultatele tool-urilor, spui: "Nu am aceasta informatie, verifica pe biletul tau sau la biroul de informatii."
4. Pentru "unde e / cum ajung / unde pot..." apelezi get_route sau list_places. Spui numele locului si cate minute de mers sunt.
5. Daca un loc e in after_security, spui clar ca e disponibil abia dupa controlul de securitate.
6. Dupa controlul de securitate pasagerul NU se mai poate intoarce in zona publica (check-in, toaleta, biroul de informatii, iesirea). Daca get_route spune reachable=false, explici motivul si oferi o alternativa din zona lui, daca exista.
7. Daca open_now=false, spui ca locul e inchis. Daca open_now este null, nu spui nici ca e deschis, nici ca e inchis.
8. Pentru intarzieri: nu promiti despagubiri in bani. Daca care_assistance_possible=true, spui ca pasagerul poate cere asistenta (masa, racoritoare) la ghiseul companiei aeriene.
9. Daca flight_status are status "FARA DATE LIVE", spui ora de pe bilet si ca nu ai informatii despre eventuale intarzieri.
10. Pentru intrebari care nu au legatura cu aeroportul sau calatoria, raspunzi politicos intr-o propozitie ca poti ajuta doar cu aeroportul, fara sa apelezi tool-uri.
11. Nu pomenesti id-uri tehnice (de ex. "gate_1", "security_out"); folosesti numele din campurile name / target_name / gate_name.
12. Pasagerul poate apasa pe orice loc de pe harta ca sa vada traseul; poti sa-i amintesti asta cand e util.
"""


def session_context(session):
    """Text scurt despre sesiune, adaugat de agent.py la fiecare mesaj."""
    pos = NODES.get(session["position"], {}).get("name", session["position"])
    bag = "are bagaj de cala" if session["has_hold_bag"] else "doar bagaj de mana"
    ticket = session.get("ticket")
    scanned = (f" Bilet scanat: zborul {ticket['flight']}, plecare {ticket['departure']}."
               if ticket and ticket.get("flight") == session["flight"] else "")
    return (f"[Context pasager] Etapa: {session['state']}. Pozitie: {pos}. "
            f"Zbor: {session['flight']}. Ora curenta: {session['now']}. Pasagerul {bag}.{scanned}")


# ---------------------------------------------------------------------------
# T7 - Autotest (fara Gemini, fara internet)
# ---------------------------------------------------------------------------
def _self_test():
    import json

    # Graful: muchii valide, fara distante nule
    for a, b, meters, _ in EDGES:
        assert a in NODES and b in NODES, f"muchie cu nod inexistent: {a}->{b}"
        assert meters > 0, f"muchie de lungime 0: {a}->{b}"
    # Desenul: fiecare nod e in interiorul zonei lui (toleranta de rotunjire)
    for node_id, n in NODES.items():
        z = ZONE_BY_ID[n["area"]]
        assert (z["x"] - 0.5 <= n["x"] <= z["x"] + z["w"] + 0.5 and
                z["y"] - 0.5 <= n["y"] <= z["y"] + z["h"] + 0.5), f"{node_id} in afara zonei {z['id']}"
    for code, f in FLIGHTS.items():
        assert NODES[f["gate"]]["kind"] == "gate", code
    assert set(CLICKABLE) == {"entrance", "toilet", "checkin", "info", "cafe", "kfc", "gate_1"}

    # Traseu intrare -> poarta 1: prin securitate, 10 m de la usa la axa holului + 67 m de hol
    s = new_session()
    r = get_route(s, "gate_1")
    assert r["reachable"] and r["path"][0] == "entrance" and r["path"][-1] == "gate_1"
    i = r["path"].index("security_in")
    assert r["path"][i + 1] == "security_out"
    assert r["distance_m"] == HALL_HALF_WIDTH_M + TERMINAL_LENGTH_M
    # Holul merge si la stanga intrarii: toaleta e la stanga, pe peretele opus
    r = get_route(s, "toilet")
    assert r["target"] == "toilet" and "wp_entrance" in r["path"] and NODES["toilet"]["s"] < 0
    assert get_route(s, "gate")["target"] == "gate_1"
    assert get_route(s, "info")["target"] == "info"
    # Mancare: doar dupa securitate (cafeneaua si KFC); programul nu e cunoscut
    lp = list_places(s, "food")
    assert lp["available_now"] == [] and {p["id"] for p in lp["after_security"]} == {"cafe", "kfc"}
    assert all(p["open_now"] is None for p in lp["after_security"])

    # Etapele calatoriei
    s = new_session()
    assert not tool_advance_state(s, "THROUGH_SECURITY")["ok"]  # are bagaj de cala
    assert tool_advance_state(s, "CHECKED_IN")["ok"] and s["position"] == "checkin"
    assert tool_advance_state(s, "THROUGH_SECURITY")["ok"] and s["position"] == "security_out"
    assert not tool_advance_state(s, "CHECKED_IN")["ok"]  # nu se merge inapoi
    r = get_route(s, "checkin")
    assert not r["reachable"] and "securitate" in r["reason"]
    assert not get_route(s, "toilet")["reachable"]          # nu exista toaleta dupa securitate
    assert not get_route(s, "entrance")["reachable"]        # nici iesirea
    r = get_route(s, "kfc")
    assert r["reachable"] and r["path"][-1] == "kfc"
    assert {p["id"] for p in list_places(s, "food")["available_now"]} == {"cafe", "kfc"}
    assert tool_advance_state(s, "BOARDING")["ok"] and s["position"] == "gate_1"
    s2 = new_session(has_hold_bag=False)
    assert tool_advance_state(s2, "THROUGH_SECURITY")["ok"]

    # Zborurile simulate (fara bilet)
    fs = flight_status(new_session(flight="W6 5678", now="08:00"))
    assert fs["delay_min"] == 120 and fs["estimated_departure"] == "11:00" and fs["boarding_opens"] == "10:20"
    assert fs["minutes_until_boarding"] == 140 and fs["care_assistance_possible"] and fs["gate"] == "gate_1"
    assert not flight_status(new_session(flight="XX 0000"))["found"]
    assert next_step(new_session())["go_to"] == "checkin"
    # Bilet scanat: ora de pe bilet + intarzierea live (simulata) a zborului
    s = new_session(flight="W6 5678", now="18:00")
    s["ticket"] = {"flight": "W6 5678", "date": "2026-10-31", "departure": "19:30", "boarding": "18:50",
                   "gate_closes": "19:15", "destination": "Roma", "seat": "12A"}
    fs = flight_status(s)
    assert fs["scheduled_departure"] == "19:30" and fs["estimated_departure"] == "21:30"
    assert fs["boarding_opens"] == "20:50" and fs["gate_closes"] == "21:15"
    assert fs["minutes_until_boarding"] == 170 and fs["schedule_source"] == "biletul scanat"
    assert fs["boarding_times_source"] == "biletul scanat" and fs["seat"] == "12A"
    # Bilet pentru un zbor fara date live: ora de pe bilet, fara intarziere cunoscuta
    s = new_session(flight="FR 1234", now="10:00")
    s["ticket"] = {"flight": "FR 1234", "departure": "12:00", "destination": "Milano"}
    fs = flight_status(s)
    assert fs["found"] and fs["status"] == "FARA DATE LIVE" and fs["delay_min"] is None
    assert fs["boarding_opens"] == "11:20" and not fs["care_assistance_possible"]
    assert "plecare 12:00" in session_context(s)

    # Tool-uri: fiecare are implementare, rezultatele sunt JSON
    assert {t["name"] for t in TOOLS} == set(DISPATCH)
    s = new_session()
    for name, fn in DISPATCH.items():
        json.dumps(fn(s, "kfc") if name == "get_route" else fn(s))

    # Calibrarea GPS: intrarea masurata in cele doua sesiuni cade la +-3.5 m de 0,
    # iar media celor doua masuratori ale portii cade la 67 m
    assert abs(geo_to_s(44.312464, 23.877588) - 3.3) < 0.5    # sesiunea 1
    assert abs(geo_to_s(44.312551, 23.877503) + 3.2) < 0.5    # sesiunea 2
    assert abs((geo_to_s(44.312480, 23.878433) + geo_to_s(44.312413, 23.878344)) / 2 - 67) < 1
    # Localizare pe axa holului
    assert locate(-15, "ARRIVED")["node"] == "wp_west_end"
    assert locate(1, "ARRIVED")["node"] == "wp_entrance"
    assert locate(-4.5, "ARRIVED")["node"] == "wp_toilet"
    assert locate(48, "ARRIVED") == {"node": "security_in", "suggest_security": False}
    # GPS-ul il arata dupa control, dar etapa nu e confirmata: ramane in zona publica si e intrebat
    assert locate(56, "CHECKED_IN") == {"node": "security_in", "suggest_security": True}
    # dupa control, zgomotul GPS nu il intoarce in zona publica
    assert locate(20, "THROUGH_SECURITY") == {"node": "security_out", "suggest_security": False}
    assert locate(98, "THROUGH_SECURITY")["node"] == "gate_1"
    s = new_session()
    r = apply_live_position(s, 13)
    assert r["changed"] and s["position"] == "wp_checkin"
    assert not apply_live_position(s, 15)["changed"]
    assert get_route(s, "gate_1")["reachable"]  # rutele pornesc si dintr-un punct de pe axa

    print("OK")


if __name__ == "__main__":
    _self_test()
