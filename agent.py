import os
import asyncio
import smtplib
from email.message import EmailMessage
from langchain_openai import ChatOpenAI
from browser_use import Agent, Browser

# --- DAS TROJANISCHE PFERD (Der Wrapper) ---
class GroqAdapter:
    """
    Dieser Adapter verhält sich wie ein LLM, ist aber nicht 'frozen'.
    Er erlaubt browser-use, Attribute wie 'ainvoke' zu verändern,
    leitet aber die echte Arbeit an Groq weiter.
    """
    def __init__(self, llm):
        self.llm = llm
        # Wir geben dem Framework genau das, was es sucht:
        self.provider = "openai"
        self.model_name = "llama-3.3-70b-versatile"
        
    # Wenn browser-use 'ainvoke' aufruft oder überschreiben will, klappt das hier,
    # weil wir eine normale Python-Klasse sind (kein Pydantic).
    async def ainvoke(self, *args, **kwargs):
        return await self.llm.ainvoke(*args, **kwargs)

    # Alle anderen Anfragen (z.B. Tools binden) leiten wir blind weiter
    def __getattr__(self, name):
        return getattr(self.llm, name)

async def run_generic_agent():
    # 1. Steel Browser verbinden
    steel_key = os.getenv('STEEL_API_KEY')
    browser = Browser(cdp_url=f"wss://connect.steel.dev?apiKey={steel_key}")
    
    # 2. Das echte Gehirn initialisieren
    real_llm = ChatOpenAI(
        model="llama-3.3-70b-versatile",
        api_key=os.getenv('GROQ_API_KEY'),
        base_url="https://api.groq.com/openai/v1",
        max_tokens=1024,
        temperature=0.1
    )
    
    # 3. Das Gehirn in den Adapter stecken (Schutzhülle)
    llm_wrapper = GroqAdapter(real_llm)

    # 4. Der Auftrag
    task = f"""
    1. Gehe zu {os.getenv('TARGET_URL')}.
    2. Logge dich ein (User: "{os.getenv('TARGET_USER')}", PW: "{os.getenv('TARGET_PW')}").
    3. Untersuche die Seite nach neuen Datenberichten der letzten 4 Wochen.
    4. Wenn du Daten findest, extrahiere sie als Liste.
    5. WICHTIG: Wenn KEINE neuen Daten da sind, antworte: "Keine neuen Daten gefunden."
    """

    # Wir geben dem Agenten den Wrapper, nicht das Original!
    agent = Agent(task=task, llm=llm_wrapper, browser=browser)
    
    history = await agent.run()
    
    result = history.final_result()
    if not result:
        try:
            result = history.history[-1].result
        except:
            result = "Agent lief durch, aber Ergebnis war leer."
            
    return result

def send_to_inbox(content):
    msg = EmailMessage()
    msg['Subject'] = "Mersenne-Bot: Bericht"
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
