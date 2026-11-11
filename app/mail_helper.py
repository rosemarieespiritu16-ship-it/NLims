from flask import current_app
from flask_mail import Message
from .extensions import mail


def send_email(recipients, subject, html_body, body=None):
    if not isinstance(recipients, list):
        recipients = [recipients]
    with current_app.app_context():
        username = current_app.config.get('MAIL_USERNAME')
        password = current_app.config.get('MAIL_PASSWORD')
        if not username or not password:
            raise RuntimeError('SMTP_USER and SMTP_PASS are not set in .env')

        sender = current_app.config.get('MAIL_DEFAULT_SENDER') or username
        msg = Message(
            subject=subject,
            recipients=recipients,
            body=body,
            html=html_body,
            sender=sender
        )
        mail.send(msg)
