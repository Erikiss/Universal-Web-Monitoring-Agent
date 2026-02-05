import os
import asyncio
import smtplib
from email.message import EmailMessage
from langchain_openai import ChatOpenAI
from browser_use import Agent, Browser

async def run_generic_agent():
    browser = Browser()
    
    # Nutzt Groq über das OpenAI-Protokoll für maximale Stabilität
    llm = ChatOpenAI(
        model="llama-3.3-70b-versatile",
        api_key=os.getenv('GROQ_API_KEY'),
        base_url="https://api.groq.com/openai/v1"
    )

    target_url = os.getenv('TARGET_URL')
    user = os.getenv('TARGET_USER')
    pw = os.getenv('TARGET_PW')
    steel_key = os.getenv('STEEL_API_KEY')

    # Präziser Task mit Zeitbeschränkung auf 4 Wochen
    task = f"""
    Nutze den Steel-Browser unter 'wss://connect.steel.dev?apiKey={steel_key}'.
    1. Gehe zu {target_url}
    2. Logge dich ein mit User: "{user}" und Passwort: "{pw}".
    3. Suche nach neuen Datenberichten oder Tabellenbeiträgen.
    4. Extrahiere NUR Informationen, die innerhalb der letzten VIER WOCHEN (28 Tage) erstellt wurden.
    5. Handle rein lesend und extrahiere die Daten als strukturierte Liste.
    """

    agent = Agent(task=task, llm=llm, browser=browser)
    history = await agent.run()
    await browser.close()
    return history.final_result()

def send_to_inbox(content):
    msg = EmailMessage()
    msg['Subject'] = "Neuer Datenbericht (Letzte 4 Wochen)"
    msg['From'] = os.getenv('EMAIL_USER')
    msg['To'] = os.getenv('EMAIL_RECEIVER')
    msg.set_content(f"Hier sind die extrahierten Daten der letzten 4 Wochen für CrewAI:\n\n{content}")

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(os.getenv('EMAIL_USER'), os.getenv('EMAIL_APP_PASSWORD'))
            smtp.send_message(msg)
        print("Erfolg: Bericht versendet.")
    except Exception as e:
        print(f"Fehler beim Versand: {e}")

async def main():
    extracted_data = await run_generic_agent()
    send_to_inbox(str(extracted_data))

if __name__ == "__main__":
    asyncio.run(main())
