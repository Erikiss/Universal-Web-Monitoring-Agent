import os
import asyncio
import smtplib
from email.message import EmailMessage
from langchain_openai import ChatOpenAI
from browser_use import Agent, Browser

# Der ultimative Wrapper: Er hat alles, was der Agent verlangt
class SimpleGroqWrapper:
    def __init__(self, model_name, api_key):
        self.llm = ChatOpenAI(
            model=model_name,
            api_key=api_key,
            base_url="https://api.groq.com/openai/v1"
        )
        # Manuelle Definition aller Felder, die Fehler verursacht haben
        self.provider = 'openai'
        self.model = model_name

    # Reicht die Aufrufe an das echte LangChain-Modell weiter
    def __getattr__(self, name):
        return getattr(self.llm, name)

    async def ainvoke(self, *args, **kwargs):
        return await self.llm.ainvoke(*args, **kwargs)

async def run_generic_agent():
    browser = Browser()
    
    # Wir nutzen den Wrapper statt der direkten Vererbung
    llm = SimpleGroqWrapper(
        model_name="llama-3.3-70b-versatile",
        api_key=os.getenv('GROQ_API_KEY')
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
    msg.set_content(f"Ergebnisse der letzten 4 Wochen:\n\n{content}")

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
