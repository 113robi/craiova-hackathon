"""Harta cu localizare live (GPS din telefon) ca o componenta Streamlit.

Ce face in browser (JavaScript):
  1. porneste localizarea doar dupa acordul pasagerului (buton) sau daca acordul exista deja;
  2. transforma (lat, lon) in s = metri de la intrare, cu aceeasi formula ca airport_core.geo_to_s;
  3. ignora citirile slabe (precizie > max_acc) si netezeste restul cu un filtru Kalman 1D;
  4. muta punctul "Esti aici" pe harta la fiecare citire (fara Python, fara reincarcare);
  5. trimite pozitia la Python DOAR cand se schimba nodul cel mai apropiat de pe axa holului
     sau cand se trece pragul de dupa securitate - ca aplicatia sa nu se reincarce continuu;
  6. "Sunt la intrare": corecteaza abaterea GPS din ziua respectiva (pozitia curenta devine intrarea);
  7. click (sau Enter) pe un loc de pe harta -> trimite id-ul locului la Python, care deseneaza traseul.

Contract:
    live_map(svg, state, key="live_map") -> {"fix": dict | None, "go": str | None}
        fix = {"s": metri de la intrare, "acc": precizie in metri, "t": timestamp ms}
        go  = id-ul locului apasat pe harta (doar in rerun-ul declansat de click)

Localizarea merge doar pe HTTPS sau pe http://localhost (regula browserelor).
"""

import streamlit as st

from airport_core import (CORRIDOR, CORRIDOR_Y, GEO_AXIS, MAP_X0, NODES, PX_PER_M, SECURITY_PASSED_S, STATES,
                          TERMINAL_LENGTH_M, WEST_END_S)

MAX_ACCURACY_M = 40   # citirile mai slabe sunt ignorate (iPhone-ul a avut si 50 m in test)
WALK_VARIANCE = 9     # m^2/s pentru filtrul Kalman: ~3 m/s, mai repede decat merge un pasager
CALIBRATION_MAX_M = 20  # "Sunt la intrare" apare doar cand GPS-ul te arata la cel mult 20 m de intrare

_CSS = """
.acm-root { display: flex; flex-direction: column; gap: 8px; }
.acm-map svg { display: block; width: 100%; height: auto; }
.acm-bar { display: flex; align-items: center; gap: 8px 10px; flex-wrap: wrap;
           font: 14px/1.4 system-ui, "Segoe UI", sans-serif; color: var(--st-text-color, #0f172a); }
.acm-status { flex: 1 1 220px; min-width: 0; }
.acm-status.ok { color: #15803d; } .acm-status.warn { color: #b45309; } .acm-status.err { color: #dc2626; }
.acm-btn { font: inherit; padding: 6px 12px; border-radius: 8px; cursor: pointer;
           border: 1px solid var(--st-primary-color, #ff4b4b); background: transparent;
           color: var(--st-primary-color, #ff4b4b); }
.acm-btn:focus-visible { outline: 3px solid #2563eb; outline-offset: 2px; }
.acm-btn[hidden] { display: none; }
"""

_JS = r"""
const SVGNS = "http://www.w3.org/2000/svg";

export default function (component) {
  const { data, parentElement, setStateValue, setTriggerValue } = component;

  // Starea globala supravietuieste re-randarilor componentei (Streamlit o reapeleaza la fiecare rerun).
  const G = (window.__acmGeo ||= {
    watchId: null, kf: null, est: null, raw: null, status: "idle", error: "", sentKey: null,
    subs: new Set(), send: null, data: null, ticker: null, offset: undefined,
  });
  G.send = setStateValue;
  G.data = data;
  if (G.offset === undefined) {   // corectia "Sunt la intrare" ramane valabila daca reincarci pagina
    try { G.offset = parseFloat(sessionStorage.getItem("acm-offset")) || 0; } catch { G.offset = 0; }
  }

  let root = parentElement.querySelector(".acm-root");
  if (!root) {
    root = document.createElement("div");
    root.className = "acm-root";
    parentElement.appendChild(root);
  }
  root.innerHTML = '<div class="acm-map"></div>' +
    '<div class="acm-bar"><span class="acm-status" role="status"></span>' +
    '<button class="acm-btn acm-cal" type="button" hidden>Sunt la intrare</button>' +
    '<button class="acm-btn acm-toggle" type="button"></button></div>';
  root.querySelector(".acm-map").innerHTML = data.svg;  // SVG generat de map_svg.py (text escapat acolo)
  const statusEl = root.querySelector(".acm-status");
  const btn = root.querySelector(".acm-toggle");
  const calBtn = root.querySelector(".acm-cal");

  btn.onclick = () => (G.watchId === null ? start() : stop());
  calBtn.onclick = calibrate;

  // Locurile de pe harta: click / Enter -> Python primeste id-ul si deseneaza traseul
  root.querySelectorAll(".acm-place").forEach((g) => {
    const go = () => setTriggerValue("go", g.getAttribute("data-node"));
    g.addEventListener("click", go);
    g.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); }
    });
  });

  const sub = () => render();
  if (root.__acmSub) G.subs.delete(root.__acmSub);  // o singura abonare per harta
  root.__acmSub = sub;
  G.subs.add(sub);
  if (!G.ticker) G.ticker = setInterval(() => G.subs.forEach((f) => f()), 1000);

  // Daca pasagerul a dat deja acordul, pornim singuri (fara sa cerem din nou).
  if (G.watchId === null && window.isSecureContext && navigator.permissions) {
    navigator.permissions.query({ name: "geolocation" })
      .then((p) => { if (p.state === "granted" && G.watchId === null) start(); })
      .catch(() => {});
  }
  render();
  return () => G.subs.delete(sub);

  // ------------------------------------------------------------------
  function start() {
    if (!window.isSecureContext) { G.status = "error"; G.error = "Localizarea merge doar pe o adresa https://."; return notify(); }
    if (!("geolocation" in navigator)) { G.status = "error"; G.error = "Browserul nu suporta localizarea."; return notify(); }
    G.status = "waiting"; G.error = "";
    G.watchId = navigator.geolocation.watchPosition(onFix, onErr,
      { enableHighAccuracy: true, maximumAge: 0, timeout: 20000 });
    notify();
  }

  function stop() {
    if (G.watchId !== null) navigator.geolocation.clearWatch(G.watchId);
    G.watchId = null; G.kf = null; G.est = null; G.status = "idle"; G.sentKey = null;
    notify();
  }

  // Pasagerul sta la intrare: estimarea curenta devine s = 0, iar corectia se aplica tuturor citirilor.
  function calibrate() {
    if (!G.kf) return;
    G.offset = (G.offset || 0) - G.kf.s;
    try { sessionStorage.setItem("acm-offset", String(G.offset)); } catch {}
    const now = Date.now();
    G.kf = { ...G.kf, s: 0, t: now };
    G.est = { ...G.est, s: 0, t: now };
    G.sentKey = null;
    maybeSend();
    notify();
  }

  function toS(lat, lon) {
    const a = G.data.axis;
    const ex = (lon - a.lon0) * 111320 * a.k;
    const ny = (lat - a.lat0) * 111320;
    return ex * a.ux + ny * a.uy + (G.offset || 0);
  }

  function onFix(p) {
    const d = G.data, acc = p.coords.accuracy, now = Date.now();
    G.raw = { acc, t: now };
    if (acc > d.max_acc) { G.status = "weak"; return notify(); }
    G.status = "live"; G.error = "";
    // Filtru Kalman 1D: citirile precise conteaza mai mult, iar dupa o pauza
    // (pasagerul poate merge ~3 m/s) estimarea se muta repede la citirea noua.
    const z = toS(p.coords.latitude, p.coords.longitude), R = Math.max(acc, 3) ** 2;
    if (!Number.isFinite(z)) return;
    if (!G.kf) G.kf = { s: z, P: R, t: now };
    else {
      const q = Number.isFinite(d.walk_var) ? d.walk_var : 9;
      const P = G.kf.P + q * Math.max(0, (now - G.kf.t) / 1000);
      const K = P / (P + R);
      G.kf = { s: G.kf.s + K * (z - G.kf.s), P: (1 - K) * P, t: now };
      if (!Number.isFinite(G.kf.s)) G.kf = { s: z, P: R, t: now };  // nu lasam filtrul sa se blocheze
    }
    G.est = { s: G.kf.s, acc, t: now };
    maybeSend();
    notify();
  }

  function onErr(e) {
    if (e.code === 1) { stop(); G.status = "error"; G.error = "Accesul la locatie e blocat. Permite-l din setarile browserului pentru acest site."; }
    else if (e.code === 3) { G.status = G.est ? G.status : "waiting"; }
    else { G.status = "error"; G.error = "Pozitia nu e disponibila. Porneste Locatia si Wi-Fi-ul telefonului."; }
    notify();
  }

  function maybeSend() {
    const d = G.data, s = G.est.s;
    let best = d.corridor[0];
    for (const n of d.corridor) if (Math.abs(n.s - s) < Math.abs(best.s - s)) best = n;
    const key = best.id + "|" + d.thresholds.map((t) => (s >= t ? 1 : 0)).join("");
    if (key === G.sentKey || !G.send) return;
    G.sentKey = key;
    G.send("fix", { s: Math.round(s * 10) / 10, acc: Math.round(G.est.acc), t: G.est.t });
  }

  function notify() { G.subs.forEach((f) => f()); }

  function render() {
    const d = G.data, live = root.querySelector("g.acm-live"), stat = root.querySelector("g.acm-static-pos");
    const fresh = G.est && Date.now() - G.est.t < 30000;
    if (live) {
      live.replaceChildren();
      if (fresh) {
        const s = Math.min(Math.max(G.est.s, d.s_min), d.s_max);
        const x = d.x0 + s * d.px, y = d.y;
        live.append(
          el("circle", { cx: x, cy: y, r: Math.min(G.est.acc * d.px, 420), fill: "#2563eb", "fill-opacity": 0.12,
                         stroke: "#2563eb", "stroke-opacity": 0.35, "stroke-width": 2, "pointer-events": "none" }),
          el("circle", { cx: x, cy: y, r: 14, fill: "#dc2626", stroke: "#ffffff", "stroke-width": 3, "pointer-events": "none" }),
          el("text", { x, y: y - 26, "font-size": 22, "font-weight": 700, "text-anchor": "middle", fill: "#dc2626",
                       "paint-order": "stroke", stroke: "#ffffff", "stroke-width": 5, "pointer-events": "none" }, "Esti aici"),
        );
      }
    }
    if (stat) stat.style.display = fresh ? "none" : "";

    const ago = (t) => Math.max(0, Math.round((Date.now() - t) / 1000));
    const cal = G.offset ? `, calibrat la intrare (${G.offset > 0 ? "+" : ""}${Math.round(G.offset)} m)` : "";
    let cls = "", text = "";
    if (!window.isSecureContext) text = "Localizarea live merge doar pe o adresa https:// (vezi tunelul Cloudflare).";
    else if (G.status === "idle") text = "Localizarea live e oprita. Apasa pe un loc de pe harta ca sa vezi traseul.";
    else if (G.status === "waiting") text = "Caut semnalul de localizare...";
    else if (G.status === "weak") { cls = "warn"; text = `Semnal slab (precizie ${Math.round(G.raw.acc)} m). Astept o citire mai buna...`; }
    else if (G.status === "live" && G.est) {
      cls = fresh ? "ok" : "warn";
      text = fresh ? `Live: precizie ${Math.round(G.est.acc)} m, actualizat acum ${ago(G.est.t)} s${cal}`
                   : `Nicio pozitie noua de ${ago(G.est.t)} s.`;
    } else if (G.status === "error") { cls = "err"; text = G.error; }
    statusEl.className = "acm-status " + cls;
    statusEl.textContent = text;
    btn.textContent = G.watchId === null ? "Porneste localizarea live" : "Opreste localizarea";
    btn.hidden = !window.isSecureContext;
    // "Sunt la intrare" doar langa intrare si inainte de securitate: o apasare din greseala la poarta
    // ar muta intrarea la 67 m si ar strica toata localizarea.
    calBtn.hidden = !(G.watchId !== null && fresh && !d.passed && Math.abs(G.est.s) <= d.cal_max_m);
  }

  function el(tag, attrs, text) {
    const e = document.createElementNS(SVGNS, tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    if (text) e.textContent = text;
    return e;
  }
}
"""

_component = None


def _get_component():
    # Inregistrare o singura data per proces (la import ar esua in afara `streamlit run`).
    global _component
    if _component is None:
        _component = st.components.v2.component("acm_live_map", css=_CSS, js=_JS)
    return _component


def live_map(svg: str, state: str, key: str = "live_map") -> dict:
    passed = STATES.index(state) >= STATES.index("THROUGH_SECURITY")
    data = {
        "svg": svg,
        "axis": GEO_AXIS,
        "x0": MAP_X0,
        "px": PX_PER_M,
        "y": CORRIDOR_Y,
        "corridor": [{"id": n, "s": NODES[n]["s"]} for n in CORRIDOR],
        "thresholds": [SECURITY_PASSED_S],
        # dupa securitate punctul nu se mai deseneaza in zona publica
        "s_min": NODES["security_out"]["s"] if passed else WEST_END_S - 2,
        "s_max": TERMINAL_LENGTH_M + 3,
        "max_acc": MAX_ACCURACY_M,
        "walk_var": WALK_VARIANCE,
        "passed": passed,
        "cal_max_m": CALIBRATION_MAX_M,
    }
    result = _get_component()(key=key, data=data, default={"fix": None},
                              on_fix_change=lambda: None, on_go_change=lambda: None)
    get = result.get if hasattr(result, "get") else (lambda k: getattr(result, k, None))
    return {"fix": get("fix"), "go": get("go")}
