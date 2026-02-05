import os
import asyncio
import smtplib
from email.message import EmailMessage

from browser_use import Agent, Browser, ChatGroq

async def run_generic_agent():
    print("--- START: Action-Zwang (stabil) ---")

    llm = ChatGroq(
        model="llama-3.3-70b-versatile",
        api_key=os.getenv("GROQ_API_KEY"),
        temperature=0.3,  # start moderat; wenn nötig auf 0.5 erhöhen
    )

    steel_key = os.getenv("STEEL_API_KEY")
    browser = Browser(cdp_url=f"wss://connect.steel.dev?apiKey={steel_key}")

    task = f"""
    ROLE: Du bist ein Automatisierungs-Bot. Du MUSST Aktionen ausführen.

    NICHT ERLAUBT:
    - Nur beobachten
    - Nur Text antworten
    - Prosa/Erklärungen

    HARTE REGEL:
    - Gib IMMER mindestens 1 Aktion aus (Click/Type/Wait/Navigate).
    - Wenn du ein Login findest, MUSST du es ausführen.

    ABLAUF (zwingend):
    1. Gehe zu {os.getenv('TARGET_URL')}.
    2. Suche im DOM nach "Log in", "Sign in", "Anmelden", "Login".
    3. ACTION: Klicke den passenden Link/Button.
    4. ACTION: Tippe User "{os.getenv('TARGET_USER')}" in das erste passende Username/Email-Input.
    5. ACTION: Tippe Passwort "{os.getenv('TARGET_PW')}" in das Password-Input.
    6. ACTION: Klicke Submit/Login.
    7. ACTION: Warte 5 Sekunden.
    8. Prüfe Login-Erfolg (Logout/Profil/Username). Falls nicht eingeloggt: Versuche alternative Login-Buttons/Inputs.
    9. Erst bei Erfolg: Extrahiere Berichte der letzten 4 Wochen.

    OUTPUT: Nur strukturierte Aktionen. Keine Prosa.
    """

    agent = Agent(
        task=task,
        llm=llm,
        browser=browser,
        use_vision=False,
    )

    history = await agent.run()
    return history.final_result() or "Kein Ergebnis."

def send_to_inbox(content: str):
    msg = EmailMessage()
    msg["Subject"] = "Mersenne-Bot: Action-Zwang Lauf"
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
