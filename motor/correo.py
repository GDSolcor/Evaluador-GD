"""
Envío de correos (invitaciones y recuperación de contraseña) por SMTP, sin librerías externas.

Configuración: datos/config_correo.json  (ver datos/config_correo.ejemplo.json)
    {"servidor": "smtp.office365.com", "puerto": 587, "tls": true,
     "usuario": "cuenta@dominio.com", "remitente": "Evaluador GD <cuenta@dominio.com>"}
La contraseña NO va en el archivo: se lee de la variable de entorno EVALUADOR_SMTP_CLAVE
(para Gmail/Outlook con verificación en dos pasos, usar una "contraseña de aplicación").

Si no hay configuración, no se envía nada: el enlace queda en el panel de administración.
"""
import json
import os
import smtplib
import ssl
from email.message import EmailMessage

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(BASE, "..", "datos", "config_correo.json")


def config():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            c = json.load(f)
    except (OSError, ValueError):
        return None
    c["clave"] = os.environ.get("EVALUADOR_SMTP_CLAVE", "")
    return c if c.get("servidor") and c.get("usuario") and c["clave"] else None


def configurado():
    return config() is not None


def estado():
    """Qué está listo y qué falta, sin exponer la contraseña."""
    try:
        with open(CONFIG, encoding="utf-8") as f:
            c = json.load(f)
    except (OSError, ValueError):
        c = {}
    return dict(archivo=bool(c.get("servidor") and c.get("usuario")), servidor=c.get("servidor", ""),
                usuario=c.get("usuario", ""), clave=bool(os.environ.get("EVALUADOR_SMTP_CLAVE")))


def armar(para, asunto, texto, html=None, imagenes=None, remitente=""):
    """Mensaje con texto y HTML. imagenes: {cid: (bytes, subtipo)} incrustadas en el HTML como <img src="cid:…">."""
    m = EmailMessage()
    m["From"] = remitente
    m["To"] = para
    m["Subject"] = asunto
    m.set_content(texto)
    if html:
        m.add_alternative(html, subtype="html")
        parte = m.get_payload()[-1]
        for cid, (datos, sub) in (imagenes or {}).items():
            parte.add_related(datos, "image", sub, cid=f"<{cid}>", disposition="inline", filename=f"{cid}.{sub}")
    return m


def enviar(para, asunto, texto, html=None, imagenes=None):
    """True si se envió; False si el correo no está configurado. Lanza excepción si el servidor falla."""
    if os.environ.get("EVALUADOR_SIN_CORREO"):
        # modo de pruebas: nada sale por SMTP; el mensaje queda como .eml para revisarlo
        import re
        import tempfile
        import time
        carpeta = os.path.join(tempfile.gettempdir(), "evaluador_correos_prueba")
        os.makedirs(carpeta, exist_ok=True)
        nombre = f"{time.strftime('%Y%m%d_%H%M%S')}_{re.sub(r'[^A-Za-z0-9]+', '_', asunto)[:40]}.eml"
        with open(os.path.join(carpeta, nombre), "wb") as fh:
            fh.write(bytes(armar(para, asunto, texto, html, imagenes, "Evaluador GD <pruebas@evaluador.test>")))
        return False
    c = config()
    if not c:
        return False
    m = armar(para, asunto, texto, html, imagenes, c.get("remitente") or c["usuario"])
    ctx = ssl.create_default_context()
    if int(c.get("puerto", 587)) == 465:
        with smtplib.SMTP_SSL(c["servidor"], 465, context=ctx, timeout=20) as s:
            s.login(c["usuario"], c["clave"])
            s.send_message(m)
    else:
        with smtplib.SMTP(c["servidor"], int(c.get("puerto", 587)), timeout=20) as s:
            if c.get("tls", True):
                s.starttls(context=ctx)
            s.login(c["usuario"], c["clave"])
            s.send_message(m)
    return True


def mensaje_enlace(nombre, enlace, tipo):
    if tipo == "invitacion":
        asunto = "Tu acceso al Evaluador de predios GD"
        intro = "Te dieron acceso al Evaluador de predios GD. Define tu contraseña en este enlace (vence en 7 días):"
    else:
        asunto = "Recuperación de contraseña · Evaluador de predios GD"
        intro = "Recibimos una solicitud para restablecer tu contraseña. Usa este enlace (vence en 60 minutos):"
    texto = f"Hola {nombre},\n\n{intro}\n\n{enlace}\n\nSi no solicitaste esto, ignora este mensaje.\n"
    html = (f"<p>Hola {nombre},</p><p>{intro}</p><p><a href=\"{enlace}\">{enlace}</a></p>"
            "<p style='color:#666'>Si no solicitaste esto, ignora este mensaje.</p>")
    return asunto, texto, html
