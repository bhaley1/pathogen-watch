"""SMTP email delivery (multipart text+html)."""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from . import config

log = logging.getLogger(__name__)


def send_email(subject: str, text_body: str, html_body: str) -> bool:
    """Send the digest. Returns True on success, False otherwise.

    Reads credentials from EmailConfig (environment-backed). Failures are
    logged but never raised — a missed digest shouldn't crash the workflow,
    and the rendered HTML is also written to disk as a backup.
    """
    cfg = config.EmailConfig()
    if not cfg.is_configured():
        log.warning("Email not configured (missing SMTP_HOST/USER/PASS/ALERT_FROM/TO); "
                    "skipping send. Digest still written to disk.")
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.from_addr
    msg["To"] = ", ".join(cfg.to_addrs)
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    try:
        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=60) as s:
            s.starttls()
            s.login(cfg.smtp_user, cfg.smtp_pass)
            s.send_message(msg)
        log.info("Sent digest to %s", cfg.to_addrs)
        return True
    except Exception as e:
        log.exception("SMTP send failed: %s", e)
        return False
