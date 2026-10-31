"""Harta cu localizare live (GPS din telefon) ca o componenta Streamlit.

Ce face in browser (JavaScript):
  1. porneste localizarea doar dupa acordul pasagerului (buton) sau daca acordul exista deja;
  2. transforma (lat, lon) in s = metri de la intrare de-a lungul holului si d = pozitia laterala,
     cu aceeasi formula ca airport_core.geo_to_s;
  3. ignora citirile slabe (precizie > max_acc) si netezeste restul cu filtre Kalman 1D (pe s si pe d);
  4. deseneaza punctul "Esti aici" pe DRUMUL REAL (care se curbeaza), la distanta s de-a lungul lui.
     GPS-ul lateral e prea imprecis in interior ca sa fie folosit direct; de aceea punctul se muta
     lateral doar cand stim sigur unde e pasagerul:
       - a ajuns la locul spre care are traseu (la cel mult snap_m metri de-a lungul holului);
       - o deplasare laterala mare langa capatul din stanga = a intrat pe holul spre toaleta
         (doar dupa "Sunt la intrare", care fixeaza si referinta laterala);
  5. intoarce harta dupa busola telefonului. Harta e desenata cu intrarea jos (aproape nordul sus).
     Pasagerul cu fata spre intrare / parcare (spre sud) o vedea in oglinda: mergea spre dreapta,
     iar punctul pleca spre stanga. Cand telefonul e indreptat spre sud, se arata harta rotita cu
     180 de grade (desenata tot de map_svg.py, cu textele drepte). Un con albastru arata incotro e
     indreptat telefonul. Butonul "Intoarce harta" o roteste manual (telefon fara busola, laptop);
  6. trimite pozitia la Python DOAR cand se schimba nodul cel mai apropiat, pragul de dupa securitate
     sau locul in care a ajuns - ca aplicatia sa nu se reincarce continuu;
  7. "Sunt la intrare": corecteaza abaterea GPS din ziua respectiva (pozitia curenta devine intrarea);
  8. click (sau Enter) pe un loc de pe harta -> trimite id-ul locului la Python, care deseneaza traseul.

Contract:
    live_map(svg, state, target=None, key="live_map") -> {"fix": dict | None, "go": str | None}
        fix = {"s": metri de la intrare, "acc": precizie, "t": timestamp ms, "at": id loc sau None}
        go  = id-ul locului apasat pe harta (doar in rerun-ul declansat de click)

Localizarea si busola merg doar pe HTTPS sau pe http://localhost (regula browserelor).
"""

import math

import streamlit as st

from airport_core import (CORRIDOR, GEO_AXIS, MAP_X0, MAP_Y0, NODES, PX_PER_M, SECURITY_PASSED_S, STATES,
                          TERMINAL_LENGTH_M, VIEWBOX, WEST_END_S)

MAX_ACCURACY_M = 40     # citirile mai slabe sunt ignorate (iPhone-ul a avut si 50 m in test)
WALK_VARIANCE = 9       # m^2/s pentru filtrul Kalman: ~3 m/s, mai repede decat merge un pasager
CALIBRATION_MAX_M = 20  # "Sunt la intrare" apare doar cand GPS-ul te arata la cel mult 20 m de intrare
SNAP_M = 3              # la cel mult 3 m (de-a lungul holului) de tinta traseului, punctul intra in loc
# Holul spre toaleta: deplasare laterala de cel putin 10 m fata de intrare, langa capatul din stanga.
# In masuratori, mersul pe hol a variat cu +-5 m lateral, iar drumul spre toaleta a ajuns la +18 m.
TOILET_DETECT = {"id": "toilet", "s_min": -16, "s_max": 3, "rel_d": 10}

# "Sus"-ul hartii: perpendiculara pe axa holului, spre peretele opus intrarii / pista.
# In grade fata de nord, in sensul acelor de ceas (~358.5: harta e aproape cu nordul sus).
MAP_UP_BEARING = round(math.degrees(math.atan2(-GEO_AXIS["uy"], GEO_AXIS["ux"])) % 360, 1)
# Harta se intoarce cand telefonul e la peste 90 + 35 de grade fata de "sus"-ul hartii si revine sub 90 - 35.
# Cat timp pasagerul merge de-a lungul holului (telefonul spre est sau vest), harta nu se schimba,
# chiar daca busola greseste cu 30 de grade langa structurile de metal.
FLIP_MARGIN_DEG = 35

_CSS = """
.acm-root { display: flex; flex-direction: column; gap: 8px; }
.acm-map svg { display: block; width: 100%; height: auto; }
.acm-map .acm-turn { animation: acm-turn .45s ease-in-out; transform-box: view-box; transform-origin: 50% 50%; }
@keyframes acm-turn { from { transform: rotate(-180deg); } }
@media (prefers-reduced-motion: reduce) { .acm-map .acm-turn { animation: none; } }
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
    watchId: null, kf: null, kd: null, est: null, raw: null, status: "idle", error: "", sentKey: null,
    subs: new Set(), send: null, data: null, ticker: null, offset: undefined, dref: undefined,
  });
  // Orientarea hartii (busola). Separat, ca sa mearga si cu un G creat de o versiune mai veche a paginii.
  if (!G.orient) {
    G.orient = { mode: "auto", flipped: false, compass: "off", granted: false, listener: null, event: null,
                 heading: null, headingT: 0, hx: null, hy: null, since: null, drawn: null, drawnAt: 0, turnT: 0 };
    try {  // alegerea manuala ("Intoarce harta") ramane valabila daca reincarci pagina
      const o = JSON.parse(sessionStorage.getItem("acm-orient") || "null");
      if (o && o.mode === "manual") { G.orient.mode = "manual"; G.orient.flipped = !!o.flipped; }
    } catch {}
  }
  const O = G.orient;
  G.send = setStateValue;
  G.data = data;
  if (G.offset === undefined) {   // corectia "Sunt la intrare" ramane valabila daca reincarci pagina
    try { G.offset = parseFloat(sessionStorage.getItem("acm-offset")) || 0; } catch { G.offset = 0; }
  }
  if (G.dref === undefined) {     // referinta laterala, fixata tot la "Sunt la intrare"
    try { const v = parseFloat(sessionStorage.getItem("acm-dref")); G.dref = Number.isFinite(v) ? v : null; }
    catch { G.dref = null; }
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
    '<button class="acm-btn acm-flip" type="button">↻ Intoarce harta</button>' +
    '<button class="acm-btn acm-auto" type="button" hidden>\u{1F9ED} Roteste dupa busola</button>' +
    '<button class="acm-btn acm-toggle" type="button"></button></div>';
  root.querySelector(".acm-map").innerHTML = data.svg;  // SVG generat de map_svg.py (text escapat acolo)
  const statusEl = root.querySelector(".acm-status");
  const btn = root.querySelector(".acm-toggle");
  const calBtn = root.querySelector(".acm-cal");
  const flipBtn = root.querySelector(".acm-flip");
  const autoBtn = root.querySelector(".acm-auto");

  btn.onclick = () => (G.watchId === null ? start(true) : stop());
  calBtn.onclick = calibrate;
  // Manual: pentru telefoane fara busola, laptop sau o busola derutata de structurile de metal.
  flipBtn.onclick = () => {
    O.mode = "manual"; O.flipped = !O.flipped; O.since = null; O.turnT = Date.now();
    saveOrient();
    notify();
  };
  autoBtn.onclick = () => {
    O.mode = "auto"; O.since = null;
    saveOrient();
    startCompass(true);   // pe iPhone, acordul pentru busola se poate cere doar la o apasare
    notify();
  };

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
      .then((p) => { if (p.state === "granted" && G.watchId === null) start(false); })
      .catch(() => {});
  }
  render();
  return () => G.subs.delete(sub);

  // ------------------------------------------------------------------
  // `gesture` = pornit dintr-o apasare pe buton (iPhone cere acordul pentru busola doar atunci).
  function start(gesture) {
    if (!window.isSecureContext) { G.status = "error"; G.error = "Localizarea merge doar pe o adresa https://."; return notify(); }
    if (!("geolocation" in navigator)) { G.status = "error"; G.error = "Browserul nu suporta localizarea."; return notify(); }
    G.status = "waiting"; G.error = "";
    G.watchId = navigator.geolocation.watchPosition(onFix, onErr,
      { enableHighAccuracy: true, maximumAge: 0, timeout: 20000 });
    startCompass(gesture);
    notify();
  }

  function stop() {
    if (G.watchId !== null) navigator.geolocation.clearWatch(G.watchId);
    G.watchId = null; G.kf = null; G.kd = null; G.est = null; G.status = "idle"; G.sentKey = null;
    stopCompass();
    notify();
  }

  // Pasagerul sta la intrare: estimarea curenta devine s = 0 (corectie pentru toate citirile urmatoare),
  // iar pozitia laterala de acum devine referinta pentru detectarea drumului spre toaleta.
  function calibrate() {
    if (!G.kf) return;
    G.offset = (G.offset || 0) - G.kf.s;
    G.dref = G.kd ? G.kd.s : null;
    try {
      sessionStorage.setItem("acm-offset", String(G.offset));
      sessionStorage.setItem("acm-dref", String(G.dref));
    } catch {}
    const now = Date.now();
    G.kf = { ...G.kf, s: 0, t: now };
    G.est = { ...G.est, s: 0, t: now };
    G.sentKey = null;
    maybeSend();
    notify();
  }

  function toSD(lat, lon) {
    const a = G.data.axis;
    const ex = (lon - a.lon0) * 111320 * a.k;
    const ny = (lat - a.lat0) * 111320;
    return { s: ex * a.ux + ny * a.uy + (G.offset || 0), d: -ex * a.uy + ny * a.ux };
  }

  // Filtru Kalman 1D: citirile precise conteaza mai mult, iar dupa o pauza
  // (pasagerul poate merge ~3 m/s) estimarea se muta repede la citirea noua.
  function kalman(prev, z, R, now, q) {
    if (!prev) return { s: z, P: R, t: now };
    const P = prev.P + q * Math.max(0, (now - prev.t) / 1000), K = P / (P + R);
    const next = { s: prev.s + K * (z - prev.s), P: (1 - K) * P, t: now };
    return Number.isFinite(next.s) ? next : { s: z, P: R, t: now };  // nu lasam filtrul sa se blocheze
  }

  function onFix(p) {
    const d = G.data, acc = p.coords.accuracy, now = Date.now();
    G.raw = { acc, t: now };
    if (acc > d.max_acc) { G.status = "weak"; return notify(); }
    G.status = "live"; G.error = "";
    const z = toSD(p.coords.latitude, p.coords.longitude), R = Math.max(acc, 3) ** 2;
    if (!Number.isFinite(z.s) || !Number.isFinite(z.d)) return;
    const q = Number.isFinite(d.walk_var) ? d.walk_var : 9;
    G.kf = kalman(G.kf, z.s, R, now, q);
    G.kd = kalman(G.kd, z.d, R, now, q);
    G.est = { s: G.kf.s, d: G.kd.s, acc, t: now };
    maybeSend();
    notify();
  }

  function onErr(e) {
    if (e.code === 1) { stop(); G.status = "error"; G.error = "Accesul la locatie e blocat. Permite-l din setarile browserului pentru acest site."; }
    else if (e.code === 3) { G.status = G.est ? G.status : "waiting"; }
    else { G.status = "error"; G.error = "Pozitia nu e disponibila. Porneste Locatia si Wi-Fi-ul telefonului."; }
    notify();
  }

  // ------------------------------------------------------------------
  // Busola: incotro e indreptat telefonul. Merge doar cat timp e pornita localizarea live.
  function startCompass(gesture) {
    const DOE = window.DeviceOrientationEvent;
    if (!DOE || !window.isSecureContext) { O.compass = "none"; return; }
    if (O.compass === "on" || O.compass === "denied") return;   // Safari nu mai intreaba pana la reincarcare
    const ask = typeof DOE.requestPermission === "function";
    // iPhone (Safari): busola trimite date doar dupa acordul dat la o apasare pe buton.
    // Chrome are si el requestPermission, dar trimite directia si fara (evenimentul "absolute").
    const needsTap = ask && !("ondeviceorientationabsolute" in window) && !O.granted;
    if (needsTap && !gesture) { O.compass = "ask"; return; }   // apare butonul "Roteste dupa busola"
    listen();
    if (ask && gesture && !O.granted) {
      DOE.requestPermission()
        .then((r) => {
          if (r === "granted") O.granted = true;
          else if (needsTap) { unlisten(); O.compass = "denied"; }
          notify();
        })
        .catch(() => { if (needsTap) { unlisten(); O.compass = "ask"; } notify(); });
    }
  }

  function listen() {
    // Android da directia fata de nord doar in "deviceorientationabsolute"; iPhone o da in webkitCompassHeading.
    if (!O.listener) {
      O.event = "ondeviceorientationabsolute" in window ? "deviceorientationabsolute" : "deviceorientation";
      O.listener = onOrient;
      window.addEventListener(O.event, O.listener);
    }
    O.compass = "on";
  }

  function unlisten() {
    if (O.listener) window.removeEventListener(O.event, O.listener);
    O.listener = null;
  }

  function stopCompass() {
    unlisten();
    if (O.compass === "on" || O.compass === "ask") O.compass = "off";
    O.heading = null; O.hx = null; O.hy = null; O.since = null;
    if (O.mode === "auto" && O.flipped) { O.flipped = false; O.turnT = Date.now(); }  // inapoi la harta obisnuita
  }

  // Directia in care se uita pasagerul, in grade fata de nord (in sensul acelor de ceas).
  // Android: din orientarea absoluta a telefonului, ca suma dintre "sus"-ul ecranului si spatele telefonului
  // proiectata pe orizontala - merge si cu telefonul culcat, si cu el tinut vertical.
  function headingFrom(e) {
    if (typeof e.webkitCompassHeading === "number" && e.webkitCompassHeading >= 0) return e.webkitCompassHeading;
    if (!e.absolute || typeof e.alpha !== "number") return null;
    const r = Math.PI / 180, a = e.alpha * r, b = (e.beta || 0) * r, g = (e.gamma || 0) * r;
    const sa = Math.sin(a), ca = Math.cos(a), sb = Math.sin(b), cb = Math.cos(b), sg = Math.sin(g), cg = Math.cos(g);
    const vx = -sa * cb - ca * sg - sa * sb * cg, vy = ca * cb - sa * sg + ca * sb * cg;
    if (Math.hypot(vx, vy) < 0.2) return null;   // telefon cu ecranul in jos: directia nu e clara
    return (Math.atan2(vx, vy) / r + 360) % 360;
  }

  function onOrient(e) {
    const h = headingFrom(e);
    if (h === null || !Number.isFinite(h)) return;
    const so = screen.orientation && Number.isFinite(screen.orientation.angle) ? screen.orientation.angle
             : Number(window.orientation) || 0;   // telefonul tinut orizontal
    // Media pe cerc (sin, cos): netezeste tremurul busolei fara salt intre 359 si 0 grade.
    const r = (h + so) * Math.PI / 180, k = O.hx === null ? 1 : 0.2;
    O.hx = (O.hx ?? 0) + k * (Math.sin(r) - (O.hx ?? 0));
    O.hy = (O.hy ?? 0) + k * (Math.cos(r) - (O.hy ?? 0));
    O.heading = (Math.atan2(O.hx, O.hy) * 180 / Math.PI + 360) % 360;
    O.headingT = Date.now();
    const turned = O.mode === "auto" && decideFlip();
    // redesenam conul de cel mult 10 ori pe secunda (evenimentele vin de ~60 de ori pe secunda)
    if (turned || (O.headingT - O.drawnAt > 100 && (O.drawn === null || Math.abs(angleDiff(O.heading, O.drawn)) > 3))) {
      notify();
    }
  }

  // Harta intoarsa = telefonul indreptat spre jumatatea "de jos" a hartii (spre intrare / parcare).
  // Prag cu histerezis + 0.8 s de confirmare, ca harta sa nu se roteasca de la tremurul busolei.
  function decideFlip() {
    const off = Math.abs(angleDiff(O.heading, G.data.up_bearing)), m = G.data.flip_margin;
    const want = O.flipped ? off > 90 - m : off > 90 + m;
    if (want === O.flipped) { O.since = null; return false; }
    const now = Date.now();
    if (O.since === null) O.since = now;
    if (now - O.since < 800) return false;
    O.flipped = want; O.since = null; O.turnT = now;
    return true;
  }

  function angleDiff(a, b) { return ((a - b) % 360 + 540) % 360 - 180; }   // in (-180, 180]

  function saveOrient() {
    try { sessionStorage.setItem("acm-orient", JSON.stringify({ mode: O.mode, flipped: O.flipped })); } catch {}
  }

  // ------------------------------------------------------------------
  // Pozitia laterala a drumului real la distanta s (interpolare intre punctele drumului).
  function pathD(s) {
    const p = G.data.path;
    if (s <= p[0][0]) return p[0][1];
    for (let i = 1; i < p.length; i++) {
      if (s <= p[i][0]) {
        const [s0, d0] = p[i - 1], [s1, d1] = p[i];
        return d0 + (d1 - d0) * (s - s0) / (s1 - s0);
      }
    }
    return p[p.length - 1][1];
  }

  // Unde desenam pasagerul: in locul in care a ajuns, sau pe drumul real.
  function spot() {
    const d = G.data, s = Math.min(Math.max(G.est.s, d.s_min), d.s_max);
    const t = d.target;
    if (t && Math.abs(G.est.s - t.s) <= d.snap_m) return { s: t.s, dd: t.d, at: t.id };
    const w = d.toilet;
    if (w && !d.passed && G.dref !== null && G.est.s >= w.s_min && G.est.s <= w.s_max &&
        G.est.d - G.dref >= w.rel_d) return { s: w.s, dd: w.d, at: w.id };
    return { s, dd: pathD(s), at: null };
  }

  function maybeSend() {
    const d = G.data, s = G.est.s, sp = spot();
    let best = d.corridor[0];
    for (const n of d.corridor) if (Math.abs(n.s - s) < Math.abs(best.s - s)) best = n;
    const key = best.id + "|" + d.thresholds.map((t) => (s >= t ? 1 : 0)).join("") + "|" + (sp.at || "");
    if (key === G.sentKey || !G.send) return;
    G.sentKey = key;
    G.send("fix", { s: Math.round(s * 10) / 10, acc: Math.round(G.est.acc), t: G.est.t, at: sp.at });
  }

  function notify() { G.subs.forEach((f) => f()); }

  function render() {
    const d = G.data, now = Date.now(), live = root.querySelector("g.acm-live");
    const fresh = G.est && now - G.est.t < 30000;
    // Orientarea: SVG-ul are harta in ambele orientari; o aratam pe cea potrivita.
    // Imediat dupa o intoarcere, harta (si punctul) se rotesc vizibil, ca pasagerul sa inteleaga ce s-a schimbat.
    const turning = now - O.turnT < 500;
    root.querySelectorAll("g.acm-orient").forEach((g) => {
      const show = (g.getAttribute("data-flip") === "1") === O.flipped;
      g.setAttribute("display", show ? "inline" : "none");
      g.classList.toggle("acm-turn", show && turning);
    });
    if (live) {
      live.classList.toggle("acm-turn", turning);
      live.replaceChildren();
      if (fresh) {
        const sp = spot();
        let x = d.x0 + sp.s * d.px, y = d.y0 - sp.dd * d.px;
        if (O.flipped) { x = d.W - x; y = d.H - y; }   // aceeasi rotire ca in map_svg.py
        live.append(el("circle", { cx: x, cy: y, r: Math.min(G.est.acc * d.px, 420), fill: "#2563eb", "fill-opacity": 0.12,
                                   stroke: "#2563eb", "stroke-opacity": 0.35, "stroke-width": 2, "pointer-events": "none" }));
        if (O.heading !== null && now - O.headingT < 3000) {
          // Conul: incotro e indreptat telefonul (desenat in sus, rotit cu unghiul fata de "sus"-ul hartii).
          const ang = angleDiff(O.heading, d.up_bearing) + (O.flipped ? 180 : 0);
          live.append(el("path", { d: `M${x} ${y} L${x - 26} ${y - 62} A67 67 0 0 1 ${x + 26} ${y - 62} Z`,
                                   fill: "#2563eb", "fill-opacity": 0.3, transform: `rotate(${ang.toFixed(1)} ${x} ${y})`,
                                   "pointer-events": "none" }));
        }
        live.append(
          el("circle", { cx: x, cy: y, r: 14, fill: "#dc2626", stroke: "#ffffff", "stroke-width": 3, "pointer-events": "none" }),
          el("text", { x, y: y - 26, "font-size": 22, "font-weight": 700, "text-anchor": "middle", fill: "#dc2626",
                       "paint-order": "stroke", stroke: "#ffffff", "stroke-width": 5, "pointer-events": "none" }, "Esti aici"),
        );
      }
    }
    O.drawn = O.heading; O.drawnAt = now;
    root.querySelectorAll("g.acm-static-pos").forEach((g) => { g.style.display = fresh ? "none" : ""; });

    const ago = (t) => Math.max(0, Math.round((now - t) / 1000));
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
    flipBtn.setAttribute("aria-pressed", String(O.flipped));
    // "Roteste dupa busola": pe iPhone (acordul se cere la o apasare) sau dupa ce harta a fost intoarsa manual
    autoBtn.hidden = !(G.watchId !== null && (O.compass === "ask" || (O.mode === "manual" && O.compass === "on")));
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


def live_map(svg: str, state: str, target: str | None = None, key: str = "live_map") -> dict:
    """`target` = locul spre care pasagerul are traseu (punctul intra in el cand ajunge)."""
    passed = STATES.index(state) >= STATES.index("THROUGH_SECURITY")
    toilet = NODES[TOILET_DETECT["id"]]
    data = {
        "svg": svg,
        "axis": GEO_AXIS,
        "x0": MAP_X0,
        "y0": MAP_Y0,
        "px": PX_PER_M,
        "W": VIEWBOX[0],   # harta intoarsa: x -> W - x, y -> H - y (ca in map_svg.py)
        "H": VIEWBOX[1],
        "up_bearing": MAP_UP_BEARING,
        "flip_margin": FLIP_MARGIN_DEG,
        "path": [[NODES[n]["s"], NODES[n]["d"]] for n in CORRIDOR],
        "corridor": [{"id": n, "s": NODES[n]["s"]} for n in CORRIDOR],
        "thresholds": [SECURITY_PASSED_S],
        # dupa securitate punctul nu se mai deseneaza in zona publica
        "s_min": NODES["security_out"]["s"] if passed else WEST_END_S - 2,
        "s_max": TERMINAL_LENGTH_M + 3,
        "max_acc": MAX_ACCURACY_M,
        "walk_var": WALK_VARIANCE,
        "passed": passed,
        "cal_max_m": CALIBRATION_MAX_M,
        "snap_m": SNAP_M,
        "target": ({"id": target, "s": NODES[target]["s"], "d": NODES[target]["d"]}
                   if target in NODES else None),
        "toilet": {**TOILET_DETECT, "s": toilet["s"], "d": toilet["d"]},
    }
    result = _get_component()(key=key, data=data, default={"fix": None},
                              on_fix_change=lambda: None, on_go_change=lambda: None)
    get = result.get if hasattr(result, "get") else (lambda k: getattr(result, k, None))
    return {"fix": get("fix"), "go": get("go")}
