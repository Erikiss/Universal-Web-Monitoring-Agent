import os
import asyncio
import smtplib
from email.message import EmailMessage
from langchain_openai import ChatOpenAI
# WICHTIG: BrowserConfig hinzufügen
from browser_use import Agent, Browser, BrowserConfig

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
        # Hier wird der tatsächliche API-Call zu Groq gemacht
        return await self.llm.ainvoke(*args, **kwargs)

async def run_generic_agent():
    # FIX: Hier binden wir Steel direkt als Browser-Infrastruktur ein
    # Ohne diesen Teil läuft der Browser nur "blind" auf dem GitHub-Server
    steel_key = os.getenv('STEEL_API_KEY')
    config = BrowserConfig(
        wss_url=f"wss://connect.steel.dev?apiKey={steel_key}"
    )
    browser = Browser(config=config)
    
    llm = SimpleGroqWrapper(
        model_name="llama-3.3-70b-versatile",
        api_key=os.getenv('GROQ_API_KEY')
    )

    task = f"""
    Gehe zu {os.getenv('TARGET_URL')}.
    Logge dich ein (User: "{os.getenv('TARGET_USER')}", PW: "{os.getenv('TARGET_PW')}").
    Extrahiere alle neuen Datenberichte der letzten 4 Wochen als Liste.
    Falls nichts gefunden wird, antworte: "Keine neuen Daten gefunden."
    """

    agent = Agent(task=task, llm=llm, browser=browser)
    history = await agent.run()
    
    # Sicherstellen, dass wir Text zurückgeben und kein leeres Objekt
    result = history.final_result()
    return result if result else "Agent beendet ohne explizites Ergebnis."

def send_to_inbox(content):
    msg = EmailMessage()
    msg['Subject'] = "Mersenne-Bot: Live-Bericht"
    msg['From'] = os.getenv('EMAIL_USER')
    msg['To'] = os.getenv('EMAIL_RECEIVER')
    msg.set_content(f"Ergebnis der KI-Analyse:\n\n{content}")

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
