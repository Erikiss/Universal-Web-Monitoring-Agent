import os
import asyncio
import smtplib
from email.message import EmailMessage
# Wir gehen zurück zum Original, da der Wrapper keine Tools (Klicks) konnte
from langchain_openai import ChatOpenAI
from browser_use import Agent, Browser

async def run_generic_agent():
    # 1. Browser Setup (Das funktioniert jetzt stabil!)
    steel_key = os.getenv('STEEL_API_KEY')
    # WICHTIG: cdp_url ist der korrekte Parameter für Steel
    browser = Browser(cdp_url=f"wss://connect.steel.dev?apiKey={steel_key}")
    
    # 2. LLM Setup (Das Gehirn)
    # Wir nutzen die offizielle OpenAI-Klasse, biegen sie aber auf Groq um.
    # Das ermöglicht dem Agenten, Buttons zu klicken (Tool Calling).
    llm = ChatOpenAI(
        model="llama-3.3-70b-versatile",
        api_key=os.getenv('GROQ_API_KEY'),
        base_url="https://api.groq.com/openai/v1",
        # Wichtig: Wir setzen max_tokens, damit er nicht abbricht
        max_tokens=1024
    )

    # 3. Der Auftrag
    target_url = os.getenv('TARGET_URL')
    user = os.getenv('TARGET_USER')
    pw = os.getenv('TARGET_PW')
    
    task = f"""
    Gehe zu {target_url}.
    Warte kurz bis die Seite geladen ist.
    Logge dich ein mit User: "{user}" und Passwort: "{pw}".
    Untersuche die Seite nach neuen Datenberichten der letzten 4 Wochen.
    Fasse die Ergebnisse zusammen.
    Wenn du keine neuen Daten findest, antworte: "Keine neuen Daten im Zeitraum gefunden."
    """

    # Agent starten
    agent = Agent(task=task, llm=llm, browser=browser)
    
    # Wir führen den Agenten aus und holen uns das Ergebnis
    history = await agent.run()
    
    # Ergebnis extrahieren
    result = history.final_result()
    
    # Falls das Ergebnis leer ist, holen wir uns den letzten Gedanken des Agenten
    if not result:
        try:
            result = history.history[-1].result
        except:
            result = "Der Agent lief durch, hat aber kein textuelles Ergebnis geliefert. Bitte Video auf Steel prüfen."
            
    return result

def send_to_inbox(content):
    msg = EmailMessage()
    msg['Subject'] = "Mersenne-Bot: Analyse-Ergebnis"
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
        print(f"Kritischer Fehler im Skript: {e}")
        send_to_inbox(f"Fehler: {e}")

if __name__ == "__main__":
    asyncio.run(main())
