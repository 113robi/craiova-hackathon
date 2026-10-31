# Airport Copilot – Aeroportul Craiova

Asistent pe telefon pentru pasagerii Aeroportului Internațional Craiova: **hartă a terminalului cu poziția ta în timp real**, trasee pe jos până la locul căutat și un **chat AI în română** care răspunde doar din date verificate de cod.

Proiect realizat la Craiova Hackathon V2.0, provocarea *Autopilot* (AI, automatizare, sisteme inteligente).

## Ce face

- **Localizare live.** Telefonul își trimite poziția (GPS + Wi-Fi, din browser). Punctul „Ești aici” se mișcă pe hartă, cu un cerc care arată precizia.
- **Trasee.** Calculează drumul cel mai scurt de la poziția ta până la check-in, toaletă, mâncare, magazin sau poarta zborului tău. Traseul se recalculează când te miști.
- **Chat AI în română.** Poți întreba „Unde pot mânca?”, „Cât timp am până la îmbarcare?” sau „Mă pot întoarce la check-in?”. Răspunsul și traseul de pe hartă vin din rezultatele codului, nu din textul inventat de model.
- **Etapele călătoriei.** Aplicația știe dacă ai ajuns, ai făcut check-in, ai trecut de securitate sau ești la îmbarcare. După securitate nu te mai trimite în zona publică. Când GPS-ul te arată după control, te întreabă dacă ai trecut și abia după confirmare schimbă etapa.
- **Zbor întârziat.** Afișează întârzierea, ora estimată, când se deschide îmbarcarea și când poți cere asistență la companie.

## Arhitectură

```
app.py          Streamlit: harta, chatul, etapele calatoriei
 ├── live_map.py   componenta JavaScript: GPS din telefon -> punct pe harta -> pozitia trimisa la Python
 ├── map_svg.py    harta terminalului (SVG generat din graf)
 ├── agent.py      Gemini cu function calling: chat(session, message) -> text + traseu + locuri evidentiate
 └── airport_core.py  nucleul: graful terminalului, Dijkstra, etapele calatoriei, tool-uri, date
```

`airport_core.py` nu depinde de niciun alt fișier. Toate celelalte se bazează pe el.

| Parte | Tehnologie |
|---|---|
| Interfață și server | Python, [Streamlit](https://streamlit.io) 1.66 (un singur proces) |
| Localizare | Geolocation API din browser, componentă proprie `st.components.v2` în JavaScript |
| Hartă | SVG generat în Python, la scară după măsurători |
| Trasee | graf orientat + Dijkstra (`heapq`). Securitatea e o muchie cu sens unic |
| AI | Gemini (`gemini-3.5-flash-lite`) prin `google-genai`, cu function calling |
| HTTPS pentru telefon | tunel Cloudflare (`cloudflared`) |

## Cum funcționează localizarea

1. Browserul dă latitudinea, longitudinea și precizia, prin `navigator.geolocation.watchPosition`.
2. Coordonatele se proiectează pe axa coridorului. Rezultatul e `s`, adică metri de la intrare (`geo_to_s` în `airport_core.py`, aceeași formulă și în JS).
3. Citirile cu precizie peste 40 m se ignoră. Restul trec printr-un filtru Kalman 1D, ca punctul să nu sară.
4. Punctul se mișcă pe hartă direct în browser. Python primește poziția **doar când se schimbă zona** (cel mai apropiat punct de pe coridor), ca pagina să nu se reîncarce continuu.
5. Python mută pasagerul pe cel mai apropiat nod, ținând cont de etapă (`locate`, `apply_live_position`), și recalculează traseul.

Măsurători făcute în terminalul vechi (31.10.2026), de la intrare până la poartă:

| Telefon | Precizie | Observație |
|---|---|---|
| Samsung S21 | 3–14 m | distanțele corecte, folosit pentru calibrare |
| iPhone 16 Pro | 17–50 m | supraestimează distanțele cu ~45% |

## Ce e real și ce e simulat

- **Măsurat:** geometria coridorului (67 m), poziția intrării, a securității (25–39 m) și a porții (67 m). Check-in-ul e la ~10 m, după estimarea echipei.
- **Simulat:** cafeneaua, toaletele, KFC, Duty Free, Poarta 2, zborurile (`W6 1234`, `W6 5678`), programul și regulile de îmbarcare (`RULES`).

Ca să muți un loc după realitate, schimbă `s` (metri de la intrare) și `side` (`+1` nord, `-1` sud) în lista `PLACES` din `airport_core.py`. Harta și traseele se actualizează singure.

## Instalare și rulare

Cerințe: Python 3.11+, o cheie Gemini API.

```powershell
pip install -r requirements.txt
copy .env.example .env      # apoi pune cheia ta in .env: API_KEY=...
python -m streamlit run app.py
```

Aplicația se deschide la `http://localhost:8501`. Pe laptop, localizarea folosește poziția laptopului.

### Pe telefon (necesar pentru localizare)

Browserul dă locația doar pe HTTPS, așa că aplicația trebuie servită printr-un tunel:

```powershell
winget install --id Cloudflare.cloudflared       # o singura data
# terminal 1
python -m streamlit run app.py
# terminal 2
& "C:\Program Files (x86)\cloudflared\cloudflared.exe" tunnel --url http://localhost:8501
```

Pe telefon deschide linkul `https://….trycloudflare.com` afișat de tunel, apasă **„Pornește localizarea live”** și permite accesul la locație. Ambele terminale trebuie să rămână deschise. Linkul se schimbă la fiecare pornire a tunelului.

**Important:** după orice modificare în fișierele `.py` sau după `git pull`, oprește Streamlit cu `Ctrl+C` și pornește-l din nou. Streamlit nu reîncarcă singur modulele importate.

## Teste

```powershell
python airport_core.py     # autotestul nucleului (fara internet) -> OK
python map_svg.py          # genereaza o previzualizare a hartii (.html)
python agent.py            # 4 intrebari reale catre Gemini (consuma cereri din cota)
```

`tools/geo-test.html` e o pagină separată care măsoară precizia localizării pe telefon. A fost folosită pentru calibrare.

## Limitări cunoscute

- Precizia GPS în interior ajunge pentru **zone** (public, securitate, porți), nu pentru locuri la un metru distanță. Punctul de pe hartă e exact unde e telefonul, iar traseul pornește din cel mai apropiat punct de pe coridor.
- Cheia gratuită Gemini permite 15 cereri pe minut, iar un mesaj în chat consumă 2–3 cereri. La eroare sau la depășirea limitei, aplicația afișează un mesaj de rezervă.
- Tunelul `trycloudflare` e pentru demo, nu pentru producție.

## Pași următori

- Localizare mai precisă prin infrastructura aeroportului: Wi-Fi cu localizare sau beacon-uri Bluetooth. Restul sistemului rămâne neschimbat, pentru că primește doar poziția.
- Pozițiile reale ale magazinelor și toaletelor, date reale de zbor și reguli de îmbarcare verificate cu compania.

## Documente de lucru

- `plan-hackathon.md`: planul inițial, cercetarea și împărțirea pe roluri.
- `taskuri.md`: lista de taskuri, în ordine.
