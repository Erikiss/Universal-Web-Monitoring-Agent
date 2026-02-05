import os
import asyncio
import smtplib
from email.message import EmailMessage
from langchain_openai import ChatOpenAI
from browser_use import Agent, Browser

# Der stabile Wrapper für Groq: Umgeht Pydantic-Fehler und fehlende Attribute
class SimpleGroqWrapper:
    def __init__(self, model_name, api_key):
        self.llm = ChatOpenAI(
            model=model_name,
            api_key=api_key,
            base_url="https://api.groq.com/openai/v1"
        )
        # Explizite Definition der vom Framework erwarteten Felder
        self.provider = 'openai'
        self.model = model_name

    # Reicht alle anderen Anfragen an das interne LangChain-Modell weiter
    def __getattr__(self, name):
        return getattr(self.llm, name)

    async def ainvoke(self, *args, **kwargs):
        return await self.llm.ainvoke(*args, **kwargs)

async def run_generic_agent():
    # Browser-Instanz starten
    browser = Browser()
    
    # Initialisierung des LLMs über unseren Wrapper
    llm = SimpleGroqWrapper(
        model_name="llama-3.3-70b-versatile",
        api_key=os.getenv('GROQ_API_KEY')
    )

    # Laden der Zugangsdaten aus den GitHub Secrets
    steel_key = os.getenv('STEEL_API_KEY')
    target_url = os.getenv('TARGET_URL')
    user = os.getenv('TARGET_USER')
    pw = os.getenv('TARGET_PW')

    # Der präzise Auftrag für die KI (Fokus auf die letzten 4 Wochen)
    task = f"""
    Nutze den Steel-Browser unter 'wss://connect.steel.dev?apiKey={steel_key}'.
    1. Gehe zu {target_url}
    2. Logge dich ein mit User: "{user}" und Passwort: "{pw}".
    3. Suche nach neuen Datenberichten oder Tabellenbeiträgen der letzten 4 WOCHEN (28 Tage).
    4. Extrahiere die Funde als strukturierte Liste.
    5. Handle rein lesend.
    """

    agent = Agent(task=task, llm=llm, browser=browser)
    history = await agent.run()
    
    # Hinweis: browser.close() wird weggelassen, um Versions-Konflikte zu vermeiden.
    # Der Prozess wird durch GitHub Actions sauber beendet.
    return history.final_result()

def send_to_inbox(content):
    # E-Mail-Konfiguration für Gmail
    msg = EmailMessage()
    msg['Subject'] = "Automatisierter Datenbericht (Letzte 4 Wochen)"
    msg['From'] = os.getenv('EMAIL_USER')
    msg['To'] = os.getenv('EMAIL_RECEIVER')
    msg.set_content(f"Hier sind die extrahierten Daten für die Weiterverarbeitung:\n\n{content}")

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(os.getenv('EMAIL_USER'), os.getenv('EMAIL_APP_PASSWORD'))
            smtp.send_message(msg)
        print("Erfolg: Bericht versendet.")
    except Exception as e:
        print(f"Fehler beim E-Mail-Versand: {e}")

async def main():
    # Hauptablauf: Scrapen und dann Versenden
    extracted_data = await run_generic_agent()
    send_to_inbox(str(extracted_data))

if __name__ == "__main__":
    asyncio.run(main())
