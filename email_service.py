"""
Serviço de e-mail — NutriIA
===========================
Envio via SMTP (biblioteca padrão, sem dependências extras). Funciona com qualquer provedor:
Gmail (senha de app), Brevo, Resend (SMTP), SendGrid, Mailgun, Amazon SES, etc.

Variáveis de ambiente:
  SMTP_HOST       ex: smtp.gmail.com | smtp-relay.brevo.com | smtp.resend.com
  SMTP_PORT       587 (STARTTLS, padrão) ou 465 (SSL)
  SMTP_USER       usuário/login SMTP
  SMTP_PASSWORD   senha ou chave de API SMTP
  SMTP_FROM       remetente, ex: NutriIA <no-reply@seudominio.com> (padrão: SMTP_USER)
"""

import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
from html import escape


def email_configurado() -> bool:
    return bool(os.environ.get("SMTP_HOST") and os.environ.get("SMTP_USER") and os.environ.get("SMTP_PASSWORD"))


def enviar_email(destinatario: str, assunto: str, texto: str, html: str = None) -> bool:
    """Envia um e-mail. Retorna True em caso de sucesso (nunca levanta exceção)."""
    if not email_configurado():
        print("[EMAIL] SMTP não configurado (defina SMTP_HOST, SMTP_USER e SMTP_PASSWORD). E-mail NÃO enviado.")
        return False

    host = os.environ["SMTP_HOST"]
    porta = int(os.environ.get("SMTP_PORT", "587"))
    usuario = os.environ["SMTP_USER"]
    senha = os.environ["SMTP_PASSWORD"]
    remetente = os.environ.get("SMTP_FROM") or usuario

    msg = EmailMessage()
    msg["Subject"] = assunto
    nome_rem, end_rem = parseaddr(remetente)
    msg["From"] = formataddr((nome_rem or "NutriIA", end_rem or usuario))
    msg["To"] = destinatario
    msg.set_content(texto)
    if html:
        msg.add_alternative(html, subtype="html")

    try:
        contexto = ssl.create_default_context()
        if porta == 465:
            with smtplib.SMTP_SSL(host, porta, timeout=15, context=contexto) as smtp:
                smtp.login(usuario, senha)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(host, porta, timeout=15) as smtp:
                smtp.starttls(context=contexto)
                smtp.login(usuario, senha)
                smtp.send_message(msg)
        return True
    except Exception as e:
        print(f"[EMAIL] Falha ao enviar e-mail: {e}")
        return False


def enviar_email_recuperacao(destinatario: str, nome: str, link: str, minutos_validade: int) -> bool:
    nome_html = escape(nome or "")
    link_html = escape(link, quote=True)
    texto = (
        f"Olá, {nome}!\n\n"
        "Recebemos um pedido para redefinir a senha da sua conta NutriIA.\n"
        f"Acesse o link abaixo para criar uma nova senha (válido por {minutos_validade} minutos):\n\n"
        f"{link}\n\n"
        "Se você não fez esse pedido, ignore este e-mail: sua senha continua a mesma."
    )
    html = f"""\
<div style="font-family:Inter,Arial,sans-serif;background:#F7F7F2;padding:24px;">
  <div style="max-width:480px;margin:0 auto;background:#FFFFFF;border-radius:20px;padding:32px;border:1px solid #E2E8E5;">
    <h2 style="margin:0 0 4px;color:#26332E;">Nutri<span style="color:#245C4A;">IA</span></h2>
    <p style="color:#5C6E66;margin:0 0 24px;font-size:14px;">Redefinição de senha</p>
    <p style="color:#26332E;font-size:15px;">Olá, <strong>{nome_html}</strong>!</p>
    <p style="color:#26332E;font-size:15px;line-height:1.5;">
      Recebemos um pedido para redefinir a senha da sua conta. Clique no botão abaixo para criar uma nova senha.
      O link é válido por {minutos_validade} minutos e só pode ser usado uma vez.
    </p>
    <p style="text-align:center;margin:28px 0;">
      <a href="{link_html}" style="background:#245C4A;color:#FFFFFF;text-decoration:none;padding:14px 28px;border-radius:14px;font-weight:600;display:inline-block;">Criar nova senha</a>
    </p>
    <p style="color:#5C6E66;font-size:12px;line-height:1.5;">
      Se o botão não funcionar, copie e cole este endereço no navegador:<br>
      <span style="word-break:break-all;">{link_html}</span>
    </p>
    <p style="color:#5C6E66;font-size:12px;">Se você não fez esse pedido, ignore este e-mail: sua senha continua a mesma.</p>
  </div>
</div>"""
    return enviar_email(destinatario, "NutriIA — Redefinição de senha", texto, html)
