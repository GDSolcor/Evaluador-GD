"""
Envío de correos (invitaciones, recuperación de contraseña y correo de proyecto), sin librerías externas.

La cuenta de envío se elige en el panel de administración (Usuarios → Correo de salida) y queda en
datos/config_correo.json. Métodos:

1. «smtp» (cuenta con usuario y contraseña): Microsoft 365 (smtp.office365.com) o Gmail (smtp.gmail.com).
   En Microsoft 365 requiere que la cuenta tenga habilitado «SMTP autenticado» (SMTP AUTH). Microsoft lo
   apaga por defecto a finales de diciembre de 2026, pero TI puede volver a activarlo por buzón.
2. «graph» (Microsoft Graph, aplicación registrada en Entra ID con Mail.Send): no depende de SMTP AUTH y
   sirve también para buzones compartidos como tramites@solcor.com.co.

La contraseña o el secreto NO va en el archivo: se guarda cifrado con DPAPI de Windows (solo lo puede
leer el usuario de Windows que corre el servidor) en datos/correo_secreto.bin. Si no hay secreto guardado
y la configuración viene del archivo editado a mano, se usa la variable de entorno EVALUADOR_SMTP_CLAVE
o EVALUADOR_GRAPH_SECRETO, como antes.

Si no hay configuración completa, no se envía nada: el enlace queda en el panel de administración.
"""
import base64
import ctypes
import json
import os
import re
import smtplib
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.environ.get("EVALUADOR_CORREO_CONFIG") or os.path.join(BASE, "..", "datos", "config_correo.json")
SECRETO = os.path.join(os.path.dirname(CONFIG), "correo_secreto.bin")
VERIFICACION = os.path.join(os.path.dirname(CONFIG), "correo_verificacion.json")
SERVIDORES = {"office365": ("smtp.office365.com", 587), "gmail": ("smtp.gmail.com", 587)}
_token = {"valor": "", "vence": 0.0}


# --- resultado del último envío real: el panel solo dice «listo» si la cuenta actual ya envió algo ---
def _firma(c):
    """Identifica la cuenta configurada: si cambia algo (o se vuelve a guardar), el resultado anterior no vale."""
    return "|".join(str(c.get(k, "")) for k in ("metodo", "servidor", "puerto", "usuario", "remitente",
                                                  "tenant", "cliente", "buzon", "actualizado"))


def _registrar_resultado(c, ok, detalle=""):
    try:
        tmp = VERIFICACION + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(dict(firma=_firma(c), ok=ok, fecha=time.strftime("%Y-%m-%d %H:%M"), detalle=detalle[:300]),
                      f, ensure_ascii=False, indent=2)
        os.replace(tmp, VERIFICACION)
    except OSError:
        pass  # no poder anotar el resultado nunca debe impedir el envío


def verificacion(c=None):
    """{ok: True|False|None, fecha, detalle} del último envío con la cuenta actual (None = sin probar)."""
    c = _leer() if c is None else c
    try:
        with open(VERIFICACION, encoding="utf-8") as f:
            v = json.load(f)
    except (OSError, ValueError):
        v = {}
    if v.get("firma") != _firma(c):
        return dict(ok=None, fecha="", detalle="")
    return dict(ok=bool(v.get("ok")), fecha=v.get("fecha", ""), detalle=v.get("detalle", ""))


def _leer():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _metodo(c):
    return c.get("metodo") or ("graph" if c.get("buzon") else "smtp")


# --- secreto cifrado con DPAPI (Windows); en otros sistemas, archivo con permisos solo del dueño ---
class _Blob(ctypes.Structure):
    _fields_ = [("n", ctypes.c_uint32), ("p", ctypes.POINTER(ctypes.c_char))]


def _dpapi(datos, cifrar):
    buf = ctypes.create_string_buffer(datos, len(datos))
    entrada = _Blob(len(datos), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    salida = _Blob()
    f = ctypes.windll.crypt32.CryptProtectData if cifrar else ctypes.windll.crypt32.CryptUnprotectData
    if not f(ctypes.byref(entrada), "EvaluadorGD" if cifrar else None, None, None, None, 0x1, ctypes.byref(salida)):
        raise OSError("DPAPI no pudo procesar el secreto del correo")
    try:
        return ctypes.string_at(salida.p, salida.n)
    finally:
        ctypes.windll.kernel32.LocalFree(salida.p)


def _guardar_secreto(valor):
    if not valor:
        if os.path.exists(SECRETO):
            os.remove(SECRETO)
        return
    datos = valor.encode("utf-8")
    datos = _dpapi(datos, True) if os.name == "nt" else datos
    fd = os.open(SECRETO, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(datos)


def _secreto_guardado():
    try:
        with open(SECRETO, "rb") as fh:
            datos = fh.read()
        return (_dpapi(datos, False) if os.name == "nt" else datos).decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _secreto(c):
    s = _secreto_guardado()
    if s or c.get("desde_panel"):  # lo configurado en el panel nunca toma la clave de otra cuenta del entorno
        return s
    return os.environ.get("EVALUADOR_GRAPH_SECRETO" if _metodo(c) == "graph" else "EVALUADOR_SMTP_CLAVE", "")


def config():
    c = _leer()
    if _metodo(c) == "outlook":  # Outlook clásico de este PC: la sesión la tiene Outlook, no hay secreto
        return c if c.get("usuario") and os.name == "nt" else None
    if _metodo(c) == "graph":
        c["secreto"] = _secreto(c)
        return c if c.get("tenant") and c.get("cliente") and c.get("buzon") and c["secreto"] else None
    c["clave"] = _secreto(c)
    return c if c.get("servidor") and c.get("usuario") and c["clave"] else None


def configurado():
    return config() is not None


def estado():
    """Qué está listo y qué falta, sin exponer secretos (también alimenta el formulario del panel)."""
    c = _leer()
    nombre, direccion = parseaddr(c.get("remitente", ""))
    base = dict(metodo=_metodo(c), nombre=nombre or "Evaluador GD", clave=bool(_secreto(c)), verificacion=verificacion(c),
                variable="" if c.get("desde_panel") else
                ("EVALUADOR_GRAPH_SECRETO" if _metodo(c) == "graph" else "EVALUADOR_SMTP_CLAVE"),
                actualizado=c.get("actualizado", ""), actualizado_por=c.get("actualizado_por", ""))
    if base["metodo"] == "outlook":
        return dict(base, archivo=bool(c.get("usuario")), clave=True, variable="", servidor="Outlook de este PC",
                    proveedor="outlook", usuario=c.get("usuario", ""), remitente=direccion or c.get("usuario", ""))
    if base["metodo"] == "graph":
        return dict(base, archivo=bool(c.get("tenant") and c.get("cliente") and c.get("buzon")),
                    servidor="Microsoft Graph", usuario=c.get("buzon", ""), remitente=c.get("buzon", ""),
                    tenant=c.get("tenant", ""), cliente=c.get("cliente", ""))
    prov = next((k for k, (srv, _) in SERVIDORES.items() if srv == c.get("servidor")), "otro")
    return dict(base, archivo=bool(c.get("servidor") and c.get("usuario")), servidor=c.get("servidor", ""),
                puerto=int(c.get("puerto", 587)), proveedor=prov, usuario=c.get("usuario", ""),
                remitente=direccion or c.get("usuario", ""))


def _de(nombre, direccion):
    nombre = re.sub(r'["<>\\]', "", nombre)
    return f'"{nombre}" <{direccion}>' if nombre else direccion


_CORREO = re.compile(r"[^@\s<>,]+@[^@\s<>,]+\.[^@\s<>,]+")


def guardar(datos, por=""):
    """Guarda la cuenta de envío elegida en el panel. datos: metodo, proveedor (office365|gmail|otro),
    servidor, puerto, usuario, remitente (dirección «De», por defecto la cuenta), nombre, clave (vacía = conservar
    la actual si la cuenta no cambió), tenant y cliente (Graph). Devuelve estado()."""
    ant = _leer()
    metodo = datos.get("metodo", "smtp")
    nombre = (datos.get("nombre") or "Evaluador GD").strip()[:60]
    c = {"metodo": metodo, "desde_panel": True, "actualizado": time.strftime("%Y-%m-%d %H:%M"), "actualizado_por": por}
    if metodo == "graph":
        buzon = (datos.get("remitente") or datos.get("usuario") or "").strip().lower()
        tenant, cliente = (datos.get("tenant") or "").strip(), (datos.get("cliente") or "").strip()
        if not (_CORREO.fullmatch(buzon) and tenant and cliente):
            raise ValueError("Para Microsoft Graph se necesitan el buzón, el id del directorio (tenant) y el id de la aplicación.")
        c.update(tenant=tenant, cliente=cliente, buzon=buzon, remitente=_de(nombre, buzon))
        misma = (_metodo(ant), ant.get("tenant"), ant.get("cliente")) == ("graph", tenant, cliente)
    elif metodo == "smtp":
        usuario = (datos.get("usuario") or "").strip().lower()
        if not _CORREO.fullmatch(usuario):
            raise ValueError("La cuenta de correo no es válida.")
        servidor, puerto = SERVIDORES.get(datos.get("proveedor"), (None, None))
        servidor = servidor or (datos.get("servidor") or "").strip()
        puerto = int(puerto or datos.get("puerto") or 587)
        if not servidor:
            raise ValueError("Falta el servidor SMTP.")
        if servidor.lower() == "smtp.office365.com":  # tenant de Solcor: SMTP AUTH apagado (535 5.7.139)
            raise ValueError("Microsoft 365 no acepta envío por SMTP en Solcor. Elige «Outlook de este PC».")
        de = (datos.get("remitente") or usuario).strip().lower()
        if not _CORREO.fullmatch(de):
            raise ValueError("La dirección del remitente no es válida.")
        c.update(servidor=servidor, puerto=puerto, tls=puerto != 465, usuario=usuario, remitente=_de(nombre, de))
        misma = (_metodo(ant), ant.get("servidor"), ant.get("usuario")) == ("smtp", servidor, usuario)
    elif metodo == "outlook":
        usuario = (datos.get("usuario") or "").strip().lower()
        de = (datos.get("remitente") or usuario).strip().lower()
        if not (_CORREO.fullmatch(usuario) and _CORREO.fullmatch(de)):
            raise ValueError("Escribe la cuenta con la que Outlook inició sesión en este PC.")
        c.update(usuario=usuario, remitente=_de(nombre, de), perfil=(datos.get("perfil") or "Outlook").strip())
        misma = True
    else:
        raise ValueError("Método de envío no válido.")
    if metodo == "outlook":  # Outlook guarda su propia sesión: no hay contraseña que pedir ni guardar
        _guardar_secreto("")
    else:
        clave = datos.get("clave") or ""
        actual = _secreto(ant) if misma else ""
        if not clave and not actual:
            raise ValueError("Escribe la contraseña de la cuenta (o el secreto de la aplicación).")
        _guardar_secreto(clave or actual)
    tmp = CONFIG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG)
    _token.update(valor="", vence=0.0)
    try:  # cuenta o contraseña nuevas: hay que volver a probar
        os.remove(VERIFICACION)
    except OSError:
        pass
    return estado()


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


def _post(url, cuerpo, cabeceras):
    req = urllib.request.Request(url, data=cuerpo, headers=cabeceras, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:  # el detalle de Graph explica el permiso o el dato que falta
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}") from None


def _token_graph(c):
    if _token["valor"] and time.time() < _token["vence"] - 120:
        return _token["valor"]
    cuerpo = urllib.parse.urlencode(dict(client_id=c["cliente"], client_secret=c["secreto"],
                                         scope="https://graph.microsoft.com/.default",
                                         grant_type="client_credentials")).encode()
    _, r = _post(f"https://login.microsoftonline.com/{c['tenant']}/oauth2/v2.0/token", cuerpo,
                 {"Content-Type": "application/x-www-form-urlencoded"})
    j = json.loads(r)
    _token.update(valor=j["access_token"], vence=time.time() + int(j.get("expires_in", 3600)))
    return _token["valor"]


def _enviar_graph(c, para, asunto, texto, html, imagenes):
    nombre, _ = parseaddr(c.get("remitente", ""))
    msg = {"subject": asunto,
           "body": {"contentType": "HTML" if html else "Text", "content": html or texto},
           "toRecipients": [{"emailAddress": {"address": a}} for _, a in getaddresses([para]) if a],
           "from": {"emailAddress": {"address": c["buzon"], **({"name": nombre} if nombre else {})}}}
    if html and imagenes:
        msg["attachments"] = [{"@odata.type": "#microsoft.graph.fileAttachment", "name": f"{cid}.{sub}",
                               "contentType": f"image/{sub}", "contentId": cid, "isInline": True,
                               "contentBytes": base64.b64encode(datos).decode()}
                              for cid, (datos, sub) in imagenes.items()]
    cuerpo = json.dumps({"message": msg, "saveToSentItems": True}).encode()
    _post(f"https://graph.microsoft.com/v1.0/users/{urllib.parse.quote(c['buzon'])}/sendMail", cuerpo,
          {"Authorization": f"Bearer {_token_graph(c)}", "Content-Type": "application/json"})


def enviar(para, asunto, texto, html=None, imagenes=None):
    """True si se envió; False si el correo no está configurado. Lanza excepción si el servidor falla."""
    if os.environ.get("EVALUADOR_SIN_CORREO"):
        # modo de pruebas: nada sale; el mensaje queda como .eml para revisarlo
        import re
        import tempfile
        carpeta = os.path.join(tempfile.gettempdir(), "evaluador_correos_prueba")
        os.makedirs(carpeta, exist_ok=True)
        nombre = f"{time.strftime('%Y%m%d_%H%M%S')}_{re.sub(r'[^A-Za-z0-9]+', '_', asunto)[:40]}.eml"
        with open(os.path.join(carpeta, nombre), "wb") as fh:
            fh.write(bytes(armar(para, asunto, texto, html, imagenes, "Evaluador GD <pruebas@evaluador.test>")))
        return False
    c = config()
    if not c:
        return False
    try:
        _enviar(c, para, asunto, texto, html, imagenes)
    except Exception as e:
        _registrar_resultado(c, False, f"{type(e).__name__}: {e}")
        raise
    _registrar_resultado(c, True)
    return True


def _enviar_outlook(c, para, asunto, texto, html, imagenes):
    import shutil
    import subprocess
    import tempfile
    _, de = parseaddr(c.get("remitente", ""))
    carpeta = tempfile.mkdtemp(prefix="evaluador_outlook_")
    try:
        adjuntos = []
        for cid, (datos, sub) in (imagenes or {}).items() if html else []:
            ruta = os.path.join(carpeta, f"{cid}.{sub}")
            with open(ruta, "wb") as fh:
                fh.write(datos)
            adjuntos.append(dict(ruta=ruta, nombre=f"{cid}.{sub}", cid=cid))
        pedido = os.path.join(carpeta, "correo.json")
        with open(pedido, "w", encoding="utf-8") as fh:  # ASCII: PowerShell 5.1 lo lee sin problemas de codificación
            json.dump(dict(perfil=c.get("perfil") or "Outlook", cuenta=c["usuario"], de=de or c["usuario"],
                           para="; ".join(a for _, a in getaddresses([para]) if a), asunto=asunto, texto=texto,
                           html=html or "", adjuntos=adjuntos), fh)
        script = os.path.join(BASE, "..", "herramientas", "enviar_outlook.ps1")
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                            "-File", script, pedido], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout).strip()[:300] or "Outlook no pudo enviar el correo")
    finally:
        shutil.rmtree(carpeta, ignore_errors=True)


def _enviar(c, para, asunto, texto, html, imagenes):
    if _metodo(c) == "outlook":
        _enviar_outlook(c, para, asunto, texto, html, imagenes)
        return
    if _metodo(c) == "graph":
        _enviar_graph(c, para, asunto, texto, html, imagenes)
        return
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
