"""Airport Copilot - interfata Streamlit: chat + harta + butoane de stare.

Rulare:  streamlit run app.py
"""

import datetime as dt

import streamlit as st

from agent import chat
from airport_core import FLIGHTS, STATES, flight_status, new_session, next_step, tool_advance_state
from map_svg import render_map

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


# ---------------------------------------------------------------------------
# Starea aplicatiei (supravietuieste rerun-urilor Streamlit)
# ---------------------------------------------------------------------------
def reset():
    st.session_state.session = new_session()
    st.session_state.messages = []   # [{"role": "user"|"assistant", "content": str}]
    st.session_state.route = []
    st.session_state.highlight = []


if "session" not in st.session_state:
    reset()

S = st.session_state.session


def say(text):
    st.session_state.messages.append({"role": "assistant", "content": text})


# ---------------------------------------------------------------------------
# Sidebar: datele calatoriei + etapele
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("Calatoria ta")

    S["flight"] = st.selectbox(
        "Zborul",
        list(FLIGHTS),
        index=list(FLIGHTS).index(S["flight"]),
        format_func=lambda c: f"{c} - {FLIGHTS[c]['destination']}"
                              + (f" (intarziat {FLIGHTS[c]['delay_min']} min)" if FLIGHTS[c]["delay_min"] else ""),
    )
    S["has_hold_bag"] = st.checkbox("Am bagaj de cala", value=S["has_hold_bag"])
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
                say(f"Etapa actualizata: **{STATE_LABELS[state]}**. {next_step(S)['step']}")
                st.rerun()
            else:
                st.error(res["error"])

    st.divider()
    if st.button("Calatorie noua", width="stretch"):
        reset()
        st.rerun()
    st.caption("Date simulate pentru demo: zboruri, magazine, program, distante, reguli de imbarcare.")


# ---------------------------------------------------------------------------
# Pagina principala
# ---------------------------------------------------------------------------
st.title("✈ Airport Copilot")
st.caption("Asistentul tau in Aeroportul International Craiova")

fs = flight_status(S)
if fs.get("found") and fs["delay_min"] > 0:
    st.warning(
        f"**Zborul {fs['flight']} spre {fs['destination']} este intarziat {fs['delay_min']} min.** "
        f"Plecare estimata {fs['estimated_departure']}, imbarcarea se deschide la {fs['boarding_opens']} "
        f"({fs['gate_name']})."
    )

col_chat, col_map = st.columns([1, 1.7], gap="large")

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

    with messages_box:
        if not st.session_state.messages:
            st.info("Salut! Intreaba-ma unde gasesti ceva in terminal, cat timp mai ai pana la imbarcare "
                    "sau ce urmeaza sa faci. Foloseste butoanele din stanga cand treci de o etapa.")
        for m in st.session_state.messages:
            with st.chat_message(m["role"]):
                st.markdown(m["content"])

with col_map:
    # st.image, nu st.html: in Streamlit 1.66 st.html nu afiseaza SVG-ul (testat), st.image da.
    st.image(render_map(S["position"], st.session_state.route, st.session_state.highlight), width="stretch")
