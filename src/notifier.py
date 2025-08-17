import logging
import smtplib
from email.mime.text import MIMEText
from typing import Optional

from .config.settings import (
    EMAIL_FROM,
    EMAIL_TO,
    ENABLE_EMAIL,
    ENABLE_NOTIFICATIONS,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_USER,
)
from .db import record_notification


class Notifier:
    def __init__(self) -> None:
        self.enabled = ENABLE_NOTIFICATIONS

    def send_email(self, subject: str, body: str) -> bool:
        if not self.enabled or not ENABLE_EMAIL:
            return False
        if not (SMTP_HOST and EMAIL_FROM and EMAIL_TO):
            logging.warning("Email not sent; SMTP or recipients not configured")
            return False
        try:
            msg = MIMEText(body)
            msg["Subject"] = subject
            msg["From"] = EMAIL_FROM
            msg["To"] = EMAIL_TO

            server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10)
            try:
                server.starttls()
            except Exception:
                pass
            if SMTP_USER and SMTP_PASSWORD:
                server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(EMAIL_FROM, [EMAIL_TO], msg.as_string())
            server.quit()
            logging.info("Notification email sent to %s", EMAIL_TO)
            return True
        except Exception as e:
            logging.error("Failed to send email: %s", e)
            return False

    def send_email_to(self, to: str, subject: str, body: str) -> bool:
        """Send an email to a specific recipient instead of the default EMAIL_TO."""
        if not self.enabled or not ENABLE_EMAIL:
            return False
        if not (SMTP_HOST and EMAIL_FROM and to):
            logging.warning("Email not sent; SMTP or recipients not configured")
            return False
        try:
            msg = MIMEText(body)
            msg["Subject"] = subject
            msg["From"] = EMAIL_FROM
            msg["To"] = to

            server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10)
            try:
                server.starttls()
            except Exception:
                pass
            if SMTP_USER and SMTP_PASSWORD:
                server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(EMAIL_FROM, [to], msg.as_string())
            server.quit()
            logging.info("Notification email sent to %s", to)
            try:
                record_notification("email", to, None, "sent")
            except Exception:
                pass
            return True
        except Exception as e:
            logging.error("Failed to send email to %s: %s", to, e)
            return False

    def notify(self, subject: str, body: str) -> None:
        # Email-only notifications
        email_ok = self.send_email(subject, body)
        if not email_ok:
            logging.info("No notifications sent (disabled or not configured)")
        else:
            try:
                record_notification("email", EMAIL_TO, None, "sent")
            except Exception:
                pass

    # Diagnostics: SMS removed; no-op methods retained for backward-compat
    def get_message_status(self, sid: str) -> Optional[dict]:
        return None

    def get_last_message(self, to: Optional[str] = None) -> Optional[dict]:
        return None
