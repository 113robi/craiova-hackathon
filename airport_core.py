"""Nucleul Airport Copilot: harta ca graf, starea calatoriei, tool-uri, date simulate.

Nu importa nimic din agent.py / map_svg.py / app.py. Toate celelalte depind de el.
Rulare autotest:  python airport_core.py   -> afiseaza OK

ATENTIE: tot ce e marcat SIMULAT trebuie spus explicit in pitch
(coordonate, distante, magazine, program, zboruri, reguli).
"""

import heapq
import math

# ---------------------------------------------------------------------------
# T2 - ZONES: fundalul hartii (dreptunghiuri). Sistem de coordonate 1000 x 600,
# acelasi cu viewBox-ul din map_svg.py. Zona publica in stanga, plecari in dreapta.
# SIMULAT: forma reala a terminalului vine de la C (T21).
# ---------------------------------------------------------------------------
VIEWBOX = (1000, 600)

ZONES = [
    {"id": "landside", "label": "Hol public", "x": 20, "y": 60, "w": 420, "h": 500, "fill": "#e0f2fe"},
    {"id": "checkin_area", "label": "Check-in", "x": 200, "y": 80, "w": 220, "h": 130, "fill": "#bae6fd"},
    {"id": "security_area", "label": "Securitate", "x": 450, "y": 60, "w": 100, "h": 500, "fill": "#fecaca"},
    {"id": "airside", "label": "Zona plecari", "x": 560, "y": 60, "w": 420, "h": 500, "fill": "#dcfce7"},
    {"id": "gate_area", "label": "Porti", "x": 840, "y": 80, "w": 130, "h": 460, "fill": "#fde68a"},
]
ZONE_BY_ID = {z["id"]: z for z in ZONES}

# ---------------------------------------------------------------------------
# T1 - NODES si EDGES
#   x, y  = doar pentru desen (NU se folosesc la distante)
#   kind  = tipul locului; "waypoint" = colt de coridor, nu e loc pentru pasager
#   zone  = "landside" (inainte de securitate) / "airside" (dupa securitate)
#   area  = id-ul din ZONES in care e desenat nodul
# SIMULAT: pozitiile si numele locurilor.
# ---------------------------------------------------------------------------
NODES = {
    # landside
    "entrance":       {"name": "Intrare terminal",       "x": 50,  "y": 480, "kind": "entrance", "zone": "landside", "area": "landside"},
    "wp_hall":        {"name": "Hol central",            "x": 260, "y": 400, "kind": "waypoint", "zone": "landside", "area": "landside"},
    "cafe_public":    {"name": "Cafenea (zona publica)", "x": 110, "y": 300, "kind": "food",     "zone": "landside", "area": "landside"},
    "toilet_a":       {"name": "Toaleta hol public",     "x": 370, "y": 500, "kind": "toilet",   "zone": "landside", "area": "landside"},
    "checkin":        {"name": "Ghisee check-in",        "x": 310, "y": 150, "kind": "checkin",  "zone": "landside", "area": "checkin_area"},
    "security_in":    {"name": "Intrare control securitate", "x": 470, "y": 300, "kind": "security", "zone": "landside", "area": "security_area"},
    # airside
    "security_out":   {"name": "Iesire control securitate", "x": 530, "y": 300, "kind": "security", "zone": "airside", "area": "security_area"},
    "wp_airside":     {"name": "Coridor zona plecari",   "x": 690, "y": 300, "kind": "waypoint", "zone": "airside",  "area": "airside"},
    "kfc":            {"name": "KFC",                    "x": 650, "y": 140, "kind": "food",     "zone": "airside",  "area": "airside"},
    "shop_duty_free": {"name": "Duty Free",              "x": 650, "y": 460, "kind": "shop",     "zone": "airside",  "area": "airside"},
    "toilet_b":       {"name": "Toaleta zona plecari",   "x": 770, "y": 495, "kind": "toilet",   "zone": "airside",  "area": "airside"},
    "gate_1":         {"name": "Poarta 1",               "x": 905, "y": 160, "kind": "gate",     "zone": "airside",  "area": "gate_area"},
    "gate_2":         {"name": "Poarta 2",               "x": 905, "y": 440, "kind": "gate",     "zone": "airside",  "area": "gate_area"},
}

# (de la, la, metri, ambele_sensuri). SIMULAT: distantele vin de la C (T21).
EDGES = [
    ("entrance", "wp_hall", 40, True),
    ("wp_hall", "cafe_public", 25, True),
    ("wp_hall", "toilet_a", 30, True),
    ("wp_hall", "checkin", 45, True),
    ("checkin", "security_in", 50, True),
    ("wp_hall", "security_in", 55, True),
    # Securitatea: un singur sens. Dupa ea nu exista drum inapoi in zona publica.
    ("security_in", "security_out", 20, False),
    ("security_out", "wp_airside", 30, True),
    ("wp_airside", "kfc", 25, True),
    ("wp_airside", "shop_duty_free", 25, True),
    ("wp_airside", "toilet_b", 35, True),
    ("wp_airside", "gate_1", 60, True),
    ("wp_airside", "gate_2", 60, True),
]

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

    print("OK")


if __name__ == "__main__":
    _self_test()
