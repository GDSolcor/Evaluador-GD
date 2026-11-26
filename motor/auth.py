"""
Usuarios, sesiones, recuperación de contraseña e historial de ingresos del Evaluador GD.

- Contraseñas: PBKDF2-HMAC-SHA256, 240.000 iteraciones, sal aleatoria por usuario (nunca en claro).
- Sesiones: token aleatorio en cookie HttpOnly/SameSite=Lax; en la base solo se guarda su hash.
- Recuperación / invitación: enlace de un solo uso, vence en 60 minutos, se envía por correo
  (motor/correo.py). Si el correo no está configurado, el enlace queda visible para el administrador.
- Bloqueo: 5 intentos fallidos en 15 minutos por correo bloquean el ingreso 15 minutos.
- Historial: cada ingreso, fallo, cierre, recuperación y cambio de usuario queda en la tabla accesos.

Base: datos/usuarios.db (SQLite). El primer usuario que se crea es administrador.
"""
import hashlib
import hmac
import os
import re
import secrets
import sqlite3

import bd
import threading
from datetime import datetime, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("EVALUADOR_USUARIOS", os.path.join(BASE, "..", "datos", "usuarios.db"))
ITER = 240_000
SESION_HORAS = 12
TOKEN_MIN = 60
MAX_FALLOS, VENTANA_MIN = 5, 15
MIN_LARGO = 10
_lock = threading.Lock()
_RE_CORREO = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _ahora():
    return datetime.now().replace(microsecond=0)


def _db():
    c = bd.conectar("ev_usuarios", DB_PATH, timeout=10)  # Postgres si EVALUADOR_DB_URL (motor/bd.py)
    c.row_factory = sqlite3.Row
    c.executescript("""
    create table if not exists usuarios(
        id integer primary key, correo text unique not null, nombre text not null,
        rol text not null default 'usuario', hash text, sal text, activo integer not null default 1,
        creado text not null, ultimo_ingreso text);
    create table if not exists sesiones(
        token_hash text primary key, usuario_id integer not null, creada text not null,
        expira text not null, ip text, agente text);
    create table if not exists tokens(
        token_hash text primary key, usuario_id integer not null, tipo text not null,
        expira text not null, usado integer not null default 0, enlace text);
    create table if not exists accesos(
        id integer primary key, fecha text not null, correo text, evento text not null,
        ip text, agente text, detalle text);
    create index if not exists ix_accesos_fecha on accesos(fecha);
    """)
    cols = [r[1] for r in c.execute("pragma table_info(usuarios)")]
    if "correo_proyectos" not in cols:
        # recibe el correo de cada proyecto nuevo en E1 aunque no sea administrador ni responsable (ticket #43)
        c.execute("alter table usuarios add column correo_proyectos integer not null default 0")
    if "permisos" not in cols:
        # permisos adicionales que da el administrador (#51), separados por coma: ver PERMISOS
        c.execute("alter table usuarios add column permisos text not null default ''")
    if "alcance" not in cols:
        # «todos»: ve todos los leads, proyectos y evaluaciones; «propios»: solo los suyos (#51)
        c.execute("alter table usuarios add column alcance text not null default 'todos'")
    if "ultima_actividad" not in [r[1] for r in c.execute("pragma table_info(sesiones)")]:
        c.execute("alter table sesiones add column ultima_actividad text")  # para saber quién está en línea (#51)
    return c


# permisos que el administrador puede dar a un usuario (#51); el administrador los tiene todos
PERMISOS = {"fuentes_ver": "Ver las bases de datos y geoportales de la herramienta",
            "fuentes_actualizar": "Lanzar la actualización de las bases de datos"}
ALCANCES = {"todos": "Todos los leads, proyectos y evaluaciones", "propios": "Solo sus leads, proyectos y evaluaciones"}
EN_LINEA_MIN = 5  # minutos sin actividad para dejar de contar como «en línea»


def puede(u, permiso):
    return bool(u) and (u.get("rol") == "admin" or permiso in (u.get("permisos") or []))


def solo_propios(u):
    """True si el usuario solo debe ver sus leads, proyectos y evaluaciones."""
    return bool(u) and u.get("rol") != "admin" and u.get("alcance") == "propios"


def _h(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _hash_clave(clave, sal):
    return hashlib.pbkdf2_hmac("sha256", clave.encode("utf-8"), bytes.fromhex(sal), ITER).hex()


def validar_clave(clave):
    if len(clave or "") < MIN_LARGO:
        return f"La contraseña debe tener al menos {MIN_LARGO} caracteres."
    if not (re.search(r"[A-Za-zÁÉÍÓÚáéíóúÑñ]", clave) and re.search(r"\d", clave)):
        return "La contraseña debe combinar letras y números."
    return None


def registrar(correo, evento, ip="", agente="", detalle=""):
    with _lock, _db() as c:
        c.execute("insert into accesos(fecha, correo, evento, ip, agente, detalle) values (?,?,?,?,?,?)",
                  (_ahora().isoformat(sep=" "), (correo or "").lower(), evento, ip, (agente or "")[:300], detalle))


def hay_usuarios():
    with _db() as c:
        return c.execute("select count(*) from usuarios").fetchone()[0] > 0


def _publico(u):
    return dict(id=u["id"], correo=u["correo"], nombre=u["nombre"], rol=u["rol"], activo=bool(u["activo"]),
                creado=u["creado"], ultimo_ingreso=u["ultimo_ingreso"], con_clave=bool(u["hash"]),
                correo_proyectos=bool(u["correo_proyectos"]),
                permisos=[p for p in (u["permisos"] or "").split(",") if p in PERMISOS],
                alcance=u["alcance"] if u["alcance"] in ALCANCES else "todos")


# ---------------------------------------------------------------- configuración inicial
def crear_primer_admin(correo, nombre, clave, ip="", agente=""):
    correo = (correo or "").strip().lower()
    if hay_usuarios():
        raise PermissionError("La herramienta ya tiene usuarios; pide acceso al administrador.")
    if not _RE_CORREO.match(correo):
        raise ValueError("Correo no válido.")
    err = validar_clave(clave)
    if err:
        raise ValueError(err)
    sal = secrets.token_hex(16)
    with _lock, _db() as c:
        c.execute("insert into usuarios(correo, nombre, rol, hash, sal, creado) values (?,?,?,?,?,?)",
                  (correo, nombre.strip() or correo, "admin", _hash_clave(clave, sal), sal, _ahora().isoformat(sep=" ")))
    registrar(correo, "admin_creado", ip, agente, "configuración inicial")


# ---------------------------------------------------------------- ingreso / sesiones
def _bloqueado(c, correo):
    desde = (_ahora() - timedelta(minutes=VENTANA_MIN)).isoformat(sep=" ")
    # solo cuentan los fallos posteriores al último ingreso correcto (por id: la fecha tiene resolución de segundos)
    ult_ok = c.execute("select max(id) from accesos where correo=? and evento in ('ingreso','clave_restablecida')",
                       (correo,)).fetchone()[0] or 0
    n = c.execute("select count(*) from accesos where correo=? and evento='ingreso_fallido' and fecha>=? and id>?",
                  (correo, desde, ult_ok)).fetchone()[0]
    return n >= MAX_FALLOS


def ingresar(correo, clave, ip="", agente=""):
    correo = (correo or "").strip().lower()
    with _db() as c:
        if _bloqueado(c, correo):
            registrar(correo, "ingreso_bloqueado", ip, agente)
            raise PermissionError(f"Demasiados intentos fallidos. Intenta de nuevo en {VENTANA_MIN} minutos "
                                  "o recupera tu contraseña.")
        u = c.execute("select * from usuarios where correo=?", (correo,)).fetchone()
    ok = bool(u and u["activo"] and u["hash"] and hmac.compare_digest(_hash_clave(clave or "", u["sal"]), u["hash"]))
    if not ok:
        registrar(correo, "ingreso_fallido", ip, agente,
                  "usuario inactivo" if u and not u["activo"] else "sin contraseña definida" if u and not u["hash"] else "")
        raise PermissionError("Correo o contraseña incorrectos.")
    token = secrets.token_urlsafe(32)
    ahora = _ahora()
    with _lock, _db() as c:
        c.execute("insert into sesiones(token_hash, usuario_id, creada, expira, ip, agente, ultima_actividad) values (?,?,?,?,?,?,?)",
                  (_h(token), u["id"], ahora.isoformat(sep=" "), (ahora + timedelta(hours=SESION_HORAS)).isoformat(sep=" "),
                   ip, (agente or "")[:300], ahora.isoformat(sep=" ")))
        c.execute("update usuarios set ultimo_ingreso=? where id=?", (ahora.isoformat(sep=" "), u["id"]))
        c.execute("delete from sesiones where expira < ?", (ahora.isoformat(sep=" "),))
    registrar(correo, "ingreso", ip, agente)
    return token, SESION_HORAS * 3600


# ---------------------------------------------------------------- modo portal (sitio común con la Ruta de desarrollo GD)
# El sitio común (Worker de la Ruta) autentica a la persona y reenvía /evaluador/* con su identidad firmada:
# X-GD-Usuario (correo), X-GD-Nombre (codificado como URI), X-GD-Rol (usuario|admin), X-GD-Fecha (segundos Unix)
# y X-GD-Firma = HMAC-SHA256 hex de «v1⏎fecha⏎correo⏎nombre⏎rol» (una línea por campo) con el secreto compartido GD_PORTAL_SECRETO.
PORTAL_VENTANA_S = 120  # tolerancia de la fecha de la firma (relojes distintos y reintentos)


def firma_portal(secreto, fecha, correo, nombre, rol):
    texto = "\n".join(["v1", fecha, correo, nombre, rol])
    return hmac.new(secreto.encode("utf-8"), texto.encode("utf-8"), hashlib.sha256).hexdigest()


def verificar_portal(secreto, cab, ahora_s=None):
    """Identidad del portal si la firma es válida y reciente: dict(correo, nombre, rol); si no, None.
    `cab` es un dict con las cabeceras X-GD-* en minúsculas."""
    from urllib.parse import unquote
    correo, nombre, rol = cab.get("x-gd-usuario", ""), cab.get("x-gd-nombre", ""), cab.get("x-gd-rol", "")
    fecha, firma = cab.get("x-gd-fecha", ""), cab.get("x-gd-firma", "")
    if not (secreto and correo and fecha and firma and rol in ("usuario", "admin") and fecha.isdigit()):
        return None
    ahora_s = int(datetime.now().timestamp()) if ahora_s is None else ahora_s
    if abs(ahora_s - int(fecha)) > PORTAL_VENTANA_S:
        return None
    if not hmac.compare_digest(firma_portal(secreto, fecha, correo, nombre, rol), firma.lower()):
        return None
    correo = correo.strip().lower()
    if not _RE_CORREO.match(correo):
        return None
    return dict(correo=correo, nombre=unquote(nombre).strip() or correo, rol=rol)


def usuario_portal(ident, ip="", agente=""):
    """Usuario del Evaluador para una identidad del portal: lo crea si no existe (conserva autoría e historial,
    cruzando por correo) y toma el rol que asignó el portal. Un usuario desactivado aquí sigue sin acceso."""
    correo, ahora = ident["correo"], _ahora()
    nuevo = False
    with _lock, _db() as c:
        u = c.execute("select * from usuarios where correo=?", (correo,)).fetchone()
        if not u:
            c.execute("insert into usuarios(correo, nombre, rol, creado, ultimo_ingreso) values (?,?,?,?,?)",
                      (correo, ident["nombre"], ident["rol"], ahora.isoformat(sep=" "), ahora.isoformat(sep=" ")))
            nuevo = True
        else:
            if not u["activo"]:
                return None
            cambios = {}
            for k in ("rol", "nombre"):  # el sitio común es quien mantiene el nombre y el rol
                if u[k] != ident[k]:
                    cambios[k] = ident[k]
            # «último ingreso»: como mucho una escritura cada 30 minutos
            if not u["ultimo_ingreso"] or u["ultimo_ingreso"] < (ahora - timedelta(minutes=30)).isoformat(sep=" "):
                cambios["ultimo_ingreso"] = ahora.isoformat(sep=" ")
            if cambios:
                c.execute(f"update usuarios set {', '.join(k + '=?' for k in cambios)} where id=?", (*cambios.values(), u["id"]))
        u = c.execute("select * from usuarios where correo=?", (correo,)).fetchone()
    if nuevo:
        registrar(correo, "usuario_creado", ip, agente, f"por el portal · rol {ident['rol']}")
    return dict(_publico(u), portal=True)


def usuario_de_sesion(token):
    if not token:
        return None
    with _db() as c:
        r = c.execute("select u.*, s.expira, s.creada as sesion_desde, s.ultima_actividad from sesiones s "
                      "join usuarios u on u.id=s.usuario_id where s.token_hash=?", (_h(token),)).fetchone()
    ahora = _ahora()
    if not r or r["expira"] < ahora.isoformat(sep=" ") or not r["activo"]:
        return None
    # actividad de la sesión (como mucho una escritura por minuto)
    if not r["ultima_actividad"] or r["ultima_actividad"] < (ahora - timedelta(seconds=60)).isoformat(sep=" "):
        try:
            with _lock, _db() as c:
                c.execute("update sesiones set ultima_actividad=? where token_hash=?", (ahora.isoformat(sep=" "), _h(token)))
        except sqlite3.Error:
            pass
    return dict(_publico(r), sesion_desde=r["sesion_desde"], sesion_expira=r["expira"])


def en_linea(minutos=EN_LINEA_MIN):
    """Usuarios con actividad en los últimos minutos: [{id, correo, nombre, ultima_actividad, sesiones}]."""
    desde = (_ahora() - timedelta(minutes=minutos)).isoformat(sep=" ")
    with _db() as c:
        return [dict(r) for r in c.execute(
            "select u.id, u.correo, u.nombre, max(s.ultima_actividad) ultima_actividad, count(*) sesiones "
            "from sesiones s join usuarios u on u.id=s.usuario_id where s.ultima_actividad>=? and s.expira>=? and u.activo=1 "
            "group by u.id order by ultima_actividad desc", (desde, _ahora().isoformat(sep=" ")))]


def cerrar_sesion(token, ip="", agente=""):
    u = usuario_de_sesion(token)
    with _lock, _db() as c:
        c.execute("delete from sesiones where token_hash=?", (_h(token or ""),))
    if u:
        registrar(u["correo"], "salida", ip, agente)


# ---------------------------------------------------------------- recuperación e invitación
def crear_token(correo, tipo, base_url):
    """Devuelve (enlace, usuario) o (None, None) si el correo no existe (no se revela)."""
    correo = (correo or "").strip().lower()
    with _db() as c:
        u = c.execute("select * from usuarios where correo=? and activo=1", (correo,)).fetchone()
    if not u:
        return None, None
    token = secrets.token_urlsafe(32)
    enlace = f"{base_url}/login?restablecer={token}"
    with _lock, _db() as c:
        c.execute("update tokens set usado=1 where usuario_id=? and usado=0", (u["id"],))
        c.execute("insert into tokens values (?,?,?,?,0,?)", (_h(token), u["id"], tipo,
                  (_ahora() + timedelta(minutes=TOKEN_MIN if tipo == "recuperacion" else 7 * 24 * 60)).isoformat(sep=" "), enlace))
    return enlace, _publico(u)


def recuperaciones_recientes(correo, minutos=60):
    desde = (_ahora() - timedelta(minutes=minutos)).isoformat(sep=" ")
    with _db() as c:
        return c.execute("select count(*) from accesos where correo=? and evento='recuperacion_solicitada' and fecha>=?",
                         ((correo or "").strip().lower(), desde)).fetchone()[0]


def restablecer(token, clave, ip="", agente=""):
    err = validar_clave(clave)
    if err:
        raise ValueError(err)
    with _db() as c:
        t = c.execute("select t.*, u.correo from tokens t join usuarios u on u.id=t.usuario_id where token_hash=?",
                      (_h(token or ""),)).fetchone()
    if not t or t["usado"] or t["expira"] < _ahora().isoformat(sep=" "):
        raise PermissionError("El enlace no es válido o ya venció. Solicita uno nuevo.")
    sal = secrets.token_hex(16)
    with _lock, _db() as c:
        c.execute("update usuarios set hash=?, sal=? where id=?", (_hash_clave(clave, sal), sal, t["usuario_id"]))
        c.execute("update tokens set usado=1 where token_hash=?", (_h(token),))
        c.execute("delete from sesiones where usuario_id=?", (t["usuario_id"],))  # cierra sesiones anteriores
    registrar(t["correo"], "clave_definida" if t["tipo"] == "invitacion" else "clave_restablecida", ip, agente)
    return t["correo"]


def info_token(token):
    with _db() as c:
        t = c.execute("select t.tipo, t.expira, t.usado, u.correo, u.nombre from tokens t join usuarios u on u.id=t.usuario_id "
                      "where token_hash=?", (_h(token or ""),)).fetchone()
    if not t or t["usado"] or t["expira"] < _ahora().isoformat(sep=" "):
        return None
    return dict(tipo=t["tipo"], correo=t["correo"], nombre=t["nombre"])


# ---------------------------------------------------------------- administración
def listar_usuarios():
    with _db() as c:
        us = [_publico(u) for u in c.execute("select * from usuarios order by rol, nombre")]
        pend = {r["usuario_id"]: dict(enlace=r["enlace"], tipo=r["tipo"], expira=r["expira"]) for r in c.execute(
            "select * from tokens where usado=0 and expira>=? order by expira", (_ahora().isoformat(sep=" "),))}
    for u in us:
        u["enlace_pendiente"] = pend.get(u["id"])
    return us


def crear_usuario(correo, nombre, rol, por, ip="", agente=""):
    correo = (correo or "").strip().lower()
    if not _RE_CORREO.match(correo):
        raise ValueError("Correo no válido.")
    if rol not in ("usuario", "admin"):
        raise ValueError("Rol no válido.")
    with _lock, _db() as c:
        try:
            c.execute("insert into usuarios(correo, nombre, rol, creado) values (?,?,?,?)",
                      (correo, (nombre or "").strip() or correo, rol, _ahora().isoformat(sep=" ")))
        except sqlite3.IntegrityError:
            raise ValueError("Ya existe un usuario con ese correo.")
    registrar(correo, "usuario_creado", ip, agente, f"por {por} · rol {rol}")


def actualizar_usuario(uid, cambios, por, ip="", agente=""):
    campos = {k: v for k, v in cambios.items() if k in ("rol", "activo", "nombre", "correo_proyectos", "permisos", "alcance")}
    if "permisos" in campos:
        lista = campos["permisos"] if isinstance(campos["permisos"], (list, tuple)) else str(campos["permisos"]).split(",")
        lista = [p.strip() for p in lista if p.strip()]
        if any(p not in PERMISOS for p in lista):
            raise ValueError("Permiso no válido.")
        if "fuentes_actualizar" in lista and "fuentes_ver" not in lista:
            lista.append("fuentes_ver")  # para actualizar hay que ver la lista
        campos["permisos"] = ",".join(sorted(set(lista)))
    if "alcance" in campos and campos["alcance"] not in ALCANCES:
        raise ValueError("Alcance no válido.")
    if not campos:
        return
    with _lock, _db() as c:
        u = c.execute("select * from usuarios where id=?", (uid,)).fetchone()
        if not u:
            raise ValueError("Usuario no existe.")
        if u["rol"] == "admin" and (campos.get("rol") == "usuario" or campos.get("activo") in (0, False)):
            n = c.execute("select count(*) from usuarios where rol='admin' and activo=1").fetchone()[0]
            if n <= 1:
                raise ValueError("Debe quedar al menos un administrador activo.")
        for k, v in campos.items():
            c.execute(f"update usuarios set {k}=? where id=?", (int(v) if k in ("activo", "correo_proyectos") else v, uid))
        if campos.get("activo") in (0, False):
            c.execute("delete from sesiones where usuario_id=?", (uid,))
    registrar(u["correo"], "usuario_modificado", ip, agente,
              f"por {por}: " + ", ".join(f"{k}={v}" for k, v in campos.items()))


def accesos(correo=None, desde=None, hasta=None, evento=None, limite=500):
    q, p = "select * from accesos where 1=1", []
    if correo:
        q += " and correo like ?"
        p.append(f"%{correo.lower()}%")
    if desde:
        q += " and fecha >= ?"
        p.append(desde)
    if hasta:
        q += " and fecha <= ?"
        p.append(hasta + " 23:59:59")
    if evento:
        q += " and evento = ?"
        p.append(evento)
    q += " order by fecha desc, id desc limit ?"
    p.append(limite)
    with _db() as c:
        filas = [dict(r) for r in c.execute(q, p)]
        resumen = [dict(r) for r in c.execute(
            "select correo, count(*) filter (where evento='ingreso') ingresos, "
            "count(*) filter (where evento='ingreso_fallido') fallidos, max(case when evento='ingreso' then fecha end) ultimo "
            "from accesos group by correo order by ultimo desc")]
    return dict(accesos=filas, resumen=resumen)
