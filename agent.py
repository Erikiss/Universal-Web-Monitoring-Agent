import os
import asyncio
import smtplib
from email.message import EmailMessage
from langchain_openai import ChatOpenAI
from browser_use import Agent, Browser

# FIX: Wir holen uns die Config direkt aus dem Untermodul, 
# da sie im Hauptpaket gerade nicht verfügbar ist.
try:
    from browser_use import BrowserConfig
except ImportError:
    from browser_use.browser.browser import BrowserConfig

class SimpleGroqWrapper:
    def __init__(self, model_name, api_key):
        self.llm = ChatOpenAI(
            model=model_name,
            api_key=api_key,
            base_url="https://api.groq.com/openai/v1"
        )
        self.provider = 'openai'
        self.model = model_name

    def __getattr__(self, name):
        return getattr(self.llm, name)

    async def ainvoke(self, *args, **kwargs):
        return await self.llm.ainvoke(*args, **kwargs)

async def run_generic_agent():
    steel_key = os.getenv('STEEL_API_KEY')
    
    # Der entscheidende Teil: Wir zwingen den Browser in die Cloud
    config = BrowserConfig(
        wss_url=f"wss://connect.steel.dev?apiKey={steel_key}"
    )
    browser = Browser(config=config)
    
    llm = SimpleGroqWrapper(
        model_name="llama-3.3-70b-versatile",
        api_key=os.getenv('GROQ_API_KEY')
    )

    # Task mit expliziter Anweisung zur Datensuche
    task = f"""
    1. Gehe zu {os.getenv('TARGET_URL')}.
    2. Logge dich ein (User: "{os.getenv('TARGET_USER')}", PW: "{os.getenv('TARGET_PW')}").
    3. Untersuche die Seite nach neuen Datenberichten der letzten 4 Wochen.
    4. Wenn du Daten findest, fasse sie zusammen.
    5. Wenn KEINE neuen Daten da sind, antworte exakt: "Keine neuen Daten gefunden."
    """

    agent = Agent(task=task, llm=llm, browser=browser)
    history = await agent.run()
    
    # Ergebnis-Handling
    result = history.final_result()
    if not result:
        return "Der Agent hat die Seite besucht, aber kein textuelles Ergebnis zurückgemeldet."
    return result

def send_to_inbox(content):
    msg = EmailMessage()
    msg['Subject'] = "Mersenne-Bot: Live-Bericht aus der Cloud"
    msg['From'] = os.getenv('EMAIL_USER')
    msg['To'] = os.getenv('EMAIL_RECEIVER')
    msg.set_content(f"Ergebnis der Session:\n\n{content}")

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(os.getenv('EMAIL_USER'), os.getenv('EMAIL_APP_PASSWORD'))
            smtp.send_message(msg)
        print("Erfolg: E-Mail versendet.")
    except Exception as e:
        print(f"Mail-Fehler: {e}")

async def main():
    extracted_data = await run_generic_agent()
    send_to_inbox(str(extracted_data))

if __name__ == "__main__":
    asyncio.run(main())
