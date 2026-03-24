from google import genai

# The Ambel Arbitrage Engine: Direct API connection with the upgraded model
client = genai.Client(api_key="AIzaSyDfZoldRBvZIADg5a45FK2jzgS9tnzNv58")

response = client.models.generate_content(
    model='gemini-2.5-flash',
    contents='Hello Gemini, the Ambel Arbitrage Engine is officially online and running the fastest model!'
)
print(response.text)
