"""Agentul LLM: trimite mesajul la Gemini cu tool-urile din airport_core si
returneaza textul + ce trebuie desenat pe harta.

Contract:
    chat(session, message) -> {"text": str, "route": list[str], "highlight": list[str]}

`route` si `highlight` vin DOAR din rezultatele tool-urilor (cod determinist),
niciodata din textul generat de model, si sunt filtrate prin NODES.
"""

import os

from dotenv import load_dotenv
from google import genai
from google.genai import types

from airport_core import DISPATCH, NODES, SYSTEM_PROMPT, TOOLS, get_route, session_context

load_dotenv()

MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
MAX_ROUNDS = 5  # cate runde de apeluri de tool-uri acceptam pentru un mesaj

FALLBACK_TEXT = "Nu pot raspunde acum. Incearca din nou sau intreaba la ghiseul de informatii."

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = genai.Client(api_key=os.getenv("API_KEY"))
    return _client


_CONFIG = types.GenerateContentConfig(
    system_instruction=SYSTEM_PROMPT,
    tools=[types.Tool(function_declarations=[
        types.FunctionDeclaration(
            name=t["name"],
            description=t["description"],
            parameters_json_schema=t["parameters"],
        )
        for t in TOOLS
    ])],
)


def _run_tool(session, name, args):
    """Executa un tool. O exceptie devine {"error": ...} ca bucla sa continue."""
    fn = DISPATCH.get(name)
    if fn is None:
        return {"error": f"Tool necunoscut: {name}"}
    try:
        return fn(session, **args)
    except Exception as exc:  # noqa: BLE001 - modelul primeste eroarea, aplicatia nu crapa
        return {"error": f"{type(exc).__name__}: {exc}"}


class _MapCollector:
    """Strange din rezultatele tool-urilor ce trebuie desenat pe harta."""

    def __init__(self):
        self.route = []
        self.highlight = []

    def add(self, name, result):
        if not isinstance(result, dict) or "error" in result:
            return
        if name == "get_route" and result.get("reachable"):
            self.route = result["path"]          # ultimul traseu valid castiga
            self.highlight.append(result["target"])
        elif name == "list_places":
            self.highlight.extend(p["id"] for p in result.get("available_now", []))
        elif name == "flight_status" and result.get("found"):
            self.highlight.append(result["gate"])
        elif name == "next_step":
            self.highlight.append(result.get("go_to"))

    def result(self, session):
        highlight = list(dict.fromkeys(n for n in self.highlight if n in NODES))  # fara duplicate, in ordine
        route = self.route
        # Modelul a raspuns din list_places / next_step fara get_route: desenam totusi
        # traseul spre primul loc evidentiat (cel mai apropiat), calculat de cod.
        if not route and highlight:
            r = get_route(session, highlight[0])
            if r.get("reachable"):
                route = r["path"]
        route = [n for n in route if n in NODES]
        return route, highlight


def chat(session: dict, message: str) -> dict:
    # Istoricul se pastreaza in sesiune; lucram pe o copie si il salvam doar la succes,
    # ca o eroare la mijloc sa nu lase istoricul stricat (apel de tool fara raspuns).
    history = list(session.get("history", []))
    history.append(types.Content(role="user", parts=[
        types.Part(text=f"{session_context(session)}\n\n{message}")
    ]))
    collector = _MapCollector()

    try:
        client = _get_client()
        text = None
        for _ in range(MAX_ROUNDS):
            resp = client.models.generate_content(model=MODEL, contents=history, config=_CONFIG)
            if not resp.candidates or resp.candidates[0].content is None:
                break
            # Pastram continutul modelului intact (inclusiv thought signatures cerute de Gemini 3).
            history.append(resp.candidates[0].content)

            calls = resp.function_calls or []
            if not calls:
                text = (resp.text or "").strip()
                break

            responses = []
            for call in calls:
                result = _run_tool(session, call.name, dict(call.args or {}))
                collector.add(call.name, result)
                responses.append(types.Part.from_function_response(name=call.name, response={"result": result}))
            history.append(types.Content(role="user", parts=responses))
    except Exception as exc:  # noqa: BLE001 - cheie gresita, fara internet, cota depasita etc.
        print(f"[agent] eroare Gemini: {type(exc).__name__}: {exc}")
        return {"text": FALLBACK_TEXT, "route": [], "highlight": []}

    if not text:
        return {"text": FALLBACK_TEXT, "route": [], "highlight": []}

    session["history"] = history
    route, highlight = collector.result(session)
    return {"text": text, "route": route, "highlight": highlight}


if __name__ == "__main__":
    # Test manual rapid (consuma apeluri API): python agent.py
    from airport_core import new_session, tool_advance_state

    s = new_session()
    for q in ["unde pot manca?", "unde e toaleta?"]:
        print(q, "->", chat(s, q))
    tool_advance_state(s, "CHECKED_IN")
    tool_advance_state(s, "THROUGH_SECURITY")
    for q in ["unde pot manca?", "pot sa ma intorc la check-in?"]:
        print(q, "->", chat(s, q))
