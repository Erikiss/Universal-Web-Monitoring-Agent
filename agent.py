import os
import asyncio
import smtplib
from email.message import EmailMessage
from langchain_openai import ChatOpenAI
from browser_use import Agent, Browser
from pydantic import ConfigDict

# Wir erlauben der Klasse explizit, dass externe Tools (wie browser-use) 
# neue Felder wie 'provider' oder 'ainvoke' hinzufügen dürfen.
class GroqChatModel(ChatOpenAI):
    model_config = ConfigDict(extra='allow')
    provider: str = 'openai'

async def run_generic_agent():
    browser = Browser()
    
    # Initialisierung mit dem gelockerten Modell
    llm = GroqChatModel(
        model="llama-3.3-70b-versatile",
        api_key=os.getenv('GROQ_API_KEY'),
        base_url="https://api.groq.com/openai/v1"
    )

    target_url = os.getenv('TARGET_URL')
    user = os.getenv('TARGET_USER')
    pw = os.getenv('TARGET_PW')
    steel_key = os.getenv('STEEL_API_KEY')

    task = f"""
    Nutze den Steel-Browser unter 'wss://connect.steel.dev?apiKey={steel_key}'.
    1. Gehe zu {target_url}
    2. Logge dich ein mit User: "{user}" und Passwort: "{pw}".
    3. Suche nach neuen Datenberichten der letzten 4 WOCHEN.
    4. Extrahiere die Funde als strukturierte Liste.
    5. Handle rein lesend.
    """

    agent = Agent(task=task, llm=llm, browser=browser)
    history = await agent.run()
    await browser.close()
    return history.final_result()

def send_to_inbox(content):
    msg = EmailMessage()
    msg['Subject'] = "Automatisierter Datenbericht"
    msg['From'] = os.getenv('EMAIL_USER')
    msg['To'] = os.getenv('EMAIL_RECEIVER')
    msg.set_content(f"Hier sind die Ergebnisse der letzten 4 Wochen:\n\n{content}")

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(os.getenv('EMAIL_USER'), os.getenv('EMAIL_APP_PASSWORD'))
            smtp.send_message(msg)
        print("Erfolg: E-Mail wurde versendet.")
    except Exception as e:
        print(f"E-Mail-Fehler: {e}")

async def main():
    extracted_data = await run_generic_agent()
    send_to_inbox(str(extracted_data))

if __name__ == "__main__":
    asyncio.run(main())
