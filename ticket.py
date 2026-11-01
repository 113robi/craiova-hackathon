"""Citirea biletului de imbarcare dintr-o poza sau captura de ecran, cu Gemini.

De ce cu AI si nu cu codul de bare: codul de bare standard al biletelor (IATA BCBP) contine
zborul, data, ruta si locul, dar NU ora de plecare. Ora apare doar tiparita pe bilet.

Contract:
    read_ticket(image_bytes) -> {"ok": True, "ticket": {...}} | {"ok": False, "error": "mesaj pentru pasager"}
    ticket = {"flight", "date", "from", "to", "destination", "departure", "boarding", "gate_closes", "seat"}
             (orele "HH:MM"; campurile care nu apar pe bilet sunt None)

Confidentialitate: numele pasagerului NU se cere si nu se pastreaza; imaginea nu se salveaza.
Test fara Gemini:  python ticket.py
Test cu Gemini:    python ticket.py cale/catre/bilet.png
"""

import io
import json
import re
from datetime import datetime

from google.genai import types

from agent import MODEL, _get_client

SCHEMA = {
    "type": "object",
    "properties": {
        "is_boarding_pass": {"type": "boolean", "description": "true doar daca imaginea e un bilet de avion / boarding pass"},
        "flight_number": {"type": "string", "description": "codul companiei + numarul zborului, ex. W6 5678"},
        "date": {"type": "string", "description": "data zborului, format YYYY-MM-DD"},
        "from_airport": {"type": "string", "description": "codul IATA din 3 litere al aeroportului de plecare"},
        "to_airport": {"type": "string", "description": "codul IATA din 3 litere al destinatiei"},
        "destination_city": {"type": "string", "description": "orasul de destinatie, asa cum e tiparit"},
        "departure_time": {"type": "string", "description": "ora de plecare, HH:MM (24h)"},
        "boarding_time": {"type": "string", "description": "ora de imbarcare, HH:MM (24h)"},
        "gate_closes_time": {"type": "string", "description": "ora la care se inchide poarta, HH:MM (24h)"},
        "seat": {"type": "string", "description": "locul, ex. 12A"},
    },
    "required": ["is_boarding_pass", "flight_number", "date", "from_airport", "to_airport", "destination_city",
                 "departure_time", "boarding_time", "gate_closes_time", "seat"],
}

PROMPT = """Extrage datele de pe acest bilet de imbarcare (boarding pass).
Reguli:
- Citeste DOAR ce e tiparit in imagine. Nu ghici si nu completa din cunostinte generale.
- Daca un camp nu apare clar pe bilet, pune sir gol "".
- Orele in format HH:MM (24 de ore). Data in format YYYY-MM-DD; daca anul lipseste, pune sir gol.
- Numarul zborului: codul companiei si numarul, de exemplu "W6 5678".
- is_boarding_pass = false daca imaginea nu este un bilet de avion.
Nu extrage numele pasagerului."""

MAX_SIDE_PX = 1600  # pozele de pe telefon se micsoreaza inainte de trimitere
MAX_IMAGE_MB = 10   # aceeasi limita ca la incarcarea din pagina (app.py)
# Protectie de memorie: o imagine mica pe disc se poate decomprima in miliarde de pixeli.
# Pozele JPEG se decodeaza direct la rezolutie redusa, deci limita conteaza mai ales pentru PNG / WebP.
MAX_PIXELS = 60_000_000


class ImageTooLarge(ValueError):
    pass


def _clean_time(value):
    m = re.fullmatch(r"\s*([01]?\d|2[0-3])\s*[:.hH]\s*([0-5]\d)\s*", value or "")
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else None


def _clean_flight(value):
    compact = re.sub(r"[\s-]+", "", (value or "").upper())
    m = re.fullmatch(r"([A-Z0-9]{2})(\d{1,5})([A-Z]?)", compact)   # pe unele bilete numarul are zerouri in fata
    if not m or not re.search(r"[A-Z]", m.group(1)) or not 0 < int(m.group(2)) <= 9999:
        return None
    return f"{m.group(1)} {int(m.group(2))}{m.group(3)}"


def _clean_date(value):
    try:
        return datetime.strptime((value or "").strip(), "%Y-%m-%d").date().isoformat()
    except ValueError:
        return None


def _clean_code(value):
    code = (value or "").strip().upper()
    return code if re.fullmatch(r"[A-Z]{3}", code) else None


def _clean_seat(value):
    seat = re.sub(r"\s+", "", (value or "").upper())
    return seat if re.fullmatch(r"\d{1,2}[A-K]", seat) else None


def _clean_text(value, max_len=40):
    # Doar litere, cifre, spatii si - ' . , ( ) /: textul ajunge in mesaje afisate ca Markdown.
    text = re.sub(r"[^\w\s\-'.,()/]|_", "", value or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_len] or None


def _minutes(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def parse_ticket(raw):
    """Valideaza raspunsul modelului. Separat de read_ticket, ca sa poata fi testat fara Gemini."""
    if not isinstance(raw, dict) or not raw.get("is_boarding_pass"):
        return {"ok": False, "error": "Imaginea nu pare sa fie un bilet de imbarcare. Incearca alta poza."}
    ticket = {
        "flight": _clean_flight(raw.get("flight_number")),
        "date": _clean_date(raw.get("date")),
        "from": _clean_code(raw.get("from_airport")),
        "to": _clean_code(raw.get("to_airport")),
        "destination": _clean_text(raw.get("destination_city")),
        "departure": _clean_time(raw.get("departure_time")),
        "boarding": _clean_time(raw.get("boarding_time")),
        "gate_closes": _clean_time(raw.get("gate_closes_time")),
        "seat": _clean_seat(raw.get("seat")),
    }
    if not ticket["flight"]:
        return {"ok": False, "error": "Nu am putut citi numarul zborului. Fa o poza mai clara, cu tot biletul in cadru."}
    if not ticket["departure"]:
        return {"ok": False, "error": "Nu am putut citi ora de plecare de pe bilet. Fa o poza mai clara."}
    # O ora de imbarcare / de inchidere a portii dupa ora de plecare e o citire gresita: o ignoram.
    dep = _minutes(ticket["departure"])
    for key in ("boarding", "gate_closes"):
        if ticket[key] and not (0 < dep - _minutes(ticket[key]) <= 180):
            ticket[key] = None
    return {"ok": True, "ticket": ticket}


def _prepare_image(image_bytes):
    """Deschide poza, o roteste dupa EXIF, o micsoreaza si o trimite ca JPEG."""
    from PIL import Image, ImageOps

    try:
        img = Image.open(io.BytesIO(image_bytes))
    except Image.DecompressionBombError as exc:
        raise ImageTooLarge(str(exc)) from exc
    img.draft("RGB", (MAX_SIDE_PX, MAX_SIDE_PX))   # JPEG: decodare direct la o rezolutie apropiata de cea finala
    if img.width * img.height > MAX_PIXELS:
        raise ImageTooLarge(f"{img.width}x{img.height}")
    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((MAX_SIDE_PX, MAX_SIDE_PX))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=88)
    return out.getvalue()


def read_ticket(image_bytes):
    if len(image_bytes) > MAX_IMAGE_MB * 1024 * 1024:
        return {"ok": False, "error": f"Poza e prea mare (peste {MAX_IMAGE_MB} MB). Incearca o captura de ecran."}
    try:
        jpeg = _prepare_image(image_bytes)
    except ImageTooLarge:
        return {"ok": False, "error": "Poza are o rezolutie prea mare. Fa o captura de ecran a biletului "
                                      "sau o poza la rezolutie normala."}
    except Exception:  # noqa: BLE001 - fisier care nu e imagine, imagine corupta
        return {"ok": False, "error": "Nu pot deschide imaginea. Incearca o poza JPG sau PNG."}
    try:
        resp = _get_client().models.generate_content(
            model=MODEL,
            contents=[types.Part.from_bytes(data=jpeg, mime_type="image/jpeg"), PROMPT],
            config=types.GenerateContentConfig(response_mime_type="application/json", response_json_schema=SCHEMA),
        )
        raw = json.loads(resp.text)
    except Exception as exc:  # noqa: BLE001 - cheie gresita, fara internet, cota depasita, raspuns invalid
        print(f"[ticket] eroare Gemini: {type(exc).__name__}: {exc}")
        return {"ok": False, "error": "Nu am putut citi biletul acum (fara conexiune sau prea multe cereri). "
                                      "Incearca din nou peste un minut."}
    return parse_ticket(raw)


def describe_ticket(t):
    """Rezumat pe un rand, pentru confirmarea pasagerului."""
    route = " -> ".join(c for c in (t.get("from"), t.get("to")) if c)
    if t.get("destination"):
        route = f"{route} ({t['destination']})" if route else t["destination"]
    parts = [f"Zbor {t['flight']}", route]
    if t.get("date"):
        parts.append(datetime.strptime(t["date"], "%Y-%m-%d").strftime("%d.%m.%Y"))
    parts.append(f"plecare {t['departure']}")
    if t.get("boarding"):
        parts.append(f"imbarcare {t['boarding']}")
    if t.get("gate_closes"):
        parts.append(f"poarta se inchide {t['gate_closes']}")
    if t.get("seat"):
        parts.append(f"loc {t['seat']}")
    return " · ".join(p for p in parts if p)


def _self_test():
    good = {"is_boarding_pass": True, "flight_number": "w6 05678", "date": "2026-10-31", "from_airport": "cra",
            "to_airport": "FCO", "destination_city": " Roma ", "departure_time": "19.30", "boarding_time": "18:50",
            "gate_closes_time": "19:15", "seat": "12 a"}
    r = parse_ticket(good)
    assert r["ok"], r
    t = r["ticket"]
    assert t == {"flight": "W6 5678", "date": "2026-10-31", "from": "CRA", "to": "FCO", "destination": "Roma",
                 "departure": "19:30", "boarding": "18:50", "gate_closes": "19:15", "seat": "12A"}, t
    assert describe_ticket(t) == ("Zbor W6 5678 · CRA -> FCO (Roma) · 31.10.2026 · plecare 19:30 · "
                                  "imbarcare 18:50 · poarta se inchide 19:15 · loc 12A")
    assert not parse_ticket({**good, "is_boarding_pass": False})["ok"]
    assert not parse_ticket({**good, "departure_time": ""})["ok"]
    assert not parse_ticket({**good, "flight_number": "1234"})["ok"]
    assert parse_ticket({**good, "flight_number": "FR1234"})["ticket"]["flight"] == "FR 1234"
    # imbarcare dupa plecare = citire gresita -> ignorata; campuri lipsa -> None
    t = parse_ticket({**good, "boarding_time": "20:10", "date": "31 OCT", "seat": ""})["ticket"]
    assert t["boarding"] is None and t["date"] is None and t["seat"] is None
    assert not parse_ticket("nu e json")["ok"]
    # Textul liber (orasul) pierde caracterele de formatare: ajunge in mesaje afisate ca Markdown
    dest = parse_ticket({**good, "destination_city": "Roma *[x](http://a.b)* <b>$1$</b>"})["ticket"]["destination"]
    assert dest and not set(dest) & set("*[]<>$_:`"), dest
    # Imagine mica pe disc, dar cu prea multi pixeli: refuzata inainte de decodare si fara cerere la Gemini
    from PIL import Image

    buf = io.BytesIO()
    Image.new("1", (10000, 7000)).save(buf, format="PNG")
    assert read_ticket(buf.getvalue())["error"].startswith("Poza are o rezolutie prea mare")
    assert read_ticket(b"0" * (MAX_IMAGE_MB * 1024 * 1024 + 1))["error"].startswith("Poza e prea mare")
    print("OK")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        with open(sys.argv[1], "rb") as f:
            result = read_ticket(f.read())
        print(result)
        if result["ok"]:
            print(describe_ticket(result["ticket"]))
    else:
        _self_test()
