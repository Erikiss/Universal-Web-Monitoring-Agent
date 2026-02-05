import os
import asyncio
import smtplib
from email.message import EmailMessage
from langchain_openai import ChatOpenAI
from browser_use import Agent, Browser

async def run_generic_agent():
    # 1. Browser Setup (Steel Verbindung)
    steel_key = os.getenv('STEEL_API_KEY')
    browser = Browser(cdp_url=f"wss://connect.steel.dev?apiKey={steel_key}")
    
    # 2. LLM Setup (Das Gehirn)
    # Wir nutzen ChatOpenAI für volle Kompatibilität (Tools, Clicks, Inputs)
    llm = ChatOpenAI(
        model="llama-3.3-70b-versatile",
        api_key=os.getenv('GROQ_API_KEY'),
        base_url="https://api.groq.com/openai/v1",
        max_tokens=1024
    )
    
    # FIX: Der chirurgische Eingriff
    # Wir zwingen die fehlenden Attribute in das Objekt, ohne dass Pydantic meckert.
    # Das behebt den "AttributeError: 'ChatOpenAI' object has no attribute 'provider'"
    object.__setattr__(llm, 'provider', 'openai')
    object.__setattr__(llm, 'model_name', 'llama-3.3-70b-versatile')

    # 3. Der Auftrag
    target_url = os.getenv('TARGET_URL')
    user = os.getenv('TARGET_USER')
    pw = os.getenv('TARGET_PW')
    
    task = f"""
    1. Gehe zu {target_url}.
    2. Logge dich ein (User: "{user}", PW: "{pw}").
    3. Suche nach neuen Datenberichten der letzten 4 Wochen.
    4. Wenn du Daten findest, extrahiere sie als Liste.
    5. WICHTIG: Wenn KEINE neuen Daten da sind, antworte: "Keine neuen Daten gefunden."
    """

    agent = Agent(task=task, llm=llm, browser=browser)
    
    # Ausführen
    history = await agent.run()
    
    # Ergebnis abholen
    result = history.final_result()
    if not result:
        # Fallback, falls die KI nichts Explizites zurückgibt
        try:
            result = history.history[-1].result
        except:
            result = "Agent lief erfolgreich durch, aber das Textergebnis ist leer."
            
    return result

def send_to_inbox(content):
    msg = EmailMessage()
    msg['Subject'] = "Mersenne-Bot: Erfolgreicher Scan"
    msg['From'] = os.getenv('EMAIL_USER')
    msg['To'] = os.getenv('EMAIL_RECEIVER')
    msg.set_content(f"Bericht vom Agenten:\n\n{content}")

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(os.getenv('EMAIL_USER'), os.getenv('EMAIL_APP_PASSWORD'))
            smtp.send_message(msg)
        print("Erfolg: E-Mail versendet.")
    except Exception as e:
        print(f"Mail-Fehler: {e}")

async def main():
    try:
        extracted_data = await run_generic_agent()
        send_to_inbox(str(extracted_data))
    except Exception as e:
        print(f"Kritischer Fehler: {e}")
        send_to_inbox(f"Systemfehler: {e}")

if __name__ == "__main__":
    asyncio.run(main())
