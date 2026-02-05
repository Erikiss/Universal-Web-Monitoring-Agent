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

# --- 2. Der Adapter (Damit Steel & Groq sich verstehen) ---
class GroqAdapter:
    def __init__(self, llm):
        self.llm = llm
        self.provider = "openai" 
        self.model_name = "llama-3.3-70b-versatile"
        self.model = "llama-3.3-70b-versatile"
        
    async def ainvoke(self, *args, **kwargs):
        return await self.llm.ainvoke(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.llm, name)

async def run_generic_agent():
    print("--- START ---")
    
    # 3. LLM Setup
    real_llm = ChatGroq(
        model="llama-3.3-70b-versatile",
        api_key=os.getenv('GROQ_API_KEY'),
        temperature=0.0 # Null Kreativität = Maximale Präzision
    )

    llm_wrapper = GroqAdapter(real_llm)

    # 4. Browser Setup
    steel_key = os.getenv('STEEL_API_KEY')
    browser = Browser(cdp_url=f"wss://connect.steel.dev?apiKey={steel_key}")

    # 5. Der "Idiotensichere" Auftrag
    # Wir zwingen den Agenten, visuell zu bestätigen, bevor er tippt.
    task = f"""
    1. Gehe zu {os.getenv('TARGET_URL')}.
    2. WARTE bis die Seite vollständig geladen ist.
    3. Suche nach dem Login-Feld oder Button.
    4. Logge dich ein (User: "{os.getenv('TARGET_USER')}", PW: "{os.getenv('TARGET_PW')}").
    5. WARTE nach dem Login 5 Sekunden, bis das Dashboard sichtbar ist.
    6. Erst DANN suche nach 'New Posts' oder Berichten der letzten 4 Wochen.
    7. Fasse zusammen, was du gefunden hast.
    """

    agent = Agent(task=task, llm=llm_wrapper, browser=browser)
    
    print("Agent startet...")
    history = await agent.run()
    
    # Ergebnis-Diagnose
    result = history.final_result()
    if not result:
        # Wenn leer, holen wir uns die letzte Aktion
        try: 
            last_step = history.history[-1]
            result = f"Kein Endergebnis. Letzter Gedanke des Agenten: {last_step.model_dump_json()}"
        except: 
            result = "Agent hat nichts zurückgegeben."
            
    return result

def send_to_inbox(content):
    msg = EmailMessage()
    msg['Subject'] = "Mersenne-Bot: Bericht"
    msg['From'] = os.getenv('EMAIL_USER')
    msg['To'] = os.getenv('EMAIL_RECEIVER')
    msg.set_content(f"Log-Auszug:\n\n{content}")

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(os.getenv('EMAIL_USER'), os.getenv('EMAIL_APP_PASSWORD'))
            smtp.send_message(msg)
        print("Mail gesendet.")
    except Exception as e:
        print(f"Mail Fehler: {e}")

async def main():
    try:
        data = await run_generic_agent()
        print(f"Ergebnis: {data}")
        send_to_inbox(str(data))
    except Exception as e:
        print(f"CRASH: {e}")
        send_to_inbox(f"Fehler: {e}")

if __name__ == "__main__":
    asyncio.run(main())
