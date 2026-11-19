import smtplib
from email.message import EmailMessage

from app.config import get_settings


class EmailNotConfiguredError(Exception):
    """SMTP isn't configured -- callers turn this into a 503, same
    pattern as the R2/PayPal gate checks elsewhere in this codebase."""


def send_email(to: str, subject: str, body: str) -> None:
    """Send a single plain-text email over SMTP+STARTTLS.

    Synchronous (real network I/O) -- callers from a request handler
    should dispatch this via scheduler.tasks.send_email_task.delay()
    instead of calling it inline, same as every other slow external
    call in this codebase.
    """
    settings = get_settings()
    if not settings.smtp_host:
        raise EmailNotConfiguredError("SMTP не настроен на сервере")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.smtp_from_address
    message["To"] = to
    message.set_content(body)

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
        smtp.starttls()
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(message)
