import os
import asyncio
import smtplib
from email.message import EmailMessage

from browser_use import Agent, Browser, ChatGroq


# -------------------------
# Telemetrie (robust)
# -------------------------
def _count_actions(obj, stats):
    """Zählt Click/Type/Scroll/Wait aus beliebig verschachtelten Dict/List-Strukturen."""
    if obj is None:
        return

    if isinstance(obj, list):
        for x in obj:
            _count_actions(x, stats)
        return

    if isinstance(obj, dict):
        # häufiges browser-use Format: {"items":[...]}
        if "items" in obj and isinstance(obj["items"], list):
            for it in obj["items"]:
                _count_actions(it, stats)

        t = obj.get("type") or obj.get("action") or obj.get("name")
        if isinstance(t, str):
            tl = t.lower()
            if "click" in tl:
                stats["clicks"] += 1
            elif "type" in tl or "fill" in tl or "input" in tl:
                stats["types"] += 1
            elif "scroll" in tl:
                stats["scrolls"] += 1
            elif "wait" in tl:
                stats["waits"] += 1

        # weiter in allen Feldern suchen
        for v in obj.values():
            _count_actions(v, stats)


def analyze_history(history):
    """
    Versucht so viel wie möglich auszulesen – auch wenn model_output/steps variieren.
    """
    stats = {"clicks": 0, "types": 0, "scrolls": 0, "waits": 0, "errors": 0}

    # 1) history.history (steps)
    try:
        for step in getattr(history, "history", []) or []:
            # Fehler
            if getattr(step, "error", None):
                stats["errors"] += 1

            # model_output kann dict/str/obj sein
            mo = getattr(step, "model_output", None)
            if mo is not None:
                if isinstance(mo, (dict, list)):
                    _count_actions(mo, stats)
                else:
                    # fallback: string-heuristik
                    s = str(mo).lower()
                    stats["clicks"] += s.count("click")
                    stats["types"] += s.count("type")
                    stats["scrolls"] += s.count("scroll")
                    stats["waits"] += s.count("wait")
    except Exception:
        pass

    report = (
        "📊 TELEMETRIE\n"
        f"- Clicks:   {stats['clicks']}\n"
        f"- Inputs:   {stats['types']}\n"
        f"- Scrolls:  {stats['scrolls']}\n"
        f"- Waits:    {stats['waits']}\n"
        f"- Errors:   {stats['errors']}\n"
    )
    return stats, report


# -------------------------
# Mail
# -------------------------
def send_to_inbox(subject, body):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.getenv("EMAIL_USER")
    msg["To"] = os.getenv("EMAIL_RECEIVER")
    msg.set_content(body)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(os.getenv("EMAIL_USER"), os.getenv("EMAIL_APP_PASSWORD"))
        smtp.send_message(msg)


# -------------------------
# Agent Run
# -------------------------
async def run_agent():
    # Steel
    steel_key = os.getenv("STEEL_API_KEY")
    browser = Browser(cdp_url=f"wss://connect.steel.dev?apiKey={steel_key}")

    # Groq: WICHTIG -> Modell, das json_schema kann (sonst 400 / keine Aktionen)
    # browser-use listet u.a.:
    # - meta-llama/llama-4-scout-17b-16e-instruct
    # - meta-llama/llama-4-maverick-17b-128e-instruct
    # - openai/gpt-oss-20b / openai/gpt-oss-120b
    groq_model = os.getenv("GROQ_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")

    llm = ChatGroq(
        api_key=os.getenv("GROQ_API_KEY"),
        model=groq_model,
        temperature=float(os.getenv("GROQ_TEMPERATURE", "0.4")),
    )

    task = f"""
ROLE: Du bist ein Automations-Agent. Du MUSST Aktionen ausführen (Click/Type), nicht nur beschreiben.
REGELN:
- Vision ist AUS. Nutze ausschließlich DOM/Text.
- Wenn du einen Login-Link findest: klicken -> Felder füllen -> Submit.

ZIEL:
1) Öffne {os.getenv('TARGET_URL')}.
2) Finde Login:
   - Plan A: Text "Log in" / "Sign in" / "Anmelden"
   - Plan B: Link mit href enthält "login"
   - Plan C: User/Icon oben rechts (Profil) -> Login
3) Nach dem Klick:
   - Warte kurz (2s)
   - Fülle User: "{os.getenv('TARGET_USER')}"
   - Fülle Password: "{os.getenv('TARGET_PW')}"
   - Klicke Submit/Login
4) Prüfe Erfolg: "Logout" / Profil / Username sichtbar.
5) Danach: finde neue Berichte/Posts der letzten 4 Wochen.
Wenn nichts: gib "Keine neuen Daten gefunden." zurück.
"""

    agent = Agent(
        task=task,
        llm=llm,
        browser=browser,
        use_vision=False,
    )

    history = await agent.run()
    stats, tele = analyze_history(history)

    result = None
    try:
        result = history.final_result()
    except Exception:
        result = None

    if not result:
        # fallback: letzter Step dump
        try:
            last = history.history[-1]
            result = getattr(last, "result", None) or getattr(last, "error", None) or str(getattr(last, "model_output", ""))[:4000]
        except Exception:
            result = "Kein Ergebnistext."

    return groq_model, stats, tele, str(result)


async def main():
    try:
        model, stats, tele, result = await run_agent()

        icon = "🚀" if stats["types"] > 0 else ("✅" if stats["clicks"] > 0 else "⚠️")
        subject = f"{icon} Mersenne-Bot ({model}): {stats['clicks']} Clicks, {stats['types']} Inputs, {stats['errors']} Errors"

        body = f"{tele}\n\n📝 RESULT\n{result}\n"
        send_to_inbox(subject, body)

    except Exception as e:
        send_to_inbox("❌ Mersenne-Bot: Crash", f"Systemfehler:\n{e}")


if __name__ == "__main__":
    asyncio.run(main())
