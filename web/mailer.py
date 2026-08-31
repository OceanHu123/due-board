from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

import httpx

from web.config import get_settings

log = logging.getLogger("due_board.mail")


class MailNotConfiguredError(RuntimeError):
    pass


def send_email(to: str, subject: str, text_body: str, html_body: str | None = None) -> None:
    settings = get_settings()
    if settings.resend_api_key.strip():
        _send_resend(to, subject, text_body, html_body)
        return
    if settings.smtp_host.strip():
        _send_smtp(to, subject, text_body, html_body)
        return
    if settings.require_mail:
        raise MailNotConfiguredError(
            "REQUIRE_MAIL is set but neither RESEND_API_KEY nor SMTP_HOST is configured"
        )
    log.warning("No mail provider configured — printing email to console")
    print("\n===== EMAIL (dev) =====")
    print(f"To: {to}")
    print(f"Subject: {subject}")
    print(text_body)
    print("===== END EMAIL =====\n")


def _send_resend(to: str, subject: str, text_body: str, html_body: str | None) -> None:
    settings = get_settings()
    payload = {
        "from": settings.smtp_from,
        "to": [to],
        "subject": subject,
        "text": text_body,
    }
    if html_body:
        payload["html"] = html_body
    r = httpx.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {settings.resend_api_key}"},
        json=payload,
        timeout=30.0,
    )
    if r.is_error:
        raise RuntimeError(f"Resend {r.status_code}: {r.text}") from None
    r.raise_for_status()


def _send_smtp(to: str, subject: str, text_body: str, html_body: str | None) -> None:
    settings = get_settings()
    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text_body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
        smtp.starttls()
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(msg)
