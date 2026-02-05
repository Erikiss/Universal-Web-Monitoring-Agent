import os
import asyncio
import smtplib
import subprocess
import sys
from email.message import EmailMessage

# --- SCHRITT 0: Auto-Installation der Abhängigkeiten ---
try:
    from langchain_groq import ChatGroq
except ImportError:
    print("⚠️ Installiere langchain-groq...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "langchain-groq"])
    from langchain_groq import ChatGroq

from browser_use import Agent, Browser

async def run_diagnostics_and_agent():
    print("--- START DIAGNOSE ---")
    
    # SCHRITT 1: Umgebungsvariable prüfen
    api_key = os.getenv('GROQ_API_KEY')
    is_set = bool(api_key)
    print(f"1. Env Variable GROQ_API_KEY gefunden? -> {is_set}")
    
    if not is_set:
        print("❌ ABBRUCH: Kein API-Key in den Umgebungsvariablen gefunden.")
        return "Fehler: Kein API-Key."

    if len(api_key) < 10:
        print(f"❌ WARNUNG: API-Key scheint zu kurz zu sein ({len(api_key)} Zeichen).")

    # SCHRITT 2: Der Groq-Ping (Isolierter Test)
    print("2. Teste Verbindung zu Groq (Ping)...")
    try:
        # Wir nutzen 'model' statt 'model_name' für maximale Kompatibilität
        test_llm = ChatGroq(
            model="llama-3.3-70b-versatile",
            api_key=api_key,
            temperature=0.1
        )
        # Ein winziger Call, nur um zu sehen, ob die Leitung steht
        response = await test_llm.ainvoke("Antworte nur mit einem Wort: PONG")
        print(f"✅ Groq Ping Erfolgreich! Antwort: {response.content}")
    except Exception as e:
        print(f"❌ Groq Ping GESCHEITERT: {e}")
        print("URSACHE: Wahrscheinlich falscher Key, falsches Projekt oder Guthaben leer.")
        return f"Diagnose-Fehler: {e}"

    print("--- DIAGNOSE ENDE (System bereit) ---")

    # SCHRITT 3: Der eigentliche Agent (Nur wenn Diagnose OK)
    print("3. Starte Browser-Agent...")
    steel_key = os.getenv('STEEL_API_KEY')
    browser = Browser(cdp_url=f"wss://connect.steel.dev?apiKey={steel_key}")
    
    task = f"""
    1. Gehe zu {os.getenv('TARGET_URL')}.
    2. Logge dich ein (User: "{os.getenv('TARGET_USER')}", PW: "{os.getenv('TARGET_PW')}").
    3. Suche nach neuen Datenberichten der letzten 4 Wochen.
    4. Wenn KEINE neuen Daten da sind, antworte: "Keine neuen Daten gefunden."
    """

    # Wir nutzen das getestete LLM-Objekt weiter
    agent = Agent(task=task, llm=test_llm, browser=browser)
    
    history = await agent.run()
    result = history.final_result()
    
    if not result:
        # Fallback für leere Ergebnisse
        try:
            result = history.history[-1].result
        except:
            result = "Agent lief durch, aber Ergebnis leer."
            
    return result

def send_to_inbox(content):
    msg = EmailMessage()
    msg['Subject'] = "Mersenne-Bot: Diagnose & Bericht"
    msg['From'] = os.getenv('EMAIL_USER')
    msg['To'] = os.getenv('EMAIL_RECEIVER')
    msg.set_content(f"Protokoll:\n\n{content}")

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(os.getenv('EMAIL_USER'), os.getenv('EMAIL_APP_PASSWORD'))
            smtp.send_message(msg)
        print("Erfolg: E-Mail versendet.")
    except Exception as e:
        print(f"Mail-Fehler: {e}")

async def main():
    try:
        extracted_data = await run_diagnostics_and_agent()
        send_to_inbox(str(extracted_data))
    except Exception as e:
        print(f"Kritischer Fehler im Main-Loop: {e}")
        send_to_inbox(f"Kritischer Fehler: {e}")

if __name__ == "__main__":
    asyncio.run(main())
