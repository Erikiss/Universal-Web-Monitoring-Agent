import os
import asyncio
import smtplib
import subprocess
import sys
from email.message import EmailMessage

# --- 1. Auto-Installation ---
try:
    from langchain_groq import ChatGroq
except ImportError:
    print("Installiere langchain-groq...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "langchain-groq"])
    from langchain_groq import ChatGroq

from browser_use import Agent, Browser

# --- 2. Der Adapter (Der Ausweis-Fälscher) ---
class GroqAdapter:
    def __init__(self, llm):
        self.llm = llm
        # Wir geben dem strengen "Buchhalter" von browser-use genau das, was er sehen will:
        self.provider = "openai"  # Wir tarnen uns als OpenAI, damit die Kostenrechnung durchläuft
        self.model_name = "llama-3.3-70b-versatile"
        self.model = "llama-3.3-70b-versatile"
        
    async def ainvoke(self, *args, **kwargs):
        # Die harte Arbeit macht das echte Groq-Modell
        return await self.llm.ainvoke(*args, **kwargs)

    def __getattr__(self, name):
        # Alle Werkzeuge (Tools) werden durchgereicht
        return getattr(self.llm, name)

async def run_generic_agent():
    print("--- START ---")
    
    # 3. Das echte Hirn (Groq)
    real_llm = ChatGroq(
        model="llama-3.3-70b-versatile",
        api_key=os.getenv('GROQ_API_KEY'),
        temperature=0.1
    )

    # 4. Der Ping-Test (Sicherstellen, dass Groq wach ist)
    print("Teste Groq Verbindung...")
    try:
        ping = await real_llm.ainvoke("Sag kurz 'Hallo'")
        print(f"Groq antwortet: {ping.content}")
    except Exception as e:
        return f"Groq Ping gescheitert: {e}"

    # 5. Browser starten
    print("Verbinde mit Steel...")
    steel_key = os.getenv('STEEL_API_KEY')
    browser = Browser(cdp_url=f"wss://connect.steel.dev?apiKey={steel_key}")

    # 6. Den Agenten mit dem Adapter starten
    # WICHTIG: Wir übergeben 'llm_wrapper', nicht 'real_llm'!
    llm_wrapper = GroqAdapter(real_llm)
    
    task = f"""
    1. Gehe zu {os.getenv('TARGET_URL')}.
    2. Logge dich ein (User: "{os.getenv('TARGET_USER')}", PW: "{os.getenv('TARGET_PW')}").
    3. Untersuche die Seite nach neuen Datenberichten der letzten 4 Wochen.
    4. Wenn du Daten findest, extrahiere sie als Liste.
    5. WICHTIG: Wenn KEINE neuen Daten da sind, antworte: "Keine neuen Daten gefunden."
    """

    agent = Agent(task=task, llm=llm_wrapper, browser=browser)
    
    print("Starte Agenten-Lauf...")
    history = await agent.run()
    
    result = history.final_result()
    if not result:
        try: result = history.history[-1].result
        except: result = "Agent lief durch, Ergebnis leer."
            
    return result

def send_to_inbox(content):
    msg = EmailMessage()
    msg['Subject'] = "Mersenne-Bot: Hybrid-Lauf"
    msg['From'] = os.getenv('EMAIL_USER')
    msg['To'] = os.getenv('EMAIL_RECEIVER')
    msg.set_content(f"Bericht:\n\n{content}")

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(os.getenv('EMAIL_USER'), os.getenv('EMAIL_APP_PASSWORD'))
            smtp.send_message(msg)
        print("E-Mail versendet.")
    except Exception as e:
        print(f"Mail-Fehler: {e}")

async def main():
    try:
        data = await run_generic_agent()
        send_to_inbox(str(data))
    except Exception as e:
        print(f"CRASH: {e}")
        send_to_inbox(f"Kritischer Fehler: {e}")

if __name__ == "__main__":
    asyncio.run(main())
