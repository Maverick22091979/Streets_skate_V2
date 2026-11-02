from __future__ import annotations

import smtplib
from email.message import EmailMessage


def send_smtp_notification(
    host: str,
    port: int,
    username: str,
    password: str,
    sender: str,
    recipient: str,
    subject: str,
    body: str,
    use_tls: bool = True,
    use_ssl: bool = False,
) -> None:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    client_type = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
    with client_type(host, port, timeout=20) as client:
        if use_tls and not use_ssl:
            client.starttls()
        if username:
            client.login(username, password)
        client.send_message(message)
