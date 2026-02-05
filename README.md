# Universal-Web-Monitoring-Agent
Dieses Repository enthält einen KI-gesteuerten Browser-Agenten, der autonom Websites besucht, sich einloggt, Daten extrahiert und diese per E-Mail für die Weiterverarbeitung (z.B. durch CrewAI) versendet.


Hier ist ein Entwurf für eine professionelle README.md, die du direkt in dein GitHub-Repository legen kannst. Sie dient als "Gebrauchsanweisung" für dich, damit du auch in Zukunft sofort weißt, wie das System funktioniert und wie du es auf eine andere Website umstellst.
🤖 Universal Web Monitoring Agent
Dieses Repository enthält einen KI-gesteuerten Browser-Agenten, der autonom Websites besucht, sich einloggt, Daten extrahiert und diese per E-Mail für die Weiterverarbeitung (z.B. durch CrewAI) versendet.
🚀 Funktionsweise
 * GitHub Actions startet täglich (oder manuell) einen virtuellen Runner.
 * Der Agent verbindet sich mit dem Steel Browser (Cloud-Infrastruktur).
 * Das Groq LLM (Llama 3.3) steuert den Browser basierend auf generischen Anweisungen.
 * Die extrahierten Daten werden über einen Gmail SMTP-Server mit einem sicheren App-Passwort versendet.
🛠 Einrichtung (Secrets)
Damit das System funktioniert, müssen folgende Repository Secrets in GitHub angelegt werden (Settings > Secrets and variables > Actions):
| Secret | Beschreibung | Beispiel |
|---|---|---|
| TARGET_URL | Die Website, die überwacht werden soll | https://example.com |
| TARGET_USER | Benutzername für den Login | dein_user |
| TARGET_PW | Passwort für den Login | dein_passwort |
| STEEL_API_KEY | API Key von steel.dev | steel-xxx |
| GROQ_API_KEY | API Key von console.groq.com | gsk-xxx |
| EMAIL_USER | Deine vollständige Gmail-Adresse | name@gmail.com |
| EMAIL_RECEIVER | Zieladresse für den Bericht | name@gmail.com |
| EMAIL_APP_PASSWORD | 16-stelliger Code von Google | abcdefghijklmnop |
📂 Dateien
 * agent.py: Das Hauptskript. Es ist webseiten-neutral programmiert.
 * .github/workflows/daily_run.yml: Die Automatisierungs-Logik für GitHub.
🔄 Website wechseln
Um eine andere Website zu überwachen, musst du keinen Code ändern. Passe einfach die TARGET_URL und die Login-Daten in den GitHub Secrets an. Die KI erkennt automatisch, wo sich die Login-Felder und Tabellen auf der neuen Seite befinden.
📧 Weiterverarbeitung (CrewAI)
Der Agent sendet E-Mails mit dem Betreff Neuer Datenbericht. Eine nachgelagerte CrewAI-Instanz kann diese Mails filtern:
 * Trigger: Suche nach Betreff "Neuer Datenbericht".
 * Action: Analysiere den Body, filtere Änderungen heraus und speichere sie in der Datenbank.
Ein kleiner Tipp für die Zukunft:
Falls die neue Website, die du überwachen willst, extrem komplex ist, kannst du im Code (agent.py) den task String noch um einen Satz ergänzen wie: "Suche speziell nach der Tabelle im Menüpunkt 'XYZ'". Aber für die meisten Foren und Portale wird der generische Befehl dank Llama 3.3 perfekt funktionieren.
Damit ist dein System komplett und zukunftssicher! Soll ich dir noch bei etwas anderem helfen?
