"""Genereaza un bilet de imbarcare de EXEMPLU (PNG), pentru testarea scanarii biletului fara un bilet real.

Biletul e marcat clar "EXEMPLU": nu e un bilet real si nu are un cod de bare valid.
Ora implicita de plecare: peste ~2 ore, rotunjita la 5 minute, ca numaratoarea din aplicatie sa aiba sens.

Utilizare (din folderul proiectului):
    python tools/make_demo_ticket.py                    # W6 5678 spre Roma, plecare peste ~2 ore
    python tools/make_demo_ticket.py --plecare 19:30
    python tools/make_demo_ticket.py --zbor "W6 1234" --destinatie "LONDRA LUTON" --cod LTN
"""

import argparse
import datetime as dt
import os
import random

from PIL import Image, ImageDraw, ImageFont

FONTS_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")


def _font(size, bold=False):
    for name in (("arialbd.ttf", "calibrib.ttf") if bold else ("arial.ttf", "calibri.ttf")):
        try:
            return ImageFont.truetype(os.path.join(FONTS_DIR, name), size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _departure(args, now):
    if args.plecare:
        return dt.datetime.combine(now.date(), dt.time.fromisoformat(args.plecare))
    dep = now + dt.timedelta(hours=2)
    dep += dt.timedelta(minutes=(5 - dep.minute % 5) % 5)
    return dep.replace(second=0, microsecond=0)


def main():
    p = argparse.ArgumentParser(description="Bilet de imbarcare de EXEMPLU, pentru testarea scanarii biletului.")
    p.add_argument("--zbor", default="W6 5678")
    p.add_argument("--destinatie", default="ROMA")
    p.add_argument("--cod", default="FCO", help="codul IATA al destinatiei")
    p.add_argument("--plecare", help="ora de plecare HH:MM (implicit: peste ~2 ore)")
    p.add_argument("--data", help="data zborului YYYY-MM-DD (implicit: ziua plecarii)")
    p.add_argument("--loc", default="12A")
    p.add_argument("--iesire", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "bilet-demo.png"))
    args = p.parse_args()

    dep = _departure(args, dt.datetime.now())
    day = dt.date.fromisoformat(args.data) if args.data else dep.date()
    boarding = (dep - dt.timedelta(minutes=40)).strftime("%H:%M")
    gate_closes = (dep - dt.timedelta(minutes=15)).strftime("%H:%M")

    W, H = 1400, 660
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    navy, gray, red, ink = "#1e3a5f", "#5b6b7a", "#dc2626", "#0f172a"

    # Antet
    d.rectangle([0, 0, W, 110], fill=navy)
    d.text((40, 30), "CARTE DE IMBARCARE / BOARDING PASS", font=_font(44, True), fill="white")
    badge, badge_font = "EXEMPLU - DEMO", _font(34, True)
    bw = d.textlength(badge, font=badge_font)
    d.rounded_rectangle([W - 40 - bw - 40, 26, W - 40, 84], radius=10, fill=red)
    d.text((W - 40 - bw - 20, 36), badge, font=badge_font, fill="white")

    def field(x, y, label, value, size=36):
        d.text((x, y), label, font=_font(22), fill=gray)
        d.text((x, y + 30), value, font=_font(size, True), fill=ink)

    field(40, 150, "PASAGER / PASSENGER", "EXEMPLU / PASAGER DEMO")
    field(40, 250, "DE LA / FROM", "CRAIOVA (CRA)")
    field(40, 350, "LA / TO", f"{args.destinatie.upper()} ({args.cod.upper()})")
    field(620, 150, "ZBOR / FLIGHT", args.zbor.upper())
    field(620, 250, "DATA / DATE", day.strftime("%d.%m.%Y"))
    field(620, 350, "LOC / SEAT", args.loc.upper())
    field(960, 150, "PLECARE / DEPARTURE", dep.strftime("%H:%M"), size=60)
    field(960, 270, "IMBARCARE / BOARDING", boarding)
    field(960, 350, "POARTA SE INCHIDE / GATE CLOSES", gate_closes)
    field(960, 430, "POARTA / GATE", "1")

    # Cod de bare decorativ (NU e un cod valid)
    rnd = random.Random(args.zbor + day.isoformat())
    x = 40
    while x < 700:
        bar = rnd.choice((2, 3, 5))
        d.rectangle([x, 500, x + bar, 590], fill=ink)
        x += bar + rnd.choice((2, 3, 4))
    d.text((40, 600), "cod de bare decorativ - nu este valid", font=_font(20), fill=gray)
    d.text((W - 40, 620), "Document de test pentru Airport Copilot. Nu este un bilet real.",
           font=_font(20), fill=gray, anchor="rs")

    img.save(args.iesire)
    print(f"Bilet de exemplu salvat: {args.iesire}")
    print(f"Zbor {args.zbor.upper()} · {day.strftime('%d.%m.%Y')} · plecare {dep:%H:%M} · "
          f"imbarcare {boarding} · poarta se inchide {gate_closes} · loc {args.loc.upper()}")


if __name__ == "__main__":
    main()
