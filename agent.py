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

# --- 2. Der Adapter (Sicherheitshalber, für Provider-Check) ---
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
    
    # 3. LLM Setup (Groq Native)
    real_llm = ChatGroq(
        model="llama-3.3-70b-versatile",
        api_key=os.getenv('GROQ_API_KEY'),
        # Etwas Temperatur hilft Llama manchmal, nicht in Loops zu geraten
        temperature=0.1 
    )

    llm_wrapper = GroqAdapter(real_llm)

    # 4. Browser Setup (Steel)
    steel_key = os.getenv('STEEL_API_KEY')
    browser = Browser(cdp_url=f"wss://connect.steel.dev?apiKey={steel_key}")

    # 5. Der Task (Login erzwingen)
    task = f"""
    1. Gehe zu {os.getenv('TARGET_URL')}.
    2. WARTE bis geladen.
    3. KLICKE auf 'Log in' oder 'Sign in'.
    4. FÜLLE AUS: User "{os.getenv('TARGET_USER')}" und Passwort "{os.getenv('TARGET_PW')}".
    5. KLICKE Login-Button.
    6. PRÜFE ob Login erfolgreich war (suche nach 'Logout' oder Usernamen).
    7. ERST DANN suche nach Berichten der letzten 4 Wochen.
    """

    # --- DER FIX: use_vision=False ---
    # Wir zwingen den Agenten, nur Text (DOM) zu nutzen.
    # Das verhindert den "items"-Fehler bei Groq!
    agent = Agent(
        task=task, 
        llm=llm_wrapper, 
        browser=browser,
        use_vision=False 
    )
    
    print("Agent startet (Text-Only Modus)...")
    history = await agent.run()
    
    # Ergebnis-Diagnose
    result = history.final_result()
    if not result:
        try: 
            last_step = history.history[-1]
            # Wir holen uns auch den Fehler, falls einer da ist
            result = f"Letzter Status: {last_step.model_dump_json()}"
        except: 
            result = "Kein Ergebnis und keine History."
            
    return result

def send_to_inbox(content):
    msg = EmailMessage()
    msg['Subject'] = "Mersenne-Bot: Text-Only Lauf"
    msg['From'] = os.getenv('EMAIL_USER')
    msg['To'] = os.getenv('EMAIL_RECEIVER')
    msg.set_content(f"Bericht:\n\n{content}")

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
        send_to_inbox(str(data))
    except Exception as e:
        print(f"CRASH: {e}")
        send_to_inbox(f"Fehler: {e}")

if __name__ == "__main__":
    asyncio.run(main())
