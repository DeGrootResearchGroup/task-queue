from unittest.mock import MagicMock, patch

from app.config import get_settings
from app.email import send_email


def test_email_enabled_reflects_credentials(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "smtp_username", "")
    monkeypatch.setattr(settings, "smtp_password", "")
    assert settings.email_enabled is False

    monkeypatch.setattr(settings, "smtp_username", "bot@example.com")
    monkeypatch.setattr(settings, "smtp_password", "app-password")
    assert settings.email_enabled is True


def test_send_email_noop_when_not_configured(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "smtp_username", "")
    monkeypatch.setattr(settings, "smtp_password", "")

    with patch("app.email.smtplib.SMTP") as mock_smtp:
        send_email("someone@example.com", "Subject", "Body", "Sender")
        mock_smtp.assert_not_called()


def test_send_email_noop_with_blank_recipient(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "smtp_username", "bot@example.com")
    monkeypatch.setattr(settings, "smtp_password", "app-password")

    with patch("app.email.smtplib.SMTP") as mock_smtp:
        send_email("", "Subject", "Body", "Sender")
        mock_smtp.assert_not_called()


def test_send_email_sends_via_starttls_with_correct_content(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "smtp_host", "smtp.gmail.com")
    monkeypatch.setattr(settings, "smtp_port", 587)
    monkeypatch.setattr(settings, "smtp_username", "bot@example.com")
    monkeypatch.setattr(settings, "smtp_password", "app-password")

    mock_server = MagicMock()
    mock_server.__enter__.return_value = mock_server

    with patch("app.email.smtplib.SMTP", return_value=mock_server) as mock_smtp:
        send_email("recipient@example.com", "Hello", "Body text", "Test Sender")

    mock_smtp.assert_called_once_with("smtp.gmail.com", 587, timeout=10)
    mock_server.starttls.assert_called_once()
    mock_server.login.assert_called_once_with("bot@example.com", "app-password")
    mock_server.send_message.assert_called_once()

    sent_msg = mock_server.send_message.call_args[0][0]
    assert sent_msg["To"] == "recipient@example.com"
    assert sent_msg["Subject"] == "Hello"
    assert "Test Sender" in sent_msg["From"]
    assert "bot@example.com" in sent_msg["From"]
    assert sent_msg.get_content().strip() == "Body text"


def test_send_email_swallows_smtp_errors(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "smtp_username", "bot@example.com")
    monkeypatch.setattr(settings, "smtp_password", "app-password")

    with patch("app.email.smtplib.SMTP", side_effect=OSError("connection refused")):
        send_email("recipient@example.com", "Hello", "Body", "Sender")  # must not raise
