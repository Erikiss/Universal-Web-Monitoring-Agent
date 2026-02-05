import os
import asyncio
import smtplib
from email.message import EmailMessage

from browser_use import Agent, Browser, ChatGroq  # <- WICHTIG: ChatGroq aus browser_use

async def run_generic_agent():
    print("--- START ---")

    llm = ChatGroq(
        model="llama-3.3-70b-versatile",
        api_key=os.getenv("GROQ_API_KEY"),
        temperature=0.1,
    )

    steel_key = os.getenv("STEEL_API_KEY")
    browser = Browser(cdp_url=f"wss://connect.steel.dev?apiKey={steel_key}")

    task = f"""
    1. Gehe zu {os.getenv('TARGET_URL')}.
    2. WARTE bis geladen.
    3. KLICKE auf 'Log in' oder 'Sign in'.
    4. FÜLLE AUS: User "{os.getenv('TARGET_USER')}" und Passwort "{os.getenv('TARGET_PW')}".
    5. KLICKE Login-Button.
    6. PRÜFE ob Login erfolgreich war (suche nach 'Logout' oder Usernamen).
    7. ERST DANN suche nach Berichten der letzten 4 Wochen.
    """

    agent = Agent(
        task=task,
        llm=llm,
        browser=browser,
        use_vision=False,   # <- Text-only
    )

    print("Agent startet (Text-Only)...")
    history = await agent.run()
    return history.final_result() or "Kein Ergebnis."

def send_to_inbox(content: str):
    msg = EmailMessage()
    msg["Subject"] = "Mersenne-Bot: Text-Only Lauf"
    msg["From"] = os.getenv("EMAIL_USER")
    msg["To"] = os.getenv("EMAIL_RECEIVER")
    msg.set_content(f"Bericht:\n\n{content}")

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(os.getenv("EMAIL_USER"), os.getenv("EMAIL_APP_PASSWORD"))
        smtp.send_message(msg)

async def main():
    try:
        result = await run_generic_agent()
        send_to_inbox(str(result))
        print("Mail gesendet.")
    except Exception as e:
        send_to_inbox(f"Fehler: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(main())
