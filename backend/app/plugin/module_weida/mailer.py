"""威达发信通道：Foxmail SMTP，账号只读服务器本地配置文件。"""

from __future__ import annotations

import os
import smtplib
import ssl
from collections.abc import Iterable
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path

MAIL_ENV = Path(os.getenv("WEIDA_MAIL_ENV", "/home/ubuntu/weida/.secrets/mail.env"))


def _load() -> dict[str, str]:
    data: dict[str, str] = {}
    if MAIL_ENV.exists():
        for line in MAIL_ENV.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            data[key.strip()] = value.strip()
    for key in ("MAIL_SMTP_HOST", "MAIL_SMTP_PORT", "MAIL_SMTP_USER", "MAIL_SMTP_PASS"):
        if os.getenv(key):
            data[key] = os.environ[key]
    return data


def _profile(profile: str) -> tuple[str, str, str, str]:
    cfg = _load()
    prefix = "PRICING_" if profile == "pricing" else ""
    host = cfg.get(prefix + "MAIL_SMTP_HOST") or cfg.get("MAIL_SMTP_HOST") or "smtp.qq.com"
    port = cfg.get(prefix + "MAIL_SMTP_PORT") or cfg.get("MAIL_SMTP_PORT") or "465"
    user = cfg.get(prefix + "MAIL_SMTP_USER") or cfg.get("MAIL_SMTP_USER") or ""
    password = cfg.get(prefix + "MAIL_SMTP_PASS") or cfg.get("MAIL_SMTP_PASS") or ""
    if not user or not password:
        raise RuntimeError(f"邮件账号未配置（{profile}）")
    return host, port, user, password


def send_mail(
    to: str | Iterable[str],
    subject: str,
    body: str,
    *,
    profile: str = "default",
    attachments: list[tuple[str, bytes, str]] | None = None,
    html: str | None = None,
) -> None:
    host, port, user, password = _profile(profile)
    recipients = [to] if isinstance(to, str) else list(to)
    if not recipients:
        raise ValueError("收件人为空")
    msg = EmailMessage()
    msg["From"] = formataddr(("威达空运", user))
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")
    for name, content, mime in attachments or []:
        maintype, _, subtype = mime.partition("/")
        msg.add_attachment(content, maintype=maintype or "application", subtype=subtype or "octet-stream", filename=name)
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(host, int(port), context=context, timeout=30) as smtp:
        smtp.login(user, password)
        smtp.send_message(msg)
