"""
Evaluaciones guardadas: el estado completo de un análisis para retomarlo después.

Se guarda lo necesario para reproducir el análisis (no los resultados, que se recalculan al abrir):
capas del predio con sus roles, parámetros del layout, supuestos financieros, nombre y origen.
También un resumen (MWp, GD, mesas, ha, TIR, municipio) para listar sin recalcular.

Base: datos/evaluaciones.db (SQLite). Todo el equipo ve y abre todas las evaluaciones; solo su autor
o un administrador puede sobrescribirlas o borrarlas (los demás usan «Guardar como nueva»).
Una evaluación puede quedar ligada a un lead (motor/leads.py).
"""
import json
import os
import sqlite3

import bd
import threading
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("EVALUADOR_EVALUACIONES", os.path.join(BASE, "..", "datos", "evaluaciones.db"))
_lock = threading.Lock()
MAX_BYTES = 20 * 1024 * 1024


def _db():
    c = bd.conectar("ev_evaluaciones", DB_PATH, timeout=10)  # Postgres si EVALUADOR_DB_URL (motor/bd.py)
    c.row_factory = sqlite3.Row
    c.executescript("""
    create table if not exists evaluaciones(
        id integer primary key, usuario_id integer not null, nombre text not null,
        creada text not null, modificada text not null, datos text not null, resumen text not null);
    create index if not exists ix_eval_usuario on evaluaciones(usuario_id, modificada);
    """)
    cols = {r[1] for r in c.execute("pragma table_info(evaluaciones)")}
    if "autor" not in cols:  # bases anteriores a la v2.2
        c.execute("alter table evaluaciones add column autor text not null default ''")
    if "lead_id" not in cols:
        c.execute("alter table evaluaciones add column lead_id integer")
    return c


def _ahora():
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def _usr(u):
    """Acepta el usuario de la sesión (dict) o solo su id (pruebas y código anterior)."""
    return u if isinstance(u, dict) else dict(id=u, nombre="", rol="usuario")


def _puede_editar(u, fila):
    return fila["usuario_id"] == u["id"] or u.get("rol") == "admin"


def listar(u, lead_id=None):
    u = _usr(u)
    with _db() as c:
        q, p = "select id, usuario_id, autor, lead_id, nombre, creada, modificada, resumen from evaluaciones", []
        if lead_id:
            q, p = q + " where lead_id=?", [lead_id]
        return [dict(id=r["id"], nombre=r["nombre"], creada=r["creada"], modificada=r["modificada"], autor=r["autor"],
                     lead_id=r["lead_id"], mia=r["usuario_id"] == u["id"], editable=_puede_editar(u, r),
                     resumen=json.loads(r["resumen"]))
                for r in c.execute(q + " order by modificada desc", p)]


def conteo_por_usuario():
    """{usuario_id: (evaluaciones, última modificación)} para las métricas del administrador (#51)."""
    with _db() as c:
        return {r[0]: (r[1], r[2]) for r in c.execute("select usuario_id, count(*), max(modificada) from evaluaciones group by usuario_id")}


def puntos(usuario_id=None):
    """[(id, nombre, autor, lead_id, lon, lat, resumen)] de las evaluaciones (todas, o solo las de usuario_id), con el
    centro de sus áreas construibles (o del mapa guardado). Para la capa «Pipeline Solcor»."""
    out = []
    with _db() as c:
        q, p = "select id, nombre, autor, lead_id, datos, resumen from evaluaciones", ()
        if usuario_id is not None:
            q, p = q + " where usuario_id=?", (usuario_id,)
        for r in c.execute(q, p):
            try:
                d = json.loads(r["datos"])
            except ValueError:
                continue
            pts = [p for cap in d.get("capas") or [] if cap.get("rol") == "construible" and cap.get("tipo") == "Polygon"
                   for p in cap.get("coords") or []]
            pts += [p for pol in d.get("dibujo") or [] if pol.get("rol") == "construible" for p in pol.get("coords") or []]
            if pts:
                lon, lat = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
            elif (d.get("mapa") or {}).get("centro"):
                lat, lon = d["mapa"]["centro"][:2]
            else:
                continue
            out.append((r["id"], r["nombre"], r["autor"], r["lead_id"], lon, lat, json.loads(r["resumen"] or "{}")))
    return out


def obtener(u, eid):
    u = _usr(u)
    with _db() as c:
        r = c.execute("select * from evaluaciones where id=?", (eid,)).fetchone()
    if not r:
        raise ValueError("La evaluación no existe.")
    return dict(id=r["id"], nombre=r["nombre"], creada=r["creada"], modificada=r["modificada"], autor=r["autor"],
                lead_id=r["lead_id"], mia=r["usuario_id"] == u["id"], editable=_puede_editar(u, r),
                datos=json.loads(r["datos"]), resumen=json.loads(r["resumen"]))


def guardar(u, nombre, datos, resumen, eid=None, lead_id=None):
    u = _usr(u)
    nombre = (nombre or "").strip()[:120] or "Evaluación sin nombre"
    txt = json.dumps(datos, ensure_ascii=False)
    if len(txt.encode("utf-8")) > MAX_BYTES:
        raise ValueError("La evaluación es demasiado grande para guardarse (más de 20 MB).")
    ahora = _ahora()
    with _lock, _db() as c:
        if eid:
            r = c.execute("select usuario_id, lead_id from evaluaciones where id=?", (eid,)).fetchone()
            if not r:
                raise ValueError("La evaluación no existe.")
            if not _puede_editar(u, r):
                raise PermissionError("Esta evaluación es de otra persona: usa «Guardar como nueva» para tener tu propia copia.")
            c.execute("update evaluaciones set nombre=?, modificada=?, datos=?, resumen=?, lead_id=? where id=?",
                      (nombre, ahora, txt, json.dumps(resumen, ensure_ascii=False), lead_id or r["lead_id"], eid))
            return eid
        cur = c.execute("insert into evaluaciones(usuario_id, autor, lead_id, nombre, creada, modificada, datos, resumen) "
                        "values (?,?,?,?,?,?,?,?)",
                        (u["id"], u.get("nombre") or u.get("correo") or "", lead_id, nombre, ahora, ahora, txt,
                         json.dumps(resumen, ensure_ascii=False)))
        return cur.lastrowid


def borrar(u, eid):
    u = _usr(u)
    with _lock, _db() as c:
        r = c.execute("select usuario_id from evaluaciones where id=?", (eid,)).fetchone()
        if not r:
            raise ValueError("La evaluación no existe.")
        if not _puede_editar(u, r):
            raise PermissionError("Solo quien creó la evaluación o un administrador puede borrarla.")
        c.execute("delete from evaluaciones where id=?", (eid,))
