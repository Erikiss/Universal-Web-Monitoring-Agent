import os
import asyncio
import smtplib
import json
from email.message import EmailMessage

from browser_use import Agent, Browser, ChatGroq


def _count_actions(obj, stats):
    if obj is None:
        return
    if isinstance(obj, list):
        for x in obj:
            _count_actions(x, stats)
        return
    if isinstance(obj, dict):
        # Standard: items-Liste
        if "items" in obj and isinstance(obj["items"], list):
            for it in obj["items"]:
                _count_actions(it, stats)

        t = obj.get("type") or obj.get("action")
        if isinstance(t, str):
            tl = t.lower()
            if "click" in tl:
                stats["clicks"] += 1
            elif "type" in tl or "fill" in tl or "input" in tl:
                stats["types"] += 1
            elif "wait" in tl:
                stats["waits"] += 1
            elif "scroll" in tl:
                stats["scrolls"] += 1
            elif "navigate" in tl or "goto" in tl:
                stats["navigates"] += 1

        for v in obj.values():
            _count_actions(v, stats)


def analyze_history(history):
    stats = {"clicks": 0, "types": 0, "waits": 0, "scrolls": 0, "navigates": 0, "errors": 0}
    for step in getattr(history, "history", []):
        if getattr(step, "error", None):
            stats["errors"] += 1

        mo = getattr(step, "model_output", None)
        if isinstance(mo, (dict, list)):
            _count_actions(mo, stats)
        elif mo is not None:
            s = str(mo).strip()
            if s.startswith("{") or s.startswith("["):
                try:
                    _count_actions(json.loads(s), stats)
                except Exception:
                    pass

        res = getattr(step, "result", None)
        if isinstance(res, (dict, list)):
            _count_actions(res, stats)

    report = (
        f"📊 TELEMETRIE\n"
        f"- Navigates: {stats['navigates']}\n"
        f"- Waits: {stats['waits']}\n"
        f"- Scrolls: {stats['scrolls']}\n"
        f"- Clicks: {stats['clicks']}\n"
        f"- Inputs: {stats['types']}\n"
        f"- Errors: {stats['errors']}\n"
    )
    return stats, report


async def run_robust_agent():
    llm = ChatGroq(
        model="llama-3.3-70b-versatile",
        api_key=os.getenv("GROQ_API_KEY"),
        temperature=0.35,  # robust, aber nicht driftig
    )

    browser = Browser(cdp_url=f"wss://connect.steel.dev?apiKey={os.getenv('STEEL_API_KEY')}")

    task = f"""
ROLE: Du bist ein robuster Web-Automation-Agent. Du MUSST handeln.
REGELN:
- Antworte NIE nur mit Text. Führe Aktionen aus.
- Vision ist AUS. Nutze ausschließlich DOM/Text/Attribute.
- Nach jedem UI-Trigger (Click auf Menu/Icon/Login): ACTION Wait 2 Sekunden.

ZIEL:
1) Login auf {os.getenv('TARGET_URL')}
2) Danach: finde "News" oder "Announcements" der letzten 4 Wochen und extrahiere Titel.

LOGIN-STRATEGIE (Plan A -> B -> C, bis Erfolg):

PLAN A (Text):
- Suche Buttons/Links mit sichtbarem Text: "Log in", "Login", "Sign in", "Anmelden".
- ACTION: Click best match.
- ACTION: Wait 2s.

PLAN B (Technisch):
- Wenn kein Text-Treffer: Suche Link-Elemente, deren href "login" oder "signin" enthält (z.B. /login).
- ACTION: Click.
- ACTION: Wait 2s.

PLAN C (Icon/Menu):
- Wenn immer noch nichts: Suche Header oben rechts nach Icon/Buttons mit aria-label/title/class, die "user", "account", "profile", "login" enthalten.
- Falls ein Menü aufklappt: ACTION: Click auf den Login/Sign in Eintrag.
- ACTION: Wait 2s.

FORMULAR (nach erfolgreichem Öffnen):
- Finde Username/Email input (type=text/email oder name/id enthält user/email/login).
- Finde Password input (type=password oder name/id enthält pass).
- ACTION: Type Username "{os.getenv('TARGET_USER')}".
- ACTION: Type Password "{os.getenv('TARGET_PW')}".
- ACTION: Click Submit/Login (Button mit "Log in"/"Sign in"/"Submit" oder type=submit).
- ACTION: Wait 5s.

ERFOLGSPRÜFUNG:
- Suche nach "Logout", "Sign out", "Abmelden" oder User/Profile-Link.
- Wenn NICHT gefunden: melde "Login fehlgeschlagen" und nenne, was du gesehen hast (z.B. Fehlermeldung auf Seite).

DANN:
- Extrahiere Titel der letzten 4 Wochen aus News/Announcements (Liste).
"""

    agent = Agent(task=task, llm=llm, browser=browser, use_vision=False)
    history = await agent.run()

    stats, tele_report = analyze_history(history)
    result = history.final_result() or "Kein Ergebnistext."
    return result, tele_report, stats


def send_to_inbox(content, tele_report, stats):
    # Betreff: klarer, aber ehrlich
    if stats["types"] >= 2 and stats["errors"] == 0:
        status = "🚀 LOGIN-VERSUCH"
    elif stats["clicks"] > 0:
        status = "✅ INTERAKTION"
    else:
        status = "⚠️ KEINE AKTION"

    subject = f"{status}: {stats['clicks']} Clicks, {stats['types']} Inputs, {stats['errors']} Errors"

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.getenv("EMAIL_USER")
    msg["To"] = os.getenv("EMAIL_RECEIVER")
    msg.set_content(f"{tele_report}\n\n📝 RESULT:\n{content}")

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(os.getenv("EMAIL_USER"), os.getenv("EMAIL_APP_PASSWORD"))
        smtp.send_message(msg)


async def main():
    try:
        content, tele_report, stats = await run_robust_agent()
        send_to_inbox(str(content), tele_report, stats)
        print("Mail gesendet.")
    except Exception as e:
        send_to_inbox(f"System-Crash: {e}", "Status: Crash", {"clicks": 0, "types": 0, "waits": 0, "scrolls": 0, "navigates": 0, "errors": 1})
        raise


if __name__ == "__main__":
    asyncio.run(main())
