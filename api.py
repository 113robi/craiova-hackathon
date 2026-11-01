"""API HTTP pentru un frontend separat (aplicatie web in browser): aceleasi functii ca interfata Streamlit
(app.py), expuse ca JSON. Logica ramane in airport_core / agent / ticket; aici sunt doar sesiunile, validarea
datelor venite din browser si limitele.

Rulare:  python api.py                 -> http://127.0.0.1:8000 (doar local; din afara prin tunelul HTTPS)
Variabile de mediu (optionale):
  API_PORT              portul (implicit 8000)
  API_ALLOWED_ORIGINS   originile acceptate de browser (CORS), separate prin virgula; implicit oricare,
                        pentru ca API-ul nu foloseste cookie-uri sau autentificare

Endpoint-uri (JSON; erorile au forma {"error": "mesaj pentru pasager"}):
  GET    /api/health                    {"ok": true}
  GET    /api/terminal                  datele hartii: contururi, locuri, muchii, proiectie, constantele localizarii
  GET    /api/flights                   zborurile cunoscute (date simulate)
  POST   /api/session                   sesiune noua
  GET    /api/session/{id}              starea sesiunii (404 daca a expirat)
  PATCH  /api/session/{id}              {flight?, has_hold_bag?, now?, use_real_time?}
  POST   /api/session/{id}/stage        {state}: etapa urmatoare (doar inainte)
  POST   /api/session/{id}/route        {to}: traseu spre un loc de pe harta
  DELETE /api/session/{id}/route
  POST   /api/session/{id}/position     {s, at}: pozitia calculata in browser (metri de la intrare)
  POST   /api/session/{id}/chat         {message}
  POST   /api/session/{id}/ticket       multipart, campul "image": citeste biletul (inca neconfirmat)
  POST   /api/session/{id}/ticket/confirm
  DELETE /api/session/{id}/ticket
  POST   /api/session/{id}/reset
"""

import asyncio
import datetime as dt
import json
import math
import os
import re
import time
import traceback
import uuid

import uvicorn
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

import airport_core as core
import live_map
from agent import chat
from map_svg import NO_ACCESS_D, PLANES_HINT_SD, SECURITY_D, SHORT_NAMES, WALL_OPENINGS, ZONE_CAPTIONS
from ticket import MAX_IMAGE_MB, describe_ticket, read_ticket

MAX_MESSAGE_CHARS = 300        # la fel ca in app.py
AI_REQUESTS_PER_MIN = 5        # cereri AI (chat + bilet) pe sesiune, intr-un minut
GLOBAL_AI_PER_MIN = 10         # pe tot serverul: protejeaza cota cheii Gemini daca cineva deschide multe sesiuni
MAX_FIX_S_M = 50_000           # o pozitie mai departe de atat de intrare e o valoare invalida
MAX_JSON_BYTES = 16 * 1024
MAX_IMAGE_BYTES = MAX_IMAGE_MB * 1024 * 1024
IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp"}
SESSION_TTL_S = 6 * 3600       # o sesiune nefolosita 6 ore se sterge
MAX_SESSIONS = 500
_SESSION_ID = re.compile(r"[0-9a-f]{32}")
_HHMM = re.compile(r"([01]\d|2[0-3]):[0-5]\d")


class ApiError(Exception):
    def __init__(self, status, error, **extra):
        super().__init__(error)
        self.status, self.error, self.extra = status, error, extra


# ---------------------------------------------------------------------------
# Datele statice: harta si constantele localizarii (aceleasi valori ca in live_map.py)
# ---------------------------------------------------------------------------
def terminal_data():
    nodes = core.NODES
    return {
        "viewbox": list(core.VIEWBOX),
        "projection": {"x0": round(core.MAP_X0, 3), "y0": core.MAP_Y0, "px_per_m": round(core.PX_PER_M, 4),
                       "note": "x = x0 + s*px_per_m ; y = y0 - d*px_per_m ; harta intoarsa: x -> W - x, y -> H - y"},
        "outlines": {"old_hall": core.OUTLINE_OLD_HALL, "toilet_annex": core.OUTLINE_TOILET_ANNEX,
                     "new_terminal": core.OUTLINE_NEW_TERMINAL, "other_building": core.OUTLINE_OTHER},
        "zones": {"security_s": list(core.SECURITY_ZONE_S), "west_no_access_s": core.WEST_NO_ACCESS_S,
                  "draw_s_min": core.DRAW_S_MIN, "draw_s_max": core.DRAW_S_MAX},
        "drawing": {  # restul desenului din map_svg.py, in metri (s, d)
            "no_access": {"s": [core.DRAW_S_MIN - 1, core.WEST_NO_ACCESS_S], "d": list(NO_ACCESS_D),
                          "clip": "old_hall", "label": "fara acces"},
            "security": {"s": list(core.SECURITY_ZONE_S), "d": list(SECURITY_D), "clip": "new_terminal"},
            "wall_openings": [{"from": list(p0), "to": list(p1), "fill": color} for p0, p1, color in WALL_OPENINGS],
            "zone_captions": [{"text": text, "s": s, "d": d} for text, s, d in ZONE_CAPTIONS],
            "planes_hint": {"text": "spre avioane", "s": PLANES_HINT_SD[0], "d": PLANES_HINT_SD[1]},
            "parking_hint": {"text": "parcare, autobuz", "near": "entrance", "offset_px": 72, "offset_px_flipped": -60},
        },
        "nodes": [{"id": n, "name": v["name"], "short_name": SHORT_NAMES.get(n), "kind": v["kind"], "zone": v["zone"],
                   "s": v["s"], "d": v["d"], "x": v["x"], "y": v["y"], "side": v["side"],
                   "clickable": n in core.CLICKABLE, "simulated": v["simulated"]} for n, v in nodes.items()],
        "edges": [{"from": a, "to": b, "meters": m, "both_ways": bw} for a, b, m, bw in core.EDGES],
        "corridor": core.CORRIDOR,
        "states": core.STATES,
        "live": {
            "geo_axis": core.GEO_AXIS, "max_acc": live_map.MAX_ACCURACY_M, "walk_var": live_map.WALK_VARIANCE,
            "gps_gate": live_map.GPS_GATE_SIGMA, "use_steps": live_map.USE_STEPS, "step_m": live_map.STEP_M,
            "step_var": live_map.STEP_VARIANCE, "steps_time_var": live_map.STEPS_TIME_VARIANCE,
            "steps_to_trust": live_map.STEPS_TO_TRUST, "up_bearing": live_map.MAP_UP_BEARING,
            "flip_on": live_map.FLIP_ON_DEG, "flip_off": live_map.FLIP_OFF_DEG,
            "cal_max_m": live_map.CALIBRATION_MAX_M, "snap_m": live_map.SNAP_M,
            "toilet": {**live_map.TOILET_DETECT, "s": nodes["toilet"]["s"], "d": nodes["toilet"]["d"]},
            "thresholds": [core.SECURITY_PASSED_S],
            "s_min_landside": core.WEST_END_S - 2, "s_min_airside": nodes["security_out"]["s"],
            "s_max": core.TERMINAL_LENGTH_M + 3,
            "path": [[nodes[n]["s"], nodes[n]["d"]] for n in core.CORRIDOR],
        },
    }


TERMINAL = terminal_data()

# ---------------------------------------------------------------------------
# Sesiunile (in memorie, ca la Streamlit). Fiecare are un lock: cererile aceleiasi sesiuni nu se amesteca.
# ---------------------------------------------------------------------------
SESSIONS = {}
_global_ai_calls = []


def _new_state():
    return {"core": core.new_session(), "route": [], "highlight": [], "nav_target": None, "suggest_security": False,
            "pending_ticket": None, "use_real_time": True, "ai_calls": [], "seen": time.monotonic(),
            "lock": asyncio.Lock()}


def _evict():
    now = time.monotonic()
    for sid in [k for k, v in SESSIONS.items() if now - v["seen"] > SESSION_TTL_S]:
        del SESSIONS[sid]
    while len(SESSIONS) >= MAX_SESSIONS:
        del SESSIONS[min(SESSIONS, key=lambda k: SESSIONS[k]["seen"])]


def _refresh_clock(sess):
    """Cu biletul confirmat si "ora reala" pornita, numaratoarea foloseste ora serverului (ca in app.py)."""
    c = sess["core"]
    if c.get("ticket") and sess["use_real_time"]:
        c["now"] = dt.datetime.now().strftime("%H:%M")


def _view(sid, sess):
    c = sess["core"]
    return {"session_id": sid, "state": c["state"], "position": c["position"], "flight": c["flight"],
            "has_hold_bag": c["has_hold_bag"], "now": c["now"], "use_real_time": sess["use_real_time"],
            "ticket": c.get("ticket"), "pending_ticket": sess["pending_ticket"],
            "flight_status": core.flight_status(c), "next_step": core.next_step(c),
            "route": sess["route"], "highlight": sess["highlight"], "nav_target": sess["nav_target"],
            "suggest_security": sess["suggest_security"]}


def _ai_wait(sess):
    """0 daca se poate face o cerere AI (si o inregistreaza), altfel cate secunde mai sunt de asteptat."""
    now = time.monotonic()
    mine = [t for t in sess["ai_calls"] if now - t < 60]
    everyone = [t for t in _global_ai_calls if now - t < 60]
    for calls, limit in ((mine, AI_REQUESTS_PER_MIN), (everyone, GLOBAL_AI_PER_MIN)):
        if len(calls) >= limit:
            return max(1, math.ceil(60 - (now - calls[0])))
    sess["ai_calls"] = mine + [now]
    _global_ai_calls[:] = everyone + [now]
    return 0


def _set_route(sess, node_id):
    """Ca navigate_to din app.py: traseul si locul evidentiat vin doar din get_route."""
    r = core.get_route(sess["core"], node_id)
    sess["highlight"] = [node_id]
    sess["nav_target"] = node_id if r.get("reachable") else None
    sess["route"] = r["path"] if r.get("reachable") and r["distance_m"] > 0 else []
    return r


def _refresh_route(sess):
    """Dupa ce pasagerul s-a miscat, traseul spre aceeasi destinatie se recalculeaza."""
    if sess["route"]:
        r = core.get_route(sess["core"], sess["route"][-1])
        ok = r.get("reachable") and r["path"][-1] != sess["core"]["position"]
        sess["route"] = r["path"] if ok else []


async def _json(request):
    """Corpul JSON al cererii, cu limita de marime (citit in bucati, chiar daca lipseste Content-Length)."""
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_JSON_BYTES:
            raise ApiError(413, "Cerere prea mare.")
        chunks.append(chunk)
    if not chunks:
        return {}
    try:
        data = json.loads(b"".join(chunks))
    except ValueError:
        raise ApiError(400, "JSON invalid.") from None
    if not isinstance(data, dict):
        raise ApiError(400, "Corpul cererii trebuie sa fie un obiect JSON.")
    return data


def session_endpoint(handler):
    """Gaseste sesiunea, o blocheaza pe durata cererii, actualizeaza ora si raspunde cu JSON."""
    async def endpoint(request):
        sid = request.path_params["sid"]
        sess = SESSIONS.get(sid) if _SESSION_ID.fullmatch(sid) else None
        if sess is None:
            raise ApiError(404, "Sesiunea nu exista sau a expirat.")
        async with sess["lock"]:
            sess["seen"] = time.monotonic()
            _refresh_clock(sess)
            result = await handler(request, sid, sess)
            return JSONResponse(result if result is not None else _view(sid, sess))
    return endpoint


# ---------------------------------------------------------------------------
# Endpoint-uri
# ---------------------------------------------------------------------------
async def health(request):
    return JSONResponse({"ok": True})


async def terminal(request):
    return JSONResponse(TERMINAL)


async def flights(request):
    return JSONResponse([{"code": k, "destination": f["destination"], "departure": f["departure"],
                          "delay_min": f["delay_min"]} for k, f in core.FLIGHTS.items()])


async def create_session(request):
    _evict()
    sid = uuid.uuid4().hex
    SESSIONS[sid] = _new_state()
    return JSONResponse(_view(sid, SESSIONS[sid]))


@session_endpoint
async def get_session(request, sid, sess):
    return None


@session_endpoint
async def patch_session(request, sid, sess):
    body, c = await _json(request), sess["core"]
    if "flight" in body:
        allowed = set(core.FLIGHTS) | ({c["ticket"]["flight"]} if c.get("ticket") else set())
        if body["flight"] not in allowed:
            raise ApiError(400, "Zbor necunoscut.")
        c["flight"] = body["flight"]
    for key, target in (("has_hold_bag", c), ("use_real_time", sess)):
        if key in body:
            if not isinstance(body[key], bool):
                raise ApiError(400, f"{key} trebuie sa fie true sau false.")
            target[key] = body[key]
    if "now" in body:
        if not isinstance(body["now"], str) or not _HHMM.fullmatch(body["now"]):
            raise ApiError(400, "Ora trebuie sa aiba forma HH:MM.")
        c["now"] = body["now"]
    _refresh_clock(sess)   # cu ora reala pornita, ora trimisa din browser nu se foloseste
    return None


@session_endpoint
async def stage(request, sid, sess):
    state, c = (await _json(request)).get("state"), sess["core"]
    if state not in core.STATES:
        raise ApiError(400, "Etapa necunoscuta.")
    if state == "THROUGH_SECURITY" and c["state"] == "ARRIVED" and sess["suggest_security"]:
        core.tool_advance_state(c, "CHECKED_IN")   # a trecut fizic de control, deci check-in-ul e facut
    res = core.tool_advance_state(c, state)
    if not res["ok"]:
        raise ApiError(400, res["error"])
    sess.update(route=[], highlight=[], nav_target=None, suggest_security=False)
    return None


@session_endpoint
async def route(request, sid, sess):
    to = (await _json(request)).get("to")
    if not isinstance(to, str) or to not in core.CLICKABLE:
        raise ApiError(400, "Loc necunoscut.")
    result = _set_route(sess, to)
    return {"result": result, "session": _view(sid, sess)}


@session_endpoint
async def clear_route(request, sid, sess):
    sess.update(route=[], highlight=[], nav_target=None)
    return None


@session_endpoint
async def position(request, sid, sess):
    body, c = await _json(request), sess["core"]
    s, at = body.get("s"), body.get("at")
    if isinstance(s, bool) or not isinstance(s, (int, float)) or not math.isfinite(s) or abs(s) > MAX_FIX_S_M:
        raise ApiError(400, "Pozitie invalida.")
    moved = core.apply_live_position(c, float(s), at if isinstance(at, str) else None)
    before_security = core.STATES.index(c["state"]) < core.STATES.index("THROUGH_SECURITY")
    sess["suggest_security"] = moved["suggest_security"] and before_security
    if moved["changed"]:
        _refresh_route(sess)
    return None


@session_endpoint
async def chat_message(request, sid, sess):
    message = (await _json(request)).get("message")
    if not isinstance(message, str) or not message.strip():
        raise ApiError(400, "Mesajul e gol.")
    message = message.strip()[:MAX_MESSAGE_CHARS]
    wait = _ai_wait(sess)
    if wait:
        raise ApiError(429, f"Ai trimis multe intrebari intr-un minut. Incearca din nou peste {wait} s. "
                            "Pana atunci, traseele merg si fara chat: apasa pe un loc de pe harta.", retry_after=wait)
    result = await run_in_threadpool(chat, sess["core"], message)
    sess["route"], sess["highlight"] = result["route"], result["highlight"]
    sess["nav_target"] = result["route"][-1] if result["route"] else None
    return {**result, "session": _view(sid, sess)}


@session_endpoint
async def upload_ticket(request, sid, sess):
    length = request.headers.get("content-length", "")
    if not length.isdigit():
        raise ApiError(411, "Lipseste dimensiunea cererii.")
    if int(length) > MAX_IMAGE_BYTES + 64 * 1024:
        raise ApiError(413, f"Poza e prea mare (peste {MAX_IMAGE_MB} MB). Incearca o captura de ecran.")
    async with request.form(max_files=1, max_fields=2) as form:
        upload = form.get("image")
        if upload is None or isinstance(upload, str):
            raise ApiError(400, "Lipseste poza (campul image).")
        if upload.content_type not in IMAGE_TYPES:
            raise ApiError(415, "Accept doar poze PNG, JPG sau WebP.")
        data = await upload.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise ApiError(413, f"Poza e prea mare (peste {MAX_IMAGE_MB} MB). Incearca o captura de ecran.")
    wait = _ai_wait(sess)
    if wait:
        raise ApiError(429, f"Prea multe cereri intr-un minut. Incearca din nou peste {wait} s.", retry_after=wait)
    result = await run_in_threadpool(read_ticket, data)
    if not result["ok"]:
        return {"ok": False, "error": result["error"]}
    sess["pending_ticket"] = result["ticket"]   # devine biletul pasagerului abia dupa confirmare
    return {"ok": True, "ticket": result["ticket"], "summary": describe_ticket(result["ticket"])}


@session_endpoint
async def confirm_ticket(request, sid, sess):
    t, c = sess["pending_ticket"], sess["core"]
    if not t:
        raise ApiError(400, "Nu exista un bilet citit de confirmat.")
    c["ticket"], c["flight"], sess["pending_ticket"] = t, t["flight"], None
    _refresh_clock(sess)
    return None


@session_endpoint
async def delete_ticket(request, sid, sess):
    sess["core"]["ticket"], sess["pending_ticket"] = None, None
    return None


@session_endpoint
async def reset(request, sid, sess):
    fresh = _new_state()
    for key in ("core", "route", "highlight", "nav_target", "suggest_security", "pending_ticket", "use_real_time"):
        sess[key] = fresh[key]   # limita de cereri AI ramane (nu se ocoleste cu "Calatorie noua")
    return None


# ---------------------------------------------------------------------------
# Aplicatia: erori ca JSON, antete de securitate, CORS
# ---------------------------------------------------------------------------
async def on_api_error(request, exc):
    return JSONResponse({"error": exc.error, **exc.extra}, status_code=exc.status)


async def on_http_error(request, exc):
    messages = {404: "Nu exista.", 405: "Metoda nu e permisa."}
    return JSONResponse({"error": messages.get(exc.status_code, "Cerere invalida.")}, status_code=exc.status_code)


async def on_unexpected(request, exc):
    traceback.print_exception(exc)   # detaliile raman in consola serverului
    return JSONResponse({"error": "Eroare interna a serverului."}, status_code=500)


class SecurityHeaders:
    """Raspunsurile API nu se pun in cache si nu se interpreteaza ca alt tip de continut."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                message["headers"] += [(b"x-content-type-options", b"nosniff"), (b"cache-control", b"no-store"),
                                       (b"referrer-policy", b"no-referrer")]
            await send(message)
        await self.app(scope, receive, send_with_headers if scope["type"] == "http" else send)


_origins = [o.strip() for o in os.getenv("API_ALLOWED_ORIGINS", "").split(",") if o.strip()]

app = Starlette(
    routes=[
        Route("/api/health", health),
        Route("/api/terminal", terminal),
        Route("/api/flights", flights),
        Route("/api/session", create_session, methods=["POST"]),
        Route("/api/session/{sid}", get_session, methods=["GET"]),
        Route("/api/session/{sid}", patch_session, methods=["PATCH"]),
        Route("/api/session/{sid}/stage", stage, methods=["POST"]),
        Route("/api/session/{sid}/route", route, methods=["POST"]),
        Route("/api/session/{sid}/route", clear_route, methods=["DELETE"]),
        Route("/api/session/{sid}/position", position, methods=["POST"]),
        Route("/api/session/{sid}/chat", chat_message, methods=["POST"]),
        Route("/api/session/{sid}/ticket", upload_ticket, methods=["POST"]),
        Route("/api/session/{sid}/ticket", delete_ticket, methods=["DELETE"]),
        Route("/api/session/{sid}/ticket/confirm", confirm_ticket, methods=["POST"]),
        Route("/api/session/{sid}/reset", reset, methods=["POST"]),
    ],
    middleware=[
        Middleware(SecurityHeaders),
        Middleware(CORSMiddleware, allow_origins=_origins or ["*"], allow_methods=["GET", "POST", "PATCH", "DELETE"],
                   allow_headers=["Content-Type"], max_age=600),
    ],
    exception_handlers={ApiError: on_api_error, HTTPException: on_http_error, Exception: on_unexpected},
)


# ---------------------------------------------------------------------------
# Autotest (fara Gemini, fara internet):  python api.py test
# ---------------------------------------------------------------------------
def _self_test():
    global chat, read_ticket
    from starlette.testclient import TestClient

    ticket = {"flight": "FR 1234", "date": None, "from": "CRA", "to": "BGY", "destination": "Milano",
              "departure": "23:50", "boarding": "23:10", "gate_closes": "23:35", "seat": "12A"}
    chat = lambda session, message: {"text": "ok", "route": core.get_route(session, "kfc")["path"],  # noqa: E731
                                     "highlight": ["kfc"]}
    read_ticket = lambda data: {"ok": True, "ticket": ticket}  # noqa: E731
    c = TestClient(app)

    assert c.get("/api/health").json() == {"ok": True}
    assert c.get("/api/terminal").json()["live"]["flip_on"] == live_map.FLIP_ON_DEG
    sid = c.post("/api/session").json()["session_id"]
    url = f"/api/session/{sid}"
    assert c.get("/api/session/" + "0" * 32).status_code == 404
    # Traseu doar spre locurile de pe harta; datele gresite din browser sunt refuzate
    assert c.post(f"{url}/route", json={"to": "kfc"}).json()["session"]["nav_target"] == "kfc"
    assert c.post(f"{url}/route", json={"to": "wp_info"}).status_code == 400
    for bad in ({"s": "abc"}, {"s": True}, {"s": 1e9}, {}):
        assert c.post(f"{url}/position", json=bad).status_code == 400
    assert c.post(f"{url}/position", content=b"x" * (MAX_JSON_BYTES + 1)).status_code == 413
    # Etapele: cu bagaj de cala nu se sare peste check-in; confirmarea din GPS face si check-in-ul
    assert c.post(f"{url}/stage", json={"state": "THROUGH_SECURITY"}).status_code == 400
    assert c.post(f"{url}/position", json={"s": 60, "at": None}).json()["suggest_security"]
    s = c.post(f"{url}/stage", json={"state": "THROUGH_SECURITY"}).json()
    assert s["state"] == "THROUGH_SECURITY" and not s["suggest_security"]
    assert not c.post(f"{url}/route", json={"to": "checkin"}).json()["result"]["reachable"]
    # Chat: limita de 5 cereri AI pe minut pe sesiune
    codes = [c.post(f"{url}/chat", json={"message": "Unde e KFC?"}).status_code for _ in range(6)]
    assert codes == [200] * 5 + [429], codes
    # Biletul devine al pasagerului abia dupa confirmare
    sid2 = c.post("/api/session").json()["session_id"]
    url2 = f"/api/session/{sid2}"
    assert c.post(f"{url2}/ticket", files={"image": ("b.gif", b"GIF89a", "image/gif")}).status_code == 415
    assert c.post(f"{url2}/ticket", files={"image": ("b.png", b"x", "image/png")}).json()["ok"]
    assert c.get(url2).json()["ticket"] is None
    assert c.post(f"{url2}/ticket/confirm").json()["flight"] == "FR 1234"
    assert c.post(f"{url2}/reset").json()["state"] == "ARRIVED"
    print("OK")


if __name__ == "__main__":
    import sys

    if sys.argv[1:] == ["test"]:
        _self_test()
    else:
        uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("API_PORT", "8000")))
