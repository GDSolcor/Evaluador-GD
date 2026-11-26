"""
Soporte técnico: tickets que los usuarios dejan desde el evaluador (errores, ajustes, mejoras, dudas).

Cada ticket guarda el texto, el contexto de uso que la interfaz adjunta sola (versión, evaluación
abierta, último error del servidor, navegador) y hasta MAX_ADJUNTOS imágenes de evidencia.
Los tickets se atienden en un barrido diario (herramientas/barrido_soporte.py): se revisan los
abiertos, se ajusta la herramienta si hace falta y se deja la respuesta, que el usuario ve en
"Mis tickets".

Base: datos/soporte.db (SQLite); imágenes en datos/soporte_adjuntos/. Cada usuario ve solo sus
tickets; el administrador los ve todos y es quien responde.
"""
import base64
import json
import os
import sqlite3

import bd
import threading
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("EVALUADOR_SOPORTE", os.path.join(BASE, "..", "datos", "soporte.db"))
_lock = threading.Lock()

TIPOS = {"error": "Error", "ajuste": "Ajuste", "mejora": "Mejora", "duda": "Duda"}
ESTADOS = {"nuevo": "Nuevo", "en_revision": "En revisión", "resuelto": "Resuelto",
           "planeado": "Planeado", "descartado": "Descartado"}
ABIERTOS = ("nuevo", "en_revision", "planeado")
MAX_ADJUNTOS = 5
MAX_IMAGEN = 8 * 1024 * 1024
# la firma del archivo decide el tipo: no se confía en el nombre ni en el tipo que diga el navegador
FIRMAS = ((b"\x89PNG\r\n\x1a\n", "image/png", ".png"), (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
          (b"GIF8", "image/gif", ".gif"), (b"RIFF", "image/webp", ".webp"))


def _carpeta():
    c = os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "soporte_adjuntos")
    os.makedirs(c, exist_ok=True)
    return c


def _db():
    c = bd.conectar("ev_soporte", DB_PATH, timeout=10)  # Postgres si EVALUADOR_DB_URL (motor/bd.py)
    c.row_factory = sqlite3.Row
    c.executescript("""
    create table if not exists tickets(
        id integer primary key, usuario_id integer not null, correo text not null, nombre text not null,
        tipo text not null, titulo text not null, descripcion text not null, contexto text not null,
        estado text not null default 'nuevo', respuesta text not null default '',
        creado text not null, modificado text not null, respondido text, visto integer not null default 1);
    create index if not exists ix_ticket_usuario on tickets(usuario_id, creado);
    create index if not exists ix_ticket_estado on tickets(estado, creado);
    create table if not exists adjuntos(
        id integer primary key, ticket_id integer not null, archivo text not null, mime text not null, nombre text not null);
    create index if not exists ix_adjunto_ticket on adjuntos(ticket_id);
    create table if not exists mensajes(
        id integer primary key, ticket_id integer not null, usuario_id integer, nombre text not null,
        rol text not null, texto text not null, creado text not null);
    create index if not exists ix_mensaje_ticket on mensajes(ticket_id, id);
    """)
    if "visto_admin" not in {r[1] for r in c.execute("pragma table_info(tickets)")}:
        # primera vez con conversación: la respuesta única que tenía cada ticket pasa a ser su primer mensaje
        c.execute("alter table tickets add column visto_admin integer not null default 1")
        c.execute("insert into mensajes(ticket_id, usuario_id, nombre, rol, texto, creado) "
                  "select id, null, 'Soporte', 'soporte', respuesta, coalesce(respondido, modificado) from tickets where respuesta<>''")
        c.commit()
    return c


def _ahora():
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


# Otros archivos de evidencia (no se muestran en la página: siempre se entregan como descarga).
ARCHIVOS = {".pdf", ".kmz", ".kml", ".docx", ".xlsx", ".xls", ".doc", ".csv", ".txt", ".json", ".geojson", ".zip", ".dwg", ".dxf", ".shp"}
MAX_ARCHIVO = 15 * 1024 * 1024


def _tipo_imagen(datos, nombre=""):
    for firma, mime, ext in FIRMAS:
        if datos.startswith(firma) and (mime != "image/webp" or datos[8:12] == b"WEBP"):
            return mime, ext
    ext = os.path.splitext(nombre or "")[1].lower()
    if ext in ARCHIVOS:
        return "application/octet-stream", ext
    raise ValueError("Tipo de archivo no admitido. Se aceptan imágenes y archivos " + ", ".join(sorted(e[1:].upper() for e in ARCHIVOS)) + ".")


def _fila(c, r):
    d = dict(r)
    d["contexto"] = json.loads(d["contexto"] or "{}")
    d["tipo_txt"], d["estado_txt"] = TIPOS.get(d["tipo"], d["tipo"]), ESTADOS.get(d["estado"], d["estado"])
    d["adjuntos"] = [dict(id=a["id"], nombre=a["nombre"], imagen=a["mime"].startswith("image/")) for a in
                     c.execute("select id, nombre, mime from adjuntos where ticket_id=? order by id", (r["id"],))]
    d["mensajes"] = [dict(m) for m in c.execute("select id, nombre, rol, texto, creado from mensajes where ticket_id=? order by id", (r["id"],))]
    d["abierto"] = d["estado"] in ABIERTOS
    return d


def crear(usuario, tipo, titulo, descripcion, contexto=None, imagenes=None):
    titulo, descripcion = (titulo or "").strip()[:140], (descripcion or "").strip()[:6000]
    if tipo not in TIPOS:
        raise ValueError("Elige el tipo de novedad.")
    if len(titulo) < 4 or len(descripcion) < 10:
        raise ValueError("Escribe un título y describe la novedad con algo más de detalle.")
    imagenes = imagenes or []
    if len(imagenes) > MAX_ADJUNTOS:
        raise ValueError(f"Máximo {MAX_ADJUNTOS} archivos por ticket.")
    archivos = []
    for im in imagenes:
        datos = base64.b64decode((im.get("b64") or "").split(",")[-1])
        nombre = os.path.basename((im.get("nombre") or "captura").replace("\\", "/"))[:80]
        mime, ext = _tipo_imagen(datos, nombre)
        if len(datos) > (MAX_IMAGEN if mime.startswith("image/") else MAX_ARCHIVO):
            raise ValueError("Cada imagen puede pesar máximo 8 MB y cada archivo 15 MB.")
        archivos.append((datos, mime, ext, nombre))
    ahora = _ahora()
    with _lock, _db() as c:
        tid = c.execute(
            "insert into tickets(usuario_id, correo, nombre, tipo, titulo, descripcion, contexto, creado, modificado) "
            "values (?,?,?,?,?,?,?,?,?)",
            (usuario["id"], usuario.get("correo", ""), usuario.get("nombre", ""), tipo, titulo, descripcion,
             json.dumps(contexto or {}, ensure_ascii=False)[:20000], ahora, ahora)).lastrowid
        for k, (datos, mime, ext, nombre) in enumerate(archivos, 1):
            archivo = f"ticket{tid:04d}_{k}{ext}"
            if bd.NUBE:  # en la nube los adjuntos van a ev_archivos.archivos
                bd.guardar_archivo(f"soporte_adjuntos/{archivo}", datos)
            else:
                with open(os.path.join(_carpeta(), archivo), "wb") as f:
                    f.write(datos)
            c.execute("insert into adjuntos(ticket_id, archivo, mime, nombre) values (?,?,?,?)", (tid, archivo, mime, nombre))
    return tid


def listar(usuario, todos=False, estado=None):
    """Tickets del usuario; con todos=True (solo administrador) los de todos los usuarios."""
    todos = todos and usuario.get("rol") == "admin"
    cond, args = [], []
    if not todos:
        cond.append("usuario_id=?"); args.append(usuario["id"])
    if estado == "abiertos":
        cond.append("estado in (%s)" % ",".join("?" * len(ABIERTOS))); args += ABIERTOS
    elif estado in ESTADOS:
        cond.append("estado=?"); args.append(estado)
    sql = "select * from tickets" + (" where " + " and ".join(cond) if cond else "") + " order by creado desc limit 500"
    with _db() as c:
        return [_fila(c, r) for r in c.execute(sql, args)]


def resumen(usuario):
    """Contadores para el botón de soporte: respuestas sin leer y, para el administrador, tickets abiertos."""
    with _db() as c:
        sin_leer = c.execute("select count(*) from tickets where usuario_id=? and visto=0", (usuario["id"],)).fetchone()[0]
        adm = usuario.get("rol") == "admin"
        abiertos = c.execute("select count(*) from tickets where estado in ('nuevo','en_revision')").fetchone()[0] if adm else 0
        comentarios = c.execute("select count(*) from tickets where visto_admin=0").fetchone()[0] if adm else 0
    return dict(sin_leer=sin_leer, abiertos=abiertos, comentarios=comentarios)


def marcar_vistos(usuario, todos=False):
    """Marca como leídas las respuestas a los tickets del usuario; con todos=True (administrador), los comentarios de los usuarios."""
    with _lock, _db() as c:
        if todos and usuario.get("rol") == "admin":
            c.execute("update tickets set visto_admin=1 where visto_admin=0")
        else:
            c.execute("update tickets set visto=1 where usuario_id=? and visto=0", (usuario["id"],))


def comentar(usuario, tid, texto):
    """Mensaje dentro de la conversación de un ticket. Escribe su autor o el administrador; el autor solo mientras
    el ticket siga abierto (lo cierra el administrador al marcarlo resuelto o descartado)."""
    texto = (texto or "").strip()[:6000]
    if len(texto) < 2:
        raise ValueError("Escribe el comentario.")
    adm = usuario.get("rol") == "admin"
    ahora = _ahora()
    with _lock, _db() as c:
        t = c.execute("select usuario_id, estado from tickets where id=?", (tid,)).fetchone()
        if not t or (t["usuario_id"] != usuario["id"] and not adm):
            raise ValueError("El ticket no existe.")
        if not adm and t["estado"] not in ABIERTOS:
            raise ValueError("Este ticket ya está cerrado. Si el tema sigue pendiente, abre un ticket nuevo.")
        c.execute("insert into mensajes(ticket_id, usuario_id, nombre, rol, texto, creado) values (?,?,?,?,?,?)",
                  (tid, usuario["id"], usuario.get("nombre", ""), "admin" if adm else "usuario", texto, ahora))
        if adm:
            c.execute("update tickets set respuesta=?, respondido=?, modificado=?, visto=case when usuario_id=? then visto else 0 end where id=?",
                      (texto, ahora, ahora, usuario["id"], tid))
        else:
            c.execute("update tickets set modificado=?, visto_admin=0 where id=?", (ahora, tid))


def responder(tid, estado, respuesta, usuario=None):
    """Respuesta del barrido: agrega un mensaje a la conversación (si trae texto nuevo) y fija el estado.
    Con usuario se exige que sea administrador; sin él es la herramienta local."""
    if usuario is not None and usuario.get("rol") != "admin":
        raise PermissionError("Solo el administrador puede responder tickets.")
    if estado not in ESTADOS:
        raise ValueError("Estado no válido.")
    ahora = _ahora()
    with _lock, _db() as c:
        r = c.execute("select respuesta, estado from tickets where id=?", (tid,)).fetchone()
        if not r:
            raise ValueError("El ticket no existe.")
        nuevo = (respuesta or "").strip()[:6000]
        nuevo = "" if nuevo == r["respuesta"] else nuevo
        respuesta = nuevo or r["respuesta"]
        cambio = bool(nuevo) or estado != r["estado"]
        if nuevo:
            c.execute("insert into mensajes(ticket_id, usuario_id, nombre, rol, texto, creado) values (?,?,?,?,?,?)",
                      (tid, usuario["id"] if usuario else None, usuario.get("nombre", "") if usuario else "Soporte",
                       "admin" if usuario else "soporte", nuevo, ahora))
        c.execute("update tickets set estado=?, respuesta=?, modificado=?, respondido=coalesce(?, respondido), visto=case when ? then 0 else visto end, visto_admin=1 "
                  "where id=?", (estado, respuesta, ahora, ahora if nuevo else None, 1 if cambio else 0, tid))


def adjunto(usuario, aid):
    """(bytes, mime, nombre) de un adjunto, si el ticket es del usuario o es administrador."""
    with _db() as c:
        r = c.execute("select a.archivo, a.mime, a.nombre, t.usuario_id from adjuntos a join tickets t on t.id=a.ticket_id where a.id=?",
                      (aid,)).fetchone()
    if not r or (r["usuario_id"] != usuario["id"] and usuario.get("rol") != "admin"):
        raise ValueError("El adjunto no existe.")
    if bd.NUBE:
        datos = bd.leer_archivo(f"soporte_adjuntos/{os.path.basename(r['archivo'])}")
        if datos is None:
            raise ValueError("El adjunto no existe.")
        return datos, r["mime"], r["nombre"]
    with open(os.path.join(_carpeta(), os.path.basename(r["archivo"])), "rb") as f:
        return f.read(), r["mime"], r["nombre"]


def rutas_adjuntos(tid):
    if bd.NUBE:  # se copian a una carpeta temporal para adjuntarlos al correo
        import tempfile
        carpeta = os.path.join(tempfile.gettempdir(), "soporte_adjuntos")
        os.makedirs(carpeta, exist_ok=True)
        rutas = []
        with _db() as c:
            archivos = [r["archivo"] for r in c.execute("select archivo from adjuntos where ticket_id=? order by id", (tid,))]
        for a in archivos:
            datos = bd.leer_archivo(f"soporte_adjuntos/{os.path.basename(a)}")
            if datos is not None:
                ruta = os.path.join(carpeta, os.path.basename(a))
                with open(ruta, "wb") as f:
                    f.write(datos)
                rutas.append(ruta)
        return rutas
    with _db() as c:
        return [os.path.join(_carpeta(), r["archivo"]) for r in
                c.execute("select archivo from adjuntos where ticket_id=? order by id", (tid,))]
