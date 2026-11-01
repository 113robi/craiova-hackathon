"""Verificare rapida a cheii Gemini din .env: trimite o singura cerere si afiseaza raspunsul.

Rulare:  python test.py
"""

import os
from dotenv import load_dotenv
from google import genai

load_dotenv()
client = genai.Client(api_key=os.getenv("API_KEY"))
r = client.models.generate_content(
    model="gemini-3.5-flash-lite",
    contents="raspunde cu un cuvant: salut",
)
print(r.text)