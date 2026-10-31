"""Nucleul Airport Copilot: harta ca graf, starea calatoriei, tool-uri, date simulate.

Nu importa nimic din agent.py / map_svg.py / app.py. Toate celelalte depind de el.
Rulare autotest:  python airport_core.py   -> afiseaza OK

ATENTIE: tot ce e marcat SIMULAT trebuie spus explicit in pitch
(coordonate, distante, magazine, program, zboruri, reguli).
"""

import heapq
import math

# ---------------------------------------------------------------------------
# GEOMETRIA TERMINALULUI
# Terminalul (vechi, Craiova) e un coridor drept, de la vest (intrare) la est (porti).
# Fiecare loc are:
#   s    = metri de la intrare, de-a lungul coridorului
#   side = 0 pe coridor, +1 latura de nord (sus pe harta), -1 latura de sud (jos)
# MASURAT (31.10.2026, Samsung S21): entrance, security_in, security_out, gate_1.
# MASURAT de echipa (~10 m de la intrare): checkin.
# SIMULAT: cafenea, toalete, KFC, Duty Free, poarta 2 -> schimbati `s`/`side` dupa realitate.
# ---------------------------------------------------------------------------
GEO = {
    # Doua puncte GPS care definesc axa coridorului (S21: "Intrare" precizie 3 m, "Poarta" 14 m).
    "origin": (44.312464, 23.877588),
    "axis_end": (44.312480, 23.878433),
}
TERMINAL_LENGTH_M = 67
SPUR_M = 5  # metri de la coridor pana la un loc lateral (ghiseu, toaleta, magazin)

# (id, nume, tip, zona, s, side)
PLACES = [
    ("entrance",       "Intrare terminal",            "entrance", "landside", 0,  0),
    ("checkin",        "Ghisee check-in",             "checkin",  "landside", 10, +1),
    ("cafe_public",    "Cafenea (zona publica)",      "food",     "landside", 15, -1),
    ("toilet_a",       "Toaleta hol public",          "toilet",   "landside", 19, +1),
    ("security_in",    "Intrare control securitate",  "security", "landside", 25, 0),
    ("security_out",   "Iesire control securitate",   "security", "airside",  39, 0),
    ("kfc",            "KFC",                         "food",     "airside",  46, +1),
    ("shop_duty_free", "Duty Free",                   "shop",     "airside",  51, -1),
    ("toilet_b",       "Toaleta zona plecari",        "toilet",   "airside",  56, +1),
    ("gate_2",         "Poarta 2",                    "gate",     "airside",  61, -1),
    ("gate_1",         "Poarta 1",                    "gate",     "airside",  67, 0),
]

# ---------------------------------------------------------------------------
# Desenul: harta 1000 x 600, coridorul orizontal. x se calculeaza din s, deci
# harta e la scara. Distantele din graf vin din s, NU din x/y.
# ---------------------------------------------------------------------------
VIEWBOX = (1000, 600)
MAP_X0 = 70          # x-ul intrarii
PX_PER_M = 12.8      # 67 m -> ~858 px
CORRIDOR_Y = 330
SPUR_PX = 110        # cat de departe de coridor se deseneaza locurile laterale

SECURITY_ZONE_S = (23.5, 42)   # banda "Securitate" pe harta
GATE_ZONE_S = (58, 71)


def s_to_x(s):
    return round(MAP_X0 + s * PX_PER_M, 1)


def _zone(zid, label, s0, s1, y, h, fill):
    x0, x1 = s_to_x(s0), s_to_x(s1)
    return {"id": zid, "label": label, "x": x0, "y": y, "w": round(x1 - x0, 1), "h": h, "fill": fill}


ZONES = [
    _zone("landside", "Hol public", -4, SECURITY_ZONE_S[0] - 0.5, 70, 490, "#e0f2fe"),
    _zone("checkin_area", "Check-in", 5, 15, 120, 150, "#bae6fd"),
    _zone("security_area", "Securitate", SECURITY_ZONE_S[0], SECURITY_ZONE_S[1], 70, 490, "#fecaca"),
    _zone("airside", "Zona plecari", SECURITY_ZONE_S[1] + 0.5, 71, 70, 490, "#dcfce7"),
    _zone("gate_area", "Porti", GATE_ZONE_S[0], GATE_ZONE_S[1] - 0.5, 90, 450, "#fde68a"),
]
ZONE_BY_ID = {z["id"]: z for z in ZONES}


def _area(kind, zone, s):
    if kind == "checkin":
        return "checkin_area"
    if SECURITY_ZONE_S[0] <= s <= SECURITY_ZONE_S[1]:
        return "security_area"
    if zone == "airside" and s >= GATE_ZONE_S[0]:
        return "gate_area"
    return zone


# ---------------------------------------------------------------------------
# T1 - NODES si EDGES, construite din PLACES
#   kind  = tipul locului; "waypoint" = punct pe coridor, nu e loc pentru pasager
#   zone  = "landside" (inainte de securitate) / "airside" (dupa securitate)
#   area  = id-ul din ZONES in care e desenat nodul
# Pentru fiecare loc lateral exista un waypoint "wp_<id>" pe coridor, in dreptul lui.
# ---------------------------------------------------------------------------
NODES = {}
for _id, _name, _kind, _zone_id, _s, _side in PLACES:
    NODES[_id] = {"name": _name, "kind": _kind, "zone": _zone_id, "s": _s, "side": _side,
                  "x": s_to_x(_s), "y": CORRIDOR_Y - _side * SPUR_PX, "area": _area(_kind, _zone_id, _s)}
    if _side != 0:
        NODES["wp_" + _id] = {"name": f"Coridor, in dreptul: {_name}", "kind": "waypoint", "zone": _zone_id,
                              "s": _s, "side": 0, "x": s_to_x(_s), "y": CORRIDOR_Y,
                              "area": _area("waypoint", _zone_id, _s)}

# Nodurile de pe coridor, de la intrare spre porti.
CORRIDOR = sorted((n for n, v in NODES.items() if v["side"] == 0), key=lambda n: NODES[n]["s"])

# (de la, la, metri, ambele_sensuri)
EDGES = []
for _a, _b in zip(CORRIDOR, CORRIDOR[1:]):
    # Securitatea: un singur sens. Dupa ea nu exista drum inapoi in zona publica.
    _both = not (_a == "security_in" and _b == "security_out")
    EDGES.append((_a, _b, NODES[_b]["s"] - NODES[_a]["s"], _both))
for _id, _name, _kind, _zone_id, _s, _side in PLACES:
    if _side != 0:
        EDGES.append(("wp_" + _id, _id, SPUR_M, True))

WALK_M_PER_MIN = 60  # pas de pasager cu bagaj de mana

# Locuri pe care le poate cere pasagerul (fara waypoint-uri si fara nodurile tehnice de securitate).
PLACE_KINDS = ("food", "toilet", "shop", "gate", "checkin")


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
# Localizare live: GPS (lat, lon) -> s (metri de la intrare) -> nod pe coridor
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
    """Metri de la intrare, de-a lungul coridorului (proiectie pe axa)."""
    a = GEO_AXIS
    ex = (lon - a["lon0"]) * 111320 * a["k"]
    ny = (lat - a["lat0"]) * 111320
    return ex * a["ux"] + ny * a["uy"]

# ---------------------------------------------------------------------------
# T4 - Date simulate: zboruri, reguli, program
# ---------------------------------------------------------------------------
# SIMULAT: zboruri inventate pentru demo.
FLIGHTS = {
    "W6 1234": {"destination": "Londra Luton", "departure": "07:30", "delay_min": 0, "gate": "gate_1"},
    "W6 5678": {"destination": "Roma", "departure": "09:00", "delay_min": 120, "gate": "gate_2"},
}

RULES = {
    # SIMULAT - de inlocuit cu regula verificata de la Wizz / aeroport (T22)
    "boarding_opens_min_before": 40,
    # SIMULAT - de inlocuit cu regula verificata de la Wizz / aeroport (T22)
    "gate_closes_min_before": 15,
    # Reg. (CE) 261/2004 art. 6: asistenta (masa, racoritoare) de la 2h intarziere
    # pentru zboruri <= 1500 km. DE VERIFICAT pentru fiecare zbor; nu e despagubire in bani.
    "care_after_delay_min": 120,
}

# SIMULAT: program de functionare. Locurile care lipsesc de aici sunt deschise mereu.
OPENING_HOURS = {
    "cafe_public": ("05:00", "22:00"),
    "kfc": ("05:30", "21:00"),
    "shop_duty_free": ("06:00", "20:00"),
}

# ---------------------------------------------------------------------------
# T3 - Starile calatoriei si sesiunea
# ---------------------------------------------------------------------------
STATES = ["ARRIVED", "CHECKED_IN", "THROUGH_SECURITY", "BOARDING"]


def new_session(flight="W6 1234", has_hold_bag=True, now="06:10"):
    return dict(state="ARRIVED", position="entrance", flight=flight, has_hold_bag=has_hold_bag, now=now)


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
        session["position"] = FLIGHTS.get(session["flight"], {}).get("gate", session["position"])
    return {"ok": True, "state": session["state"], "position": session["position"]}


# ---------------------------------------------------------------------------
# Localizare live: s (metri de la intrare, din GPS) -> nod din graf
# ---------------------------------------------------------------------------
# Peste acest s, GPS-ul arata clar zona de dupa control (mijlocul securitatii + 3 m marja).
SECURITY_PASSED_S = (NODES["security_in"]["s"] + NODES["security_out"]["s"]) / 2 + 3


def locate(s, state):
    """Nodul de pe coridor cel mai apropiat de s, tinand cont de etapa calatoriei.

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
    hours = OPENING_HOURS.get(node_id)
    if not hours:
        return True
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

    `to` poate fi un id de nod ("kfc") sau un tip ("toilet", "food", "gate").
    Pentru "gate" se alege poarta zborului pasagerului.
    """
    start = session["position"]
    dist, prev = _dijkstra(start)

    if to == "gate" and session.get("flight") in FLIGHTS:
        to = FLIGHTS[session["flight"]]["gate"]

    if to in NODES:
        target = to
    elif to in PLACE_KINDS:
        candidates = [n for n, v in NODES.items() if v["kind"] == to and n in dist]
        if not candidates:
            return {"reachable": False, "reason": f"Nu exista niciun loc de tipul '{to}' accesibil din pozitia ta."}
        target = min(candidates, key=lambda n: dist[n])
    else:
        return {"reachable": False, "reason": f"Loc necunoscut: '{to}'."}

    if target not in dist:
        reason = "Nu exista drum pana acolo din pozitia ta."
        if NODES[start]["zone"] == "airside" and NODES[target]["zone"] == "landside":
            reason = ("Dupa controlul de securitate nu te mai poti intoarce in zona publica "
                      "(check-in, cafeneaua publica). Pentru probleme cu biletul sau bagajul, "
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
    """Starea zborului pasagerului, cu orele calculate din RULES."""
    code = session.get("flight")
    flight = FLIGHTS.get(code)
    if not flight:
        return {"found": False, "reason": f"Nu am date despre zborul '{code}'. Verifica pe biletul tau sau la ghiseu."}

    now = _to_min(session["now"])
    estimated = _to_min(flight["departure"]) + flight["delay_min"]
    boarding_opens = estimated - RULES["boarding_opens_min_before"]
    gate_closes = estimated - RULES["gate_closes_min_before"]
    return {
        "found": True,
        "flight": code,
        "destination": flight["destination"],
        "scheduled_departure": flight["departure"],
        "delay_min": flight["delay_min"],
        "status": "INTARZIAT" if flight["delay_min"] > 0 else "LA TIMP",
        "estimated_departure": _to_hhmm(estimated),
        "gate": flight["gate"],
        "gate_name": NODES[flight["gate"]]["name"],
        "boarding_opens": _to_hhmm(boarding_opens),
        "gate_closes": _to_hhmm(gate_closes),
        "minutes_until_boarding": boarding_opens - now,
        "minutes_until_gate_closes": gate_closes - now,
        "now": session["now"],
        "care_assistance_possible": flight["delay_min"] >= RULES["care_after_delay_min"],
        "data_note": "Zbor si reguli SIMULATE pentru demo.",
    }


def next_step(session):
    """Ce are de facut pasagerul acum, in functie de stare."""
    state = session["state"]
    fs = flight_status(session)
    gate = fs["gate"] if fs.get("found") else "gate"

    if state == "ARRIVED":
        if session["has_hold_bag"]:
            step, go_to = "Mergi la check-in si preda bagajul de cala.", "checkin"
        else:
            step, go_to = "Fara bagaj de cala: daca ai check-in online, mergi direct la controlul de securitate.", "security_in"
    elif state == "CHECKED_IN":
        step, go_to = "Mergi la controlul de securitate.", "security_in"
    elif state == "THROUGH_SECURITY":
        if fs.get("found") and fs["minutes_until_boarding"] > 0:
            step = f"Ai timp liber pana la imbarcare (se deschide la {fs['boarding_opens']}). Poti manca sau merge la magazin."
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
        "description": ("Listeaza locurile din aeroport (mancare, toalete, magazine, porti, check-in), separate in: "
                        "available_now (accesibile acum), after_security (doar dupa control), not_accessible. "
                        "Include distanta, minute de mers si daca e deschis acum."),
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
        "description": ("Starea zborului pasagerului: intarziere, ora estimata de plecare, poarta, cand se deschide "
                        "imbarcarea, cand se inchide poarta si cate minute mai sunt."),
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
3. Daca o informatie nu exista in rezultatele tool-urilor, spui: "Nu am aceasta informatie, verifica pe biletul tau sau la ghiseul de informatii."
4. Pentru "unde e / cum ajung / unde pot..." apelezi get_route sau list_places. Spui numele locului si cate minute de mers sunt.
5. Daca un loc e in after_security, spui clar ca e disponibil abia dupa controlul de securitate.
6. Dupa controlul de securitate pasagerul NU se mai poate intoarce in zona publica (check-in, cafeneaua publica). Daca get_route spune reachable=false, explici motivul si oferi o alternativa din zona lui.
7. Daca un loc are open_now=false, spui ca e inchis acum si dai programul.
8. Pentru intarzieri: nu promiti despagubiri in bani. Daca care_assistance_possible=true, spui ca pasagerul poate cere asistenta (masa, racoritoare) la ghiseul companiei aeriene.
9. Pentru intrebari care nu au legatura cu aeroportul sau calatoria, raspunzi politicos intr-o propozitie ca poti ajuta doar cu aeroportul, fara sa apelezi tool-uri.
10. Nu pomenesti id-uri tehnice (de ex. "gate_1", "security_out"); folosesti numele din campurile name / target_name / gate_name.
"""


def session_context(session):
    """Text scurt despre sesiune, adaugat de agent.py la fiecare mesaj."""
    pos = NODES.get(session["position"], {}).get("name", session["position"])
    bag = "are bagaj de cala" if session["has_hold_bag"] else "doar bagaj de mana"
    return (f"[Context pasager] Etapa: {session['state']}. Pozitie: {pos}. "
            f"Zbor: {session['flight']}. Ora curenta: {session['now']}. Pasagerul {bag}.")


# ---------------------------------------------------------------------------
# T7 - Autotest (fara Gemini, fara internet)
# ---------------------------------------------------------------------------
def _self_test():
    import json

    # T1: muchiile refera noduri existente
    for a, b, meters, _ in EDGES:
        assert a in NODES and b in NODES, f"muchie cu nod inexistent: {a}->{b}"
        assert meters > 0
    # T2: fiecare nod e desenat in interiorul zonei lui
    for node_id, n in NODES.items():
        z = ZONE_BY_ID[n["area"]]
        assert z["x"] <= n["x"] <= z["x"] + z["w"] and z["y"] <= n["y"] <= z["y"] + z["h"], f"{node_id} in afara zonei {z['id']}"
    # T4: portile zborurilor exista
    for code, f in FLIGHTS.items():
        assert NODES[f["gate"]]["kind"] == "gate", code

    # T5: traseu intrare -> poarta 1 trece prin securitate
    s = new_session()
    r = get_route(s, "gate_1")
    assert r["reachable"] and r["path"][0] == "entrance" and r["path"][-1] == "gate_1"
    i = r["path"].index("security_in")
    assert r["path"][i + 1] == "security_out"
    # cea mai apropiata toaleta de la intrare e cea din holul public
    assert get_route(s, "toilet")["target"] == "toilet_a"
    # "gate" = poarta zborului
    assert get_route(s, "gate")["target"] == "gate_1"
    # bug-ul din plan: KFC nu e "available_now" pentru pasagerul de la intrare
    lp = list_places(s, "food")
    assert [p["id"] for p in lp["available_now"]] == ["cafe_public"]
    assert [p["id"] for p in lp["after_security"]] == ["kfc"]

    # T3: stari
    s = new_session()
    assert not tool_advance_state(s, "THROUGH_SECURITY")["ok"]  # are bagaj de cala
    assert tool_advance_state(s, "CHECKED_IN")["ok"] and s["position"] == "checkin"
    assert tool_advance_state(s, "THROUGH_SECURITY")["ok"] and s["position"] == "security_out"
    assert not tool_advance_state(s, "CHECKED_IN")["ok"]  # nu se merge inapoi
    # dupa securitate: check-in inaccesibil, KFC accesibil
    r = get_route(s, "checkin")
    assert not r["reachable"] and "securitate" in r["reason"]
    lp = list_places(s, "food")
    assert [p["id"] for p in lp["available_now"]] == ["kfc"]
    assert [p["id"] for p in lp["not_accessible"]] == ["cafe_public"]
    assert tool_advance_state(s, "BOARDING")["ok"] and s["position"] == "gate_1"
    # fara bagaj de cala se poate sari peste check-in
    s2 = new_session(has_hold_bag=False)
    assert tool_advance_state(s2, "THROUGH_SECURITY")["ok"]

    # T4/T5: zborul intarziat (eroul demo-ului)
    fs = flight_status(new_session(flight="W6 5678", now="08:00"))
    assert fs["delay_min"] == 120 and fs["estimated_departure"] == "11:00"
    assert fs["boarding_opens"] == "10:20" and fs["minutes_until_boarding"] == 140
    assert fs["care_assistance_possible"]
    assert not flight_status(new_session(flight="XX 0000"))["found"]
    assert next_step(new_session())["go_to"] == "checkin"

    # T6: fiecare tool declarat are implementare, rezultatele sunt JSON
    assert {t["name"] for t in TOOLS} == set(DISPATCH)
    s = new_session()
    for name, fn in DISPATCH.items():
        out = fn(s, "kfc") if name == "get_route" else fn(s)
        json.dumps(out)

    # Geometrie: coridorul are lungimea masurata, iar traseul intrare -> poarta 1 o respecta
    assert get_route(new_session(), "gate_1")["distance_m"] == TERMINAL_LENGTH_M

    # Localizare live: punctele masurate cu S21 cad unde trebuie pe axa
    assert abs(geo_to_s(*GEO["origin"])) < 0.01
    assert abs(geo_to_s(*GEO["axis_end"]) - 67.3) < 0.5
    assert abs(geo_to_s(44.312430, 23.877910) - 25.5) < 1   # "Inainte de securitate"
    assert abs(geo_to_s(44.312400, 23.878082) - 39.1) < 1   # "Dupa securitate"
    assert locate(-5, "ARRIVED")["node"] == "entrance"
    assert locate(10, "ARRIVED")["node"] == "wp_checkin"
    assert locate(25.5, "ARRIVED") == {"node": "security_in", "suggest_security": False}
    # GPS-ul il arata dupa control, dar etapa nu e confirmata: ramane in zona publica si e intrebat
    assert locate(39, "CHECKED_IN") == {"node": "security_in", "suggest_security": True}
    # dupa control, zgomotul GPS nu il intoarce in zona publica
    assert locate(20, "THROUGH_SECURITY") == {"node": "security_out", "suggest_security": False}
    # iPhone-ul a supraestimat (98 m): limitat la capatul coridorului
    assert locate(98, "THROUGH_SECURITY")["node"] == "gate_1"
    s = new_session()
    r = apply_live_position(s, 15)
    assert r["changed"] and s["position"] == "wp_cafe_public"
    assert not apply_live_position(s, 15.5)["changed"]
    assert get_route(s, "gate_1")["reachable"]  # rutele pornesc si dintr-un punct de pe coridor

    print("OK")


if __name__ == "__main__":
    _self_test()
