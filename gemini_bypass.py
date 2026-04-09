"""
gemini_bypass.py — Quick connectivity test for the Gemini API.

Usage:
    GEMINI_API_KEY=your_key python gemini_bypass.py

The API key is loaded from the GEMINI_API_KEY environment variable.
Never hardcode secrets in source files.
"""

import os

from dotenv import load_dotenv
from google import genai

load_dotenv()

api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    raise EnvironmentError("GEMINI_API_KEY is not set. Copy .env.example to .env and fill it in.")

client = genai.Client(api_key=api_key)

response = client.models.generate_content(
    model="gemini-2.5-flash",
    contents="Hello Gemini, the Ambel Arbitrage Engine is officially online and running the fastest model!",
)
print(response.text)
