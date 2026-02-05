import os
import asyncio
import smtplib
from email.message import EmailMessage
from langchain_openai import ChatOpenAI
from browser_use import Agent, Browser

# Unser bewährter Wrapper für Groq (bleibt unverändert)
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
    # Die Adresse für den Steel-Browser
    wss_url = f"wss://connect.steel.dev?apiKey={steel_key}"
    
    # FIX: Der Parameter heißt 'cdp_url' (Chrome DevTools Protocol).
    # Wir übergeben ihn direkt, ohne 'config'-Objekt.
    browser = Browser(cdp_url=wss_url)
    
    llm = SimpleGroqWrapper(
        model_name="llama-3.3-70b-versatile",
        api_key=os.getenv('GROQ_API_KEY')
    )

    # Präziser Task, damit du keine "None"-Mail mehr bekommst
    task = f"""
    1. Gehe zu {os.getenv('TARGET_URL')}.
    2. Warte kurz, bis die Seite geladen ist.
    3. Logge dich ein (User: "{os.getenv('TARGET_USER')}", PW: "{os.getenv('TARGET_PW')}").
    4. Suche nach neuen Datenberichten oder Foreneinträgen der letzten 4 Wochen.
    5. Fasse die Ergebnisse zusammen. 
    6. Falls du unsicher bist oder nichts findest, schreibe: "Seite besucht, aber keine Daten identifiziert."
    """

    agent = Agent(task=task, llm=llm, browser=browser)
    history = await agent.run()
    
    result = history.final_result()
    return result if result else "Agent hat gearbeitet, aber keinen Text zurückgegeben."

def send_to_inbox(content):
    msg = EmailMessage()
    msg['Subject'] = "Mersenne-Bot: Bericht aus der Cloud"
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
