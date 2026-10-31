# Airport Copilot – Aeroportul Craiova

Asistent pe telefon pentru pasagerii Aeroportului Internațional Craiova: **hartă reală a terminalului cu poziția ta în timp real**, trasee pe jos până la locul pe care apeși, **scanarea biletului** pentru ora de îmbarcare și un **chat AI în română** care răspunde doar din date verificate de cod.

Proiect realizat la Craiova Hackathon V2.0, provocarea *Autopilot* (AI, automatizare, sisteme inteligente).

## Ce face

- **Hartă reală.** Contururile clădirilor din OpenStreetMap, la scară: holul vechi cu capătul tăiat în diagonală, anexa cu toaleta, clădirea alipită cu securitatea și poarta. Drumul de mers urmează traseul înregistrat pe teren și se curbează spre poartă. Locurile sunt măsurate pe teren.
- **Localizare live.** Telefonul își trimite poziția (GPS + Wi-Fi, din browser). Punctul „Ești aici” merge pe drumul real, cu un cerc care arată precizia, și intră în locul spre care ai traseu când ajungi la el. Butonul **„Sunt la intrare”** corectează abaterea GPS din ziua respectivă.
- **Trasee la un click.** Apeși pe un loc de pe hartă sau pe butoanele „Du-mă la” și primești traseul de unde ești, fără chatbot. Traseul se recalculează când te miști.
- **Scanarea biletului.** Faci o poză biletului de îmbarcare, iar aplicația citește zborul, data și orele tipărite (plecare, îmbarcare, închiderea porții). Confirmi ce a citit și primești numărătoarea până la îmbarcare, după ora reală a telefonului.
- **Chat AI în română.** Poți întreba „Unde pot mânca?”, „Cât timp am până la îmbarcare?” sau „Mă pot întoarce la check-in?”. Răspunsul și traseul de pe hartă vin din rezultatele codului, nu din textul inventat de model.
- **Etapele călătoriei.** Aplicația știe dacă ai ajuns, ai făcut check-in, ai trecut de securitate sau ești la îmbarcare. După securitate nu te mai trimite în zona publică. Când GPS-ul te arată după control, te întreabă dacă ai trecut și abia după confirmare schimbă etapa.
- **Zbor întârziat.** Afișează întârzierea, ora estimată, când se deschide îmbarcarea și când poți cere asistență la companie.

## Arhitectură

```
app.py          Streamlit: harta, biletul, chatul, etapele calatoriei
 ├── live_map.py   componenta JavaScript: GPS din telefon, click pe harta, "Sunt la intrare"
 ├── map_svg.py    harta terminalului (SVG la scara, locuri pe care se poate apasa)
 ├── ticket.py     citirea biletului de imbarcare dintr-o poza (Gemini, raspuns JSON validat)
 ├── agent.py      Gemini cu function calling: chat(session, message) -> text + traseu + locuri evidentiate
 └── airport_core.py  nucleul: geometria terminalului, graful, Dijkstra, etapele, zborurile, tool-uri
```

`airport_core.py` nu depinde de niciun alt fișier. Toate celelalte se bazează pe el.

| Parte | Tehnologie |
|---|---|
| Interfață și server | Python, [Streamlit](https://streamlit.io) 1.66 (un singur proces) |
| Localizare și click pe hartă | Geolocation API din browser, componentă proprie `st.components.v2` în JavaScript |
| Hartă | SVG generat în Python, la scară după măsurători și conturul clădirii din OpenStreetMap |
| Trasee | graf orientat + Dijkstra (`heapq`). Securitatea e o muchie cu sens unic |
| AI | Gemini (`gemini-3.5-flash-lite`) prin `google-genai`: function calling pentru chat, imagine → JSON pentru bilet |
| HTTPS pentru telefon | tunel Cloudflare (`cloudflared`) |

## Cum funcționează localizarea

1. Browserul dă latitudinea, longitudinea și precizia, prin `navigator.geolocation.watchPosition`.
2. Coordonatele se transformă în metri: `s` de-a lungul holului (negativ la stânga intrării, pozitiv spre securitate) și `d` de-a curmezișul lui (`geo_to_s` în `airport_core.py`, aceeași formulă și în JS).
3. Citirile cu precizie peste 40 m se ignoră. Restul trec prin filtre Kalman 1D, ca punctul să nu sară.
4. **Pe lungime**, punctul urmează GPS-ul și stă pe drumul real, care se curbează. **Pe lățime**, GPS-ul din interior e prea imprecis (la măsurători, intrarea a ieșit mai „sus” decât check-in-ul), așa că punctul se mută lateral doar când știm sigur unde ești:
   - când ajungi la cel mult 3 m de locul spre care ai traseu, punctul intră în loc și apare „Ai ajuns”;
   - o deplasare laterală mare (peste 10 m) lângă capătul din stânga înseamnă că ai intrat pe holul spre toaletă.
5. Python primește poziția **doar când se schimbă zona** sau locul, ca pagina să nu se reîncarce continuu. Apoi mută pasagerul pe nodul potrivit, ținând cont de etapă (`locate`, `apply_live_position`), și recalculează traseul.
6. **„Sunt la intrare”** mută estimarea curentă la `s = 0`, fixează referința laterală și aplică aceeași corecție tuturor citirilor următoare. Butonul apare doar la cel mult 20 m de intrare și înainte de securitate, ca să nu poată fi apăsat din greșeală la poartă.

Măsurători făcute în terminalul vechi (31.10.2026):

| Telefon | Precizie | Observație |
|---|---|---|
| Samsung S21 | 3–15 m | două sesiuni; distanțele dintre locuri s-au potrivit (intrare → poartă: 67 m și 66,6 m), dar poziția absolută a alunecat cu ~7 m între sesiuni |
| iPhone 16 Pro | 17–50 m | supraestimează distanțele cu ~45% |

Calibrarea folosește media celor două sesiuni S21. Abaterea din ziua demo-ului se corectează cu „Sunt la intrare”.

## Cum funcționează scanarea biletului

Codul de bare standard al biletelor (IATA BCBP) conține zborul, data, ruta și locul, dar **nu și ora de plecare**. De aceea biletul se citește din poză:

1. Pasagerul fotografiază biletul (hârtie sau ecran) sau încarcă o captură de ecran.
2. Poza se micșorează și se trimite la Gemini, care întoarce JSON după o schemă fixă: zbor, dată, aeroporturi, oră de plecare, îmbarcare, închiderea porții, loc. Numele pasagerului nu se cere.
3. Codul validează fiecare câmp (format de zbor, ore, dată). Câmpurile necitite rămân goale, iar o oră de îmbarcare după plecare e ignorată.
4. Pasagerul vede ce s-a citit și confirmă. Abia apoi biletul devine zborul lui.
5. Ora programată vine de pe bilet. Întârzierea vine din datele live, simulate pentru demo. Dacă zborul nu e în datele live, aplicația spune că nu are informații despre întârzieri.

Poza nu se salvează. Pentru demo există un bilet de exemplu, marcat „EXEMPLU”:

```powershell
python tools/make_demo_ticket.py                 # W6 5678 spre Roma, plecare peste ~2 ore -> tools/bilet-demo.png
python tools/make_demo_ticket.py --plecare 19:30
```

## Ce e real și ce e simulat

- **Măsurat pe teren:** drumul de mers (de la ~8 m la stânga intrării până la poartă, 82 m de la intrare), intrarea, toaleta (la stânga, pe peretele opus, la capătul unui hol lateral de 10–15 m), check-in-ul (14 m), biroul de informații (30 m), securitatea (~50 m), cafeneaua (imediat după securitate) și Poarta 1.
- **Din OpenStreetMap:** contururile clădirilor (pereții de pe hartă).
- **Simulat:** **KFC** (nu există în terminal, e pus doar pentru demo), starea live a zborurilor (întârzierea lui `W6 5678`), regulile de îmbarcare (`RULES`, folosite doar când biletul nu are orele tipărite).
- **Necunoscut:** programul cafenelei. Agentul nu spune că un loc e deschis dacă nu știe.

Ca să muți sau să adaugi un loc, modifici în `airport_core.py` lista `SIDE_PLACES` (locurile din lateral, cu coordonatele `(s, d)` în metri și punctul de drum de care sunt legate) sau `MAIN_PATH` (drumul de mers). `s` e distanța de la intrare (negativ la stânga), iar `d` e poziția laterală (pozitiv spre peretele opus intrării). Harta, distanțele și traseele se actualizează singure. Autotestul verifică dacă fiecare loc e în conturul clădirii.

## Instalare și rulare

Cerințe: Python 3.11+, o cheie Gemini API.

```powershell
pip install -r requirements.txt
copy .env.example .env      # apoi pune cheia ta in .env: API_KEY=...
python -m streamlit run app.py
```

Aplicația se deschide la `http://localhost:8501`. Pe laptop, localizarea folosește poziția laptopului.

### Pe telefon (necesar pentru localizare și pentru poza biletului)

Browserul dă locația doar pe HTTPS, așa că aplicația trebuie servită printr-un tunel:

```powershell
winget install --id Cloudflare.cloudflared       # o singura data
# terminal 1
python -m streamlit run app.py
# terminal 2
& "C:\Program Files (x86)\cloudflared\cloudflared.exe" tunnel --url http://localhost:8501
```

Pe telefon deschide linkul `https://….trycloudflare.com` afișat de tunel, apasă **„Pornește localizarea live”** și permite accesul la locație. La intrarea în terminal, apasă **„Sunt la intrare”**. Ambele terminale trebuie să rămână deschise. Linkul se schimbă la fiecare pornire a tunelului.

**Important:** după orice modificare în fișierele `.py` sau după `git pull`, oprește Streamlit cu `Ctrl+C` și pornește-l din nou. Streamlit nu reîncarcă singur modulele importate.

## Teste

```powershell
python airport_core.py     # autotestul nucleului (fara internet) -> OK
python ticket.py           # validarea datelor de pe bilet (fara internet) -> OK
python ticket.py tools/bilet-demo.png   # citirea reala a biletului de exemplu (1 cerere Gemini)
python map_svg.py          # genereaza o previzualizare a hartii (.html)
python agent.py            # 4 intrebari reale catre Gemini (consuma cereri din cota)
```

`tools/geo-test.html` e pagina de măsurare folosită pentru calibrare: arată metri de la intrare, face media citirilor la salvare și poate înregistra tot traseul.

## Limitări cunoscute

- Precizia GPS în interior ajunge pe lungimea holului, dar nu și pe lățime. De aceea punctul stă pe drumul de mers și intră lateral într-un loc doar la sosire sau la drumul spre toaletă. Traseul pornește din cel mai apropiat punct al drumului.
- Cheia gratuită Gemini permite 15 cereri pe minut. Un mesaj în chat consumă 2–3 cereri, o scanare de bilet consumă una. Traseele din click pe hartă nu consumă nimic. La eroare sau la depășirea limitei, aplicația afișează un mesaj de rezervă.
- Tunelul `trycloudflare` e pentru demo, nu pentru producție.

## Pași următori

- Localizare mai precisă prin infrastructura aeroportului: Wi-Fi cu localizare sau beacon-uri Bluetooth. Restul sistemului rămâne neschimbat, pentru că primește doar poziția.
- Date reale de zbor (întârzieri, poartă) de la aeroport și reguli de îmbarcare verificate cu compania.

## Documente de lucru

- `plan-hackathon.md`: planul inițial, cercetarea și împărțirea pe roluri.
- `taskuri.md`: lista de taskuri, în ordine.
