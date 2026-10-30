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