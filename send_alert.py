"""Send an alert email via SMTP.

All personal data (recipient address, SMTP account) comes exclusively from
environment variables that GitHub Actions injects from repository secrets, so
nothing private ever appears in this public repository or in workflow logs.

Required environment variables:
  SMTP_HOST, SMTP_USERNAME, SMTP_PASSWORD, ALERT_EMAIL_TO
Optional:
  SMTP_PORT (default 587; 465 uses implicit TLS), ALERT_EMAIL_FROM
"""

from __future__ import annotations

import argparse
import os
import smtplib
import ssl
import sys
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

REQUIRED_VARS = ("SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "ALERT_EMAIL_TO")


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subject", required=True)
    parser.add_argument("--body-file", required=True)
    args = parser.parse_args(argv)

    missing = [name for name in REQUIRED_VARS if not os.getenv(name)]
    if missing:
        raise SystemExit(
            "ERROR: alert email is not configured. Add these repository "
            f"secrets: {', '.join(missing)}"
        )

    body = Path(args.body_file).read_text(encoding="utf-8")
    host = os.environ["SMTP_HOST"]
    # A secret that is not configured arrives as an empty string, not unset.
    port = int(os.getenv("SMTP_PORT") or "587")
    username = os.environ["SMTP_USERNAME"]
    password = os.environ["SMTP_PASSWORD"]
    sender = os.getenv("ALERT_EMAIL_FROM") or username
    recipients = [
        address.strip()
        for address in os.environ["ALERT_EMAIL_TO"].split(",")
        if address.strip()
    ]
    if not recipients:
        raise SystemExit("ERROR: ALERT_EMAIL_TO contains no address.")

    message = EmailMessage()
    message["Subject"] = args.subject
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = make_msgid()
    message.set_content(body)

    # Scheduled-run logs are public: never let recipient/credentials reach
    # stdout/stderr, not even inside an SMTP error message or traceback.
    private = recipients + [username, password, sender]
    try:
        refused = deliver(host, port, username, password, message)
    except (smtplib.SMTPException, OSError) as exc:
        raise SystemExit(f"ERROR: SMTP delivery failed: {scrub(str(exc), private)}")
    if refused:
        raise SystemExit(f"ERROR: server refused {len(refused)} recipient(s).")

    print(f"Alert email sent to {len(recipients)} recipient(s).")


def deliver(host, port, username, password, message):
    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=60) as client:
            client.login(username, password)
            return client.send_message(message)
    with smtplib.SMTP(host, port, timeout=60) as client:
        client.starttls(context=context)
        client.login(username, password)
        return client.send_message(message)


def scrub(text: str, private: list[str]) -> str:
    for value in private:
        if value:
            text = text.replace(value, "***")
    return text


if __name__ == "__main__":
    main(sys.argv[1:])
