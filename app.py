"""Airport Copilot - interfata Streamlit: harta (live, clickabila) + chat + bilet + etapele calatoriei.

Rulare:  python -m streamlit run app.py
"""

import datetime as dt
import hashlib

import streamlit as st

from agent import chat
from airport_core import (FLIGHTS, NODES, STATES, apply_live_position, flight_status, get_route, new_session,
                          next_step, tool_advance_state)
from live_map import live_map
from map_svg import render_map
from ticket import describe_ticket, read_ticket

st.set_page_config(page_title="Airport Copilot - Craiova", page_icon="✈", layout="wide")

STATE_LABELS = {
    "ARRIVED": "Am ajuns la aeroport",
    "CHECKED_IN": "Check-in facut",
    "THROUGH_SECURITY": "Dupa controlul de securitate",
    "BOARDING": "La imbarcare",
}
STATE_BUTTONS = {
    "CHECKED_IN": "Am facut check-in",
    "THROUGH_SECURITY": "Am trecut de securitate",
    "BOARDING": "Merg la imbarcare",
}
SUGGESTIONS = ["Unde pot manca?", "Unde e toaleta?", "Cat timp am pana la imbarcare?", "Ma pot intoarce la check-in?"]
# Butoanele "Du-ma la" de sub harta (pe telefon locurile de pe harta sunt mici pentru deget)
QUICK_TARGETS = {
    "toilet": "\U0001F6BB Toaleta",
    "checkin": "\U0001F9F3 Check-in",
    "info": "ℹ️ Informatii",
    "cafe": "☕ Cafenea",
    "kfc": "\U0001F357 KFC",
    "gate_1": "✈️ Poarta 1",
    "entrance": "\U0001F6AA Iesire",
}


# ---------------------------------------------------------------------------
# Starea aplicatiei (supravietuieste rerun-urilor Streamlit)
# ---------------------------------------------------------------------------
def reset():
    st.session_state.session = new_session()
    st.session_state.messages = []   # [{"role": "user"|"assistant", "content": str}]
    st.session_state.route = []
    st.session_state.highlight = []
    st.session_state.suggest_security = False  # GPS-ul arata pasagerul dupa control, neconfirmat
    st.session_state.nav_target = None         # locul spre care are traseu (ramane si dupa ce ajunge)
    st.session_state.ticket_result = None      # biletul citit, inca neconfirmat
    st.session_state.ticket_hash = None        # ca aceeasi poza sa nu fie trimisa de doua ori la Gemini
    st.session_state.upload_n = 0              # schimbarea cheii goleste campul de incarcare
    st.session_state.pop("use_real_time", None)


if "session" not in st.session_state:
    reset()

S = st.session_state.session


def say(text):
    st.session_state.messages.append({"role": "assistant", "content": text})


def real_time_on():
    return bool(S.get("ticket")) and st.session_state.get("use_real_time", True)


def clock_now():
    return dt.datetime.now().strftime("%H:%M")


def flight_summary(fs):
    """Starea zborului intr-o propozitie, din flight_status (fara AI)."""
    mins = fs["minutes_until_boarding"]
    when = f"peste {mins} min" if mins > 0 else "deja deschisa"
    text = (f"**Zborul {fs['flight']} spre {fs['destination']}**: plecare {fs['estimated_departure']}, "
            f"imbarcarea se deschide la {fs['boarding_opens']} ({when}), poarta se inchide la {fs['gate_closes']}.")
    if (fs["delay_min"] or 0) > 0:
        text = (f"**Zborul {fs['flight']} spre {fs['destination']} este intarziat {fs['delay_min']} min.** "
                f"Plecare estimata {fs['estimated_departure']} (programat {fs['scheduled_departure']}), "
                f"imbarcarea se deschide la {fs['boarding_opens']} ({when}).")
        if fs["care_assistance_possible"]:
            text += " Poti cere asistenta (masa, racoritoare) la ghiseul companiei."
    elif fs["delay_min"] is None:
        text += " Nu avem date live despre intarzieri."
    return text


def navigate_to(node_id):
    """Traseu spre un loc apasat pe harta sau ales din butoane (fara AI)."""
    if node_id not in NODES:
        return
    name = NODES[node_id]["name"]
    st.session_state.messages.append({"role": "user", "content": f"Du-ma la: {name}"})
    r = get_route(S, node_id)
    st.session_state.nav_target = node_id if r.get("reachable") else None
    if r.get("reachable") and r["distance_m"] == 0:
        st.session_state.route, st.session_state.highlight = [], [node_id]
        say(f"Esti deja la **{name}**.")
    elif r.get("reachable"):
        st.session_state.route, st.session_state.highlight = r["path"], [node_id]
        say(f"\U0001F9ED Traseu spre **{name}**: ~{r['walk_min']} min ({r['distance_m']} m). "
            "Urmeaza linia albastra de pe harta.")
    else:
        st.session_state.route, st.session_state.highlight = [], [node_id]
        say(f"Nu pot face traseu spre **{name}**. {r['reason']}")
    st.rerun()  # reincarca toata pagina, ca mesajul sa apara si in chat


# ---------------------------------------------------------------------------
# Sidebar: datele calatoriei + etapele
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("Calatoria ta")

    ticket = S.get("ticket")
    options = list(FLIGHTS) + ([ticket["flight"]] if ticket and ticket["flight"] not in FLIGHTS else [])

    def flight_label(code):
        from_ticket = bool(ticket and ticket["flight"] == code)
        dest = (ticket.get("destination") if from_ticket else None) or FLIGHTS.get(code, {}).get("destination", "")
        delay = FLIGHTS.get(code, {}).get("delay_min", 0)
        return (f"{code} - {dest}" + (f" (intarziat {delay} min)" if delay else "")
                + (" \U0001F3AB din bilet" if from_ticket else ""))

    S["flight"] = st.selectbox("Zborul", options, index=options.index(S["flight"]) if S["flight"] in options else 0,
                               format_func=flight_label)
    S["has_hold_bag"] = st.checkbox("Am bagaj de cala", value=S["has_hold_bag"])
    if ticket:
        st.toggle("Ora reala", value=True, key="use_real_time",
                  help="Cu biletul scanat, numaratoarea pana la imbarcare foloseste ora telefonului.")
    if real_time_on():
        S["now"] = clock_now()
        st.caption(f"Ora acum: **{S['now']}**")
    else:
        now = st.time_input("Ora curenta (simulata)", value=dt.time.fromisoformat(S["now"]), step=300)
        S["now"] = now.strftime("%H:%M")

    st.divider()
    st.subheader("Etapa")
    current = STATES.index(S["state"])
    for i, state in enumerate(STATES):
        mark = "✅" if i < current else ("➡️" if i == current else "⬜")
        st.markdown(f"{mark} {'**' + STATE_LABELS[state] + '**' if i == current else STATE_LABELS[state]}")

    for state, label in STATE_BUTTONS.items():
        if st.button(label, disabled=STATES.index(state) <= current, width="stretch"):
            res = tool_advance_state(S, state)
            if res["ok"]:
                st.session_state.route, st.session_state.highlight = [], []
                st.session_state.nav_target = None
                say(f"Etapa actualizata: **{STATE_LABELS[state]}**. {next_step(S)['step']}")
                st.rerun()
            else:
                st.error(res["error"])

    st.divider()
    if st.button("Calatorie noua", width="stretch"):
        reset()
        st.rerun()
    st.caption("Holul si locurile sunt masurate pe teren. Simulate pentru demo: KFC, intarzierile zborurilor "
               "si regulile de imbarcare cand biletul nu le are tiparite.")


# ---------------------------------------------------------------------------
# Pagina principala
# ---------------------------------------------------------------------------
st.title("✈ Airport Copilot")
st.caption("Asistentul tau in Aeroportul International Craiova")

fs = flight_status(S)
has_ticket = bool(S.get("ticket") and S["ticket"]["flight"] == S["flight"])
if fs.get("found") and ((fs["delay_min"] or 0) > 0 or has_ticket):
    (st.warning if (fs["delay_min"] or 0) > 0 else st.info)(flight_summary(fs))

with st.expander("\U0001F3AB Scaneaza biletul de imbarcare", expanded=bool(st.session_state.ticket_result)):
    st.caption("Fa o poza biletului (pe hartie sau pe ecranul altui telefon) sau incarca o captura de ecran. "
               "Citim zborul si orele tiparite; numele tau nu se pastreaza si poza nu se salveaza.")
    upload = st.file_uploader("Poza biletului", type=["png", "jpg", "jpeg", "webp"],
                              key=f"ticket_upload_{st.session_state.upload_n}", label_visibility="collapsed")
    if upload is not None:
        data = upload.getvalue()
        digest = hashlib.sha256(data).hexdigest()
        if digest != st.session_state.ticket_hash:
            st.session_state.ticket_hash = digest
            with st.spinner("Citesc biletul..."):
                st.session_state.ticket_result = read_ticket(data)

    result = st.session_state.ticket_result
    if result and result["ok"]:
        t = result["ticket"]
        st.info("Am citit: " + describe_ticket(t))
        c1, c2 = st.columns(2)
        if c1.button("Confirm, e corect", type="primary", width="stretch"):
            S["ticket"] = t
            S["flight"] = t["flight"]
            if st.session_state.get("use_real_time", True):
                S["now"] = clock_now()
            st.session_state.ticket_result = None
            st.session_state.ticket_hash = None
            st.session_state.upload_n += 1
            say("\U0001F3AB Am citit biletul. " + flight_summary(flight_status(S)))
            st.rerun()
        if c2.button("Nu e corect, incerc alta poza", width="stretch"):
            st.session_state.ticket_result = None
            st.session_state.ticket_hash = None
            st.session_state.upload_n += 1
            st.rerun()
    elif result:
        st.error(result["error"])

    if S.get("ticket"):
        st.success("Bilet confirmat: " + describe_ticket(S["ticket"]))
        ticket_day = S["ticket"].get("date")
        if ticket_day and ticket_day != dt.date.today().isoformat():
            st.warning(f"Biletul e pentru {dt.date.fromisoformat(ticket_day):%d.%m.%Y}, nu pentru azi. "
                       "Orele se calculeaza ca si cum zborul ar fi azi.")
        if st.button("Sterge biletul"):
            S["ticket"] = None
            st.rerun()

# Harta prima: pe telefon coloanele se pun una sub alta, iar harta live trebuie sa fie sus.
col_map, col_chat = st.columns([1.6, 1], gap="large")

with col_chat:
    messages_box = st.container(height=460, autoscroll=True)

    clicked = None
    cols = st.columns(2)
    for i, q in enumerate(SUGGESTIONS):
        if cols[i % 2].button(q, key=f"sugg_{i}", width="stretch"):
            clicked = q

    prompt = st.chat_input("Intreaba ceva despre aeroport...") or clicked
    if prompt:
        st.session_state.messages.append({"role": "user", "content": prompt})
        with messages_box:
            with st.spinner("Ma gandesc..."):
                result = chat(S, prompt)
        say(result["text"])
        st.session_state.route = result["route"]
        st.session_state.highlight = result["highlight"]
        st.session_state.nav_target = result["route"][-1] if result["route"] else None

    with messages_box:
        if not st.session_state.messages:
            st.info("Salut! Apasa pe un loc de pe harta ca sa vezi traseul, scaneaza biletul ca sa stiu cat timp "
                    "ai pana la imbarcare, sau intreaba-ma orice despre terminal.")
        for m in st.session_state.messages:
            with st.chat_message(m["role"]):
                st.markdown(m["content"])


def refresh_route():
    """Dupa ce pasagerul s-a miscat, recalculeaza traseul spre aceeasi destinatie."""
    route = st.session_state.route
    if not route:
        return
    r = get_route(S, route[-1])
    st.session_state.route = r["path"] if r.get("reachable") and r["path"][-1] != S["position"] else []


def live_target():
    """Tinta traseului, trimisa hartii live doar daca e in zona in care se afla pasagerul."""
    target = st.session_state.get("nav_target")
    if target not in NODES:
        return None
    zone_now = "airside" if STATES.index(S["state"]) >= STATES.index("THROUGH_SECURITY") else "landside"
    return target if NODES[target]["zone"] == zone_now else None


@st.fragment
def map_panel():
    # Fragment: o citire GPS noua reincarca doar harta, nu si chatul.
    svg = render_map(S["position"], st.session_state.route, st.session_state.highlight)
    res = live_map(svg, S["state"], target=live_target())

    if res["go"]:
        navigate_to(res["go"])  # click pe un loc de pe harta

    fix = res["fix"]
    if fix and fix.get("t") != st.session_state.get("last_fix_t"):
        st.session_state.last_fix_t = fix["t"]
        moved = apply_live_position(S, float(fix["s"]), fix.get("at"))
        st.session_state.suggest_security = moved["suggest_security"]
        if moved["changed"]:
            refresh_route()
            st.rerun(scope="fragment")  # redeseneaza harta cu noua pozitie / noul traseu

    target = st.session_state.get("nav_target")
    if target and S["position"] == target:
        st.success(f"Ai ajuns: **{NODES[target]['name']}**.")

    if st.session_state.get("suggest_security") and STATES.index(S["state"]) < STATES.index("THROUGH_SECURITY"):
        st.info("Localizarea arata ca ai trecut de controlul de securitate. Confirmi?")
        if st.button("Da, am trecut de securitate", type="primary", key="confirm_security"):
            if S["state"] == "ARRIVED":
                tool_advance_state(S, "CHECKED_IN")  # a trecut fizic de control, deci check-in-ul e facut
            tool_advance_state(S, "THROUGH_SECURITY")
            st.session_state.suggest_security = False
            st.session_state.route, st.session_state.highlight = [], []
            st.session_state.nav_target = None
            say(f"Etapa actualizata: **{STATE_LABELS['THROUGH_SECURITY']}**. {next_step(S)['step']}")
            st.rerun()  # reincarca toata pagina: etapele din stanga si chatul

    st.caption("Du-ma la:")
    with st.container(horizontal=True, gap="small"):
        for node_id, label in QUICK_TARGETS.items():
            if st.button(label, key=f"go_{node_id}"):
                navigate_to(node_id)
        if st.session_state.route and st.button("✖ Sterge traseul", key="clear_route"):
            st.session_state.route, st.session_state.highlight = [], []
            st.session_state.nav_target = None
            st.rerun(scope="fragment")


with col_map:
    map_panel()
