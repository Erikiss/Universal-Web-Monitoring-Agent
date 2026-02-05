import os
import asyncio
import smtplib
from email.message import EmailMessage
from langchain_groq import ChatGroq
from browser_use import Agent, Browser

async def run_generic_agent():
    # In der neuesten Version übergeben wir die wss_url direkt an den Browser
    # oder nutzen eine interne BrowserConfig, die anders importiert wird.
    # Dieser Weg hier ist der stabilste:
    browser = Browser()
    
    # KI-Modell (Das Gehirn)
    llm = ChatGroq(model_name="llama-3.3-70b-versatile", api_key=os.getenv('GROQ_API_KEY'))

    # Variablen aus den GitHub Secrets
    target_url = os.getenv('TARGET_URL')
    user = os.getenv('TARGET_USER')
    pw = os.getenv('TARGET_PW')
    steel_key = os.getenv('STEEL_API_KEY')

    # Der Auftrag an die KI - inklusive Anweisung für den Steel-Browser
    task = f"""
    Nutze den Steel-Browser unter 'wss://connect.steel.dev?apiKey={steel_key}'.
    1. Gehe zu {target_url}
    2. Logge dich ein mit User: "{user}" und Passwort: "{pw}".
    3. Extrahiere die neuesten Datenberichte oder Tabellen als strukturierte Liste.
    4. Handle rein lesend.
    """

    agent = Agent(task=task, llm=llm, browser=browser)
    history = await agent.run()
    await browser.close()
    return history.final_result()

def send_to_inbox(content):
    msg = EmailMessage()
    msg['Subject'] = "Neuer Datenbericht"
    msg['From'] = os.getenv('EMAIL_USER')
    msg['To'] = os.getenv('EMAIL_RECEIVER')
    msg.set_content(f"Hier sind die extrahierten Daten für CrewAI:\n\n{content}")

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
