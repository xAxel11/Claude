import email
import imaplib
import smtplib
from email.header import decode_header, make_header
from email.message import EmailMessage

from .. import config
from ..bridge import bridge
from ._base import tool


def _check_creds():
    if not (config.EMAIL_ADDRESS and config.EMAIL_PASSWORD):
        raise RuntimeError("Email isn't set up. Add EMAIL_ADDRESS and EMAIL_PASSWORD to the .env file.")


@tool
def send_email(to: str, subject: str, body: str) -> str:
    """Send an email from the user's account. The user is asked to approve it first."""
    _check_creds()
    if not bridge.confirm(f"Send this email?\n\nTo: {to}\nSubject: {subject}\n\n{body[:800]}"):
        return "The user cancelled the email."
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = config.EMAIL_ADDRESS, to, subject
    msg.set_content(body)
    with smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, timeout=20) as smtp:
        smtp.login(config.EMAIL_ADDRESS, config.EMAIL_PASSWORD)
        smtp.send_message(msg)
    return f"Email sent to {to}."


def _text_body(msg):
    parts = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        if part.get_content_type() == "text/plain" and not part.get_filename():
            payload = part.get_payload(decode=True) or b""
            return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    return ""


@tool
def read_emails(count: int = 5, unread_only: bool = True) -> str:
    """Read the most recent emails in the inbox (sender, subject and a short preview)."""
    _check_creds()
    with imaplib.IMAP4_SSL(config.IMAP_HOST) as imap:
        imap.login(config.EMAIL_ADDRESS, config.EMAIL_PASSWORD)
        imap.select("INBOX", readonly=True)
        _, data = imap.search(None, "UNSEEN" if unread_only else "ALL")
        ids = data[0].split()[-count:][::-1]
        if not ids:
            return "No unread emails." if unread_only else "The inbox is empty."
        out = []
        for i in ids:
            _, msg_data = imap.fetch(i, "(BODY.PEEK[])")
            msg = email.message_from_bytes(msg_data[0][1])
            sender = str(make_header(decode_header(msg.get("From", ""))))
            subject = str(make_header(decode_header(msg.get("Subject", ""))))
            preview = " ".join(_text_body(msg).split())[:200]
            out.append(f"From: {sender}\nSubject: {subject}\n{preview}")
    return "\n\n".join(out)


TOOLS = [send_email, read_emails]
