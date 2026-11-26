"""
Leads de predios y su paso a proyecto.

Un lead es un predio ofrecido o identificado, antes de ser proyecto: quién lo ofrece, dónde queda y cómo llegó.
Se evalúa en el evaluador (las evaluaciones guardan su lead_id) y, cuando pasa a E1, se crea su carpeta en el
pipeline de SharePoint con la convención de Desarrollo GD:

    01 Pipeline/E1/GD 00NN Nombre/   con  0 Resumen … 9 Comercial
    archivos:  AAAAMMDD E1 descripción v01.ext

El ID (GD 00NN) es el siguiente al mayor que exista en las carpetas del pipeline y del archivo.
La raíz se toma de EVALUADOR_PIPELINE, de datos/config_pipeline.json {"raiz": "..."} o, si existe, de la carpeta
sincronizada «Solcor Colombia S.A.S. - Desarrollo GD» del usuario de Windows.

Base: datos/leads.db (SQLite). Todo el equipo ve todos los leads.
"""
import json
import os
import re
import sqlite3

import bd
import threading
import time
from datetime import date, datetime

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("EVALUADOR_LEADS", os.path.join(BASE, "..", "datos", "leads.db"))
_lock = threading.Lock()

ESTADOS = {"nuevo": "Nuevo", "evaluado": "Evaluado", "e1": "Proyecto E1", "standby": "En espera", "descartado": "Descartado"}
CANALES = ["Interno · Equipo Desarrollo", "Interno · Equipo Comercial", "Interno · Inbound",
           "Externo · Aliado", "Externo · Broker", "Externo · Otro"]
SUBCARPETAS = ["0 Resumen", "1 Predial", "2 Consultas", "3 Jurídico", "4 Conexión", "5 Energía", "6 Capex", "7 Opex",
               "8 Financiera", "9 Comercial"]
# entregable -> (subcarpeta, descripción en el nombre del archivo)
ENTREGABLES = {
    "ficha": ("0 Resumen", "Ficha Técnica"),
    "crm": ("0 Resumen", "Datos para el CRM"),
    "kml": ("1 Predial", "Layout preliminar"),
    "kmz": ("1 Predial", "Layout preliminar"),
    "predial": ("1 Predial", "Reporte Caracterización Predial"),
    "titulos": ("3 Jurídico", "Estudio de Títulos Preliminar"),
    "plano": ("5 Energía", "Plano Layout"),
    "cotizador": ("6 Capex", "Cotizador ajustado"),
    "financiero": ("8 Financiera", "Modelo Financiero"),
    "oferta": ("9 Comercial", "Propuesta de Arriendo"),
}
CAMPOS = ("nombre", "propietario", "contacto", "municipio", "depto", "vereda", "numero_predial", "matricula",
          "area_ha", "lon", "lat", "canal", "notas", "responsable")
MAX_RUTA = 235  # Windows y OneDrive fallan en silencio sobre 260; se deja margen para el nombre de los archivos


def _db():
    c = bd.conectar("ev_leads", DB_PATH, timeout=10)  # Postgres si EVALUADOR_DB_URL (motor/bd.py)
    c.row_factory = sqlite3.Row
    c.executescript("""
    create table if not exists leads(
        id integer primary key, nombre text not null, propietario text not null default '', contacto text not null default '',
        municipio text not null default '', depto text not null default '', vereda text not null default '',
        numero_predial text not null default '', matricula text not null default '', area_ha real,
        lon real, lat real, canal text not null default '', notas text not null default '',
        estado text not null default 'nuevo', proyecto text not null default '', carpeta text not null default '',
        creado_por text not null default '', usuario_id integer, creado text not null, modificado text not null);
    create index if not exists ix_leads_estado on leads(estado, modificado);
    create table if not exists entregables(
        id integer primary key, lead_id integer not null, tipo text not null, archivo text not null,
        por text not null default '', fecha text not null);
    """)
    if "responsable" not in {r[1] for r in c.execute("pragma table_info(leads)")}:
        # correo del usuario del evaluador responsable del lead (recibe el resumen al pasar a E1)
        c.execute("alter table leads add column responsable text not null default ''")
    return c


def url_sharepoint(rel=""):
    """Enlace web a una carpeta de «Desarrollo GD» (rel relativa a esa carpeta). Toma la dirección de
    EVALUADOR_SHAREPOINT_URL, de datos/config_pipeline.json («url») o de la sincronización de OneDrive del equipo."""
    from urllib.parse import quote
    base = os.environ.get("EVALUADOR_SHAREPOINT_URL", "")
    cfg = os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "config_pipeline.json")
    if not base and os.path.isfile(cfg):
        try:
            base = json.load(open(cfg, encoding="utf-8")).get("url", "")
        except (OSError, ValueError):
            pass
    raiz = raiz_pipeline()
    if not base and raiz:
        try:
            import winreg
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\SyncEngines\Providers\OneDrive")
            for i in range(winreg.QueryInfoKey(k)[0]):
                sub = winreg.OpenKey(k, winreg.EnumKey(k, i))
                try:
                    mp = winreg.QueryValueEx(sub, "MountPoint")[0]
                    if os.path.normcase(os.path.abspath(mp)) == os.path.normcase(os.path.abspath(raiz)):
                        base = winreg.QueryValueEx(sub, "FullRemotePath")[0]
                        break
                except OSError:
                    continue
        except (ImportError, OSError):
            pass
    if not base:
        return ""
    return base.rstrip("/").replace(" ", "%20") + ("/" + quote(rel.replace("\\", "/")) if rel else "")


def _ahora():
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def _num(v):
    try:
        return float(str(v).replace(",", ".")) if v not in (None, "") else None
    except ValueError:
        return None


# ---------------------------------------------------------------- pipeline en disco
def raiz_pipeline():
    """Carpeta «Desarrollo GD» (la que contiene «01 Pipeline»), o None si no está configurada ni sincronizada."""
    cand = [os.environ.get("EVALUADOR_PIPELINE", "")]
    cfg = os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "config_pipeline.json")
    if os.path.isfile(cfg):
        try:
            cand.append(json.load(open(cfg, encoding="utf-8")).get("raiz", ""))
        except (OSError, ValueError):
            pass
    cand.append(os.path.join(os.path.expanduser("~"), "Solcor Colombia S.A.S", "Solcor Colombia S.A.S. - Desarrollo GD"))
    for r in cand:
        if r and os.path.isdir(os.path.join(r, "01 Pipeline")):
            return r
    return None


def _proyectos_en_disco(raiz):
    """{numero: nombre de carpeta} de todas las carpetas «GD NNNN …» del pipeline y del archivo."""
    out = {}
    for base in ("01 Pipeline", "02 Archivo"):
        b = os.path.join(raiz, base)
        if not os.path.isdir(b):
            continue
        for etapa in os.listdir(b):
            e = os.path.join(b, etapa)
            if not os.path.isdir(e):
                continue
            for n in os.listdir(e):
                m = re.match(r"GD[ _-]?(\d{3,4})\b", n)
                if m and os.path.isdir(os.path.join(e, n)):
                    out[int(m.group(1))] = os.path.join(base, etapa, n)
    return out


def siguiente_id(raiz=None):
    raiz = raiz or raiz_pipeline()
    if not raiz:
        return None
    usados = set(_proyectos_en_disco(raiz))
    with _db() as c:  # también los que este evaluador ya asignó, por si la carpeta aún no sincroniza
        for (p,) in c.execute("select proyecto from leads where proyecto<>''"):
            m = re.search(r"(\d{3,4})", p)
            if m:
                usados.add(int(m.group(1)))
    return max(usados, default=0) + 1


def estado_pipeline():
    raiz = raiz_pipeline()
    return dict(disponible=bool(raiz), raiz=raiz or "", siguiente=f"GD {siguiente_id(raiz):04d}" if raiz else "",
                subcarpetas=SUBCARPETAS)


def _nombre_carpeta(txt):
    txt = re.sub(r'[\\/:*?"<>|\r\n\t]', " ", txt or "")
    txt = re.sub(r"\s+", " ", txt).strip(" .-")
    return txt[:40].strip()


# ---------------------------------------------------------------- leads
def _fila(c, r):
    d = dict(r)
    d["estado_txt"] = ESTADOS.get(d["estado"], d["estado"])
    d["entregables"] = [dict(tipo=e["tipo"], archivo=e["archivo"], fecha=e["fecha"], por=e["por"]) for e in
                        c.execute("select * from entregables where lead_id=? order by id desc", (r["id"],))]
    d["url"] = url_sharepoint(d["carpeta"]) if d.get("carpeta") else ""
    return d


def resumen_correo(lead, evaluacion=None, motivo="e1", con_layout=False):
    """(asunto, texto, html) del correo del proyecto: resumen, enlace a la carpeta en SharePoint y lista de archivos,
    cada uno con su enlace. evaluacion: datos que manda la interfaz (capacidad, GD, área, red…). El HTML lleva el logo
    (cid:logo) y, con con_layout, la captura del layout (cid:layout): las imágenes las incrusta correo.armar."""
    from html import escape as e
    ev = evaluacion or {}
    lugar = ", ".join(x for x in (lead.get("vereda"), lead.get("municipio"), lead.get("depto")) if x) or "por confirmar"
    filas = [("Proyecto", f"{lead['proyecto']} {lead['nombre']}"), ("Ubicación", lugar),
             ("Coordenada", f"{lead['lat']:.6f}, {lead['lon']:.6f}" if lead.get("lat") is not None else "por confirmar"),
             ("Propietario", lead.get("propietario") or "por confirmar"), ("Contacto", lead.get("contacto") or "por confirmar"),
             ("Número predial", lead.get("numero_predial") or "por confirmar"), ("Matrícula", lead.get("matricula") or "por confirmar"),
             ("Responsable", lead.get("responsable") or "sin asignar"), ("Canal", lead.get("canal") or "sin registrar")]
    for clave, nombre in (("capacidad", "Capacidad"), ("gd", "Bloques GD"), ("area", "Área"), ("red", "Red cercana"),
                          ("mt", "Línea MT (punto de conexión → CT)"),
                          ("restricciones", "Restricciones"), ("titulos", "Estudio de títulos"), ("tir", "TIR del proyecto")):
        if ev.get(clave):
            filas.append((nombre, str(ev[clave])))
    pref = (lead.get("carpeta") or "").rstrip("\\/") + os.sep
    # (nombre a mostrar, enlace web del archivo en SharePoint); el enlace sale de la ruta relativa a la raíz del pipeline
    archivos = sorted({((x["archivo"][len(pref):] if x["archivo"].startswith(pref) else x["archivo"]).replace("\\", " / "),
                        url_sharepoint(x["archivo"]) if lead.get("url") else "")
                       for x in lead.get("entregables") or []})
    url = lead.get("url") or ""
    asunto = (f"Nuevo proyecto en E1: {lead['proyecto']} {lead['nombre']}" if motivo == "e1"
              else f"Entregables actualizados: {lead['proyecto']} {lead['nombre']}")
    intro = ("Se creó un nuevo proyecto en E1 desde el Evaluador GD." if motivo == "e1"
             else "Se guardaron entregables del proyecto desde el Evaluador GD.")
    texto = "\n".join([intro, ""] + [f"{a}: {b}" for a, b in filas] + ["", f"Carpeta en SharePoint: {url or lead['carpeta']}", "",
                      f"Archivos en la carpeta ({len(archivos)}):"] + [f"  - {a}" + (f"\n    {u}" if u else "") for a, u in archivos]
                     + ["", "Evaluador GD · Solcor"])
    html = ("<div style='font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#0d2436;max-width:720px'>"
            "<p style='margin:0 0 14px;padding-bottom:10px;border-bottom:3px solid #7cc01f'>"
            "<img src='cid:logo' alt='Solcor Colombia' height='42' style='height:42px'></p>"
            f"<p>{e(intro)}</p>"
            "<table style='border-collapse:collapse'>" + "".join(
                f"<tr><td style='padding:3px 12px 3px 0;color:#5b6b75'>{e(a)}</td><td style='padding:3px 0'><b>{e(b)}</b></td></tr>" for a, b in filas) +
            "</table>" +
            ("<p><b>Layout del predio evaluado</b></p><p><img src='cid:layout' alt='Layout del predio' width='680' "
             "style='width:100%;max-width:680px;border:1px solid #dde3e6;border-radius:6px'></p>" if con_layout else "") +
            (f"<p><a href='{e(url)}' style='background:#16a36b;color:#fff;padding:8px 14px;border-radius:6px;text-decoration:none'>"
             "Abrir la carpeta en SharePoint</a></p>" if url else f"<p>Carpeta: {e(lead['carpeta'])}</p>") +
            f"<p><b>Archivos en la carpeta ({len(archivos)})</b></p><ul>" +
            "".join(f"<li><a href='{e(u)}'>{e(a)}</a></li>" if u else f"<li>{e(a)}</li>" for a, u in archivos) + "</ul>"
            "<p style='color:#5b6b75;font-size:12px'>Evaluador GD · Solcor</p></div>")
    return asunto, texto, html


def listar(estado=None):
    with _db() as c:
        q, p = "select * from leads", []
        if estado in ESTADOS:
            q, p = q + " where estado=?", [estado]
        return [_fila(c, r) for r in c.execute(q + " order by modificado desc limit 1000", p)]


def obtener(lid):
    with _db() as c:
        r = c.execute("select * from leads where id=?", (lid,)).fetchone()
        if not r:
            raise ValueError("El lead no existe.")
        return _fila(c, r)


def guardar(u, datos, lid=None):
    v = {k: datos.get(k) for k in CAMPOS}
    v["nombre"] = (v["nombre"] or "").strip()[:120]
    if len(v["nombre"]) < 3:
        raise ValueError("Ponle un nombre al lead (predio, finca o sector).")
    for k in ("lon", "lat", "area_ha"):
        v[k] = _num(v[k])
    if (v["lon"] is None) != (v["lat"] is None) or (v["lon"] is not None and not (-82 < v["lon"] < -66 and -5 < v["lat"] < 14)):
        raise ValueError("La coordenada no es válida (debe quedar en Colombia).")
    for k in CAMPOS:
        if k not in ("lon", "lat", "area_ha"):
            v[k] = str(v[k] or "").strip()[:2000 if k == "notas" else 200]
    ahora = _ahora()
    with _lock, _db() as c:
        if lid:
            if not c.execute("select 1 from leads where id=?", (lid,)).fetchone():
                raise ValueError("El lead no existe.")
            c.execute("update leads set " + ", ".join(f"{k}=?" for k in CAMPOS) + ", modificado=? where id=?",
                      [v[k] for k in CAMPOS] + [ahora, lid])
            return lid
        return c.execute("insert into leads(" + ", ".join(CAMPOS) + ", creado_por, usuario_id, creado, modificado) values ("
                         + ",".join("?" * len(CAMPOS)) + ",?,?,?,?)",
                         [v[k] for k in CAMPOS] + [u.get("nombre") or u.get("correo") or "", u.get("id"), ahora, ahora]).lastrowid


def es_suyo(u, lead):
    """El lead lo creó el usuario o es su responsable (alcance «solo los suyos», #51)."""
    return lead.get("usuario_id") == u.get("id") or (lead.get("responsable") or "").lower() == (u.get("correo") or "").lower()


def conteo_por_usuario():
    """{usuario_id: dict(leads, proyectos)} y {correo: leads como responsable} para las métricas del administrador (#51)."""
    with _db() as c:
        creados = {r[0]: dict(leads=r[1], proyectos=r[2] or 0) for r in c.execute(
            "select usuario_id, count(*), sum(case when estado='e1' then 1 else 0 end) from leads group by usuario_id")}
        resp = {(r[0] or "").lower(): r[1] for r in c.execute("select responsable, count(*) from leads where responsable<>'' group by responsable")}
    return creados, resp


def puntos_pipeline(evaluaciones_pts, u=None):
    """Puntos de la capa «Pipeline Solcor»: cada lead con coordenada (su estado; si ya es proyecto, la etapa de la
    carpeta donde está hoy en SharePoint: E1, E2…) y cada evaluación guardada que no está ligada a un lead.
    u: si se da, solo los leads de ese usuario (alcance «solo los suyos», #51)."""
    raiz = raiz_pipeline()
    en_disco = _proyectos_en_disco(raiz) if raiz else {}
    out = []
    for l in listar():
        if l["lat"] is None or (u is not None and not es_suyo(u, l)):
            continue
        etapa = ""
        m = re.search(r"(\d{3,4})", l["proyecto"] or "")
        if m and int(m.group(1)) in en_disco:
            partes = en_disco[int(m.group(1))].split(os.sep)
            etapa = partes[1] if partes[0] == "01 Pipeline" else "Archivo"
        out.append(dict(tipo="lead", id=l["id"], nombre=l["nombre"], proyecto=l["proyecto"], estado=l["estado"],
                        estado_txt=(f"Proyecto {etapa}" if etapa and etapa != "Archivo" else "Archivado" if etapa else l["estado_txt"]),
                        etapa=etapa, lon=l["lon"], lat=l["lat"], municipio=", ".join(x for x in (l["municipio"], l["depto"]) if x),
                        responsable=l.get("responsable", ""), url=l.get("url", "")))
    ligadas = {l["id"] for l in out}
    for eid, nombre, autor, lead_id, lon, lat, res in evaluaciones_pts:
        if lead_id in ligadas:
            continue
        out.append(dict(tipo="evaluacion", id=eid, nombre=nombre, proyecto="", estado="evaluacion", estado_txt="Evaluación sin lead",
                        etapa="", lon=lon, lat=lat, municipio=res.get("municipio", ""), autor=autor,
                        mwp=res.get("mwp"), n_gd=res.get("n_gd")))
    return out


def cambiar_estado(lid, estado):
    if estado not in ESTADOS or estado == "e1":
        raise ValueError("Estado no válido (a E1 se pasa con «Crear proyecto»).")
    with _lock, _db() as c:
        r = c.execute("select estado from leads where id=?", (lid,)).fetchone()
        if not r:
            raise ValueError("El lead no existe.")
        if r["estado"] == "e1":
            raise ValueError("El lead ya es un proyecto en E1; su etapa se maneja en el CRM y en la carpeta.")
        c.execute("update leads set estado=?, modificado=? where id=?", (estado, _ahora(), lid))


def marcar_evaluado(lid):
    with _lock, _db() as c:
        c.execute("update leads set estado='evaluado', modificado=? where id=? and estado='nuevo'", (_ahora(), lid))


def borrar(u, lid):
    with _lock, _db() as c:
        r = c.execute("select usuario_id, estado from leads where id=?", (lid,)).fetchone()
        if not r:
            raise ValueError("El lead no existe.")
        if r["estado"] == "e1":
            raise ValueError("El lead ya tiene carpeta de proyecto; no se borra desde aquí.")
        if r["usuario_id"] != u.get("id") and u.get("rol") != "admin":
            raise PermissionError("Solo quien creó el lead o un administrador puede borrarlo.")
        c.execute("delete from leads where id=?", (lid,))


# ---------------------------------------------------------------- paso a E1
def crear_proyecto(u, lid, nombre_corto=None):
    """Asigna el siguiente ID, crea «01 Pipeline/E1/GD 00NN Nombre» con sus subcarpetas y deja el lead en E1."""
    raiz = raiz_pipeline()
    if not raiz:
        raise ValueError("No se encontró la carpeta «Desarrollo GD» sincronizada en este equipo. "
                         "Configura su ruta en datos/config_pipeline.json.")
    lead = obtener(lid)
    if lead["estado"] == "e1":
        raise ValueError(f"Este lead ya es el proyecto {lead['proyecto']}.")
    nombre = _nombre_carpeta(nombre_corto or lead["nombre"])
    if len(nombre) < 3:
        raise ValueError("El nombre del proyecto debe tener al menos 3 caracteres.")
    with _lock:
        num = siguiente_id(raiz)
        proyecto = f"GD {num:04d}"
        rel = os.path.join("01 Pipeline", "E1", f"{proyecto} {nombre}")
        destino = os.path.join(raiz, rel)
        # debe caber: carpeta del proyecto + subcarpeta + un nombre de archivo de la convención (~60 caracteres)
        if len(destino) + 1 + max(len(s) for s in SUBCARPETAS) + 1 + 60 > MAX_RUTA + 20:
            raise ValueError("El nombre es muy largo para la ruta de SharePoint; usa uno más corto.")
        if os.path.exists(destino):
            raise ValueError(f"Ya existe la carpeta {rel}.")
        os.makedirs(os.path.join(raiz, "01 Pipeline", "E1"), exist_ok=True)
        os.makedirs(destino)
        for s in SUBCARPETAS:
            os.makedirs(os.path.join(destino, s))
        with _db() as c:
            c.execute("update leads set estado='e1', proyecto=?, carpeta=?, modificado=? where id=?",
                      (proyecto, rel, _ahora(), lid))
    resumen = [f"{proyecto} {nombre}", "", f"Creado desde el Evaluador GD el {date.today():%d/%m/%Y} por {u.get('nombre') or u.get('correo') or ''}.",
               "", f"Lead: {lead['nombre']}", f"Propietario: {lead['propietario'] or 'por confirmar'}",
               f"Contacto: {lead['contacto'] or 'por confirmar'}",
               f"Ubicación: {', '.join(x for x in (lead['vereda'], lead['municipio'], lead['depto']) if x) or 'por confirmar'}",
               f"Coordenada: {lead['lat']}, {lead['lon']}" if lead["lat"] is not None else "Coordenada: por confirmar",
               f"Número predial: {lead['numero_predial'] or 'por confirmar'}", f"Matrícula: {lead['matricula'] or 'por confirmar'}",
               f"Canal: {lead['canal'] or 'sin registrar'}", "", "Notas:", lead["notas"] or "—"]
    guardar_entregable(u, lid, "lead", "\r\n".join(resumen).encode("utf-8"), ".txt")
    return obtener(lid)


def guardar_entregable(u, lid, tipo, datos, ext, descripcion=None):
    """Escribe un entregable en la subcarpeta que le corresponde, con el nombre de la convención y sin pisar
    versiones anteriores (v01, v02…). Devuelve la ruta relativa a «Desarrollo GD»."""
    lead = obtener(lid)
    raiz = raiz_pipeline()
    if lead["estado"] != "e1" or not lead["carpeta"] or not raiz:
        raise ValueError("El lead aún no tiene carpeta de proyecto: primero pásalo a E1.")
    sub, desc = ("0 Resumen", "Ficha del lead") if tipo == "lead" else ENTREGABLES.get(tipo, (None, None))
    if not sub:
        raise ValueError("Tipo de entregable no reconocido.")
    ext = ext if ext.startswith(".") else "." + ext
    if ext.lower() not in (".docx", ".pptx", ".kml", ".kmz", ".pdf", ".xlsx", ".dxf", ".txt", ".png", ".jpg"):
        raise ValueError("Tipo de archivo no admitido como entregable.")
    carpeta = os.path.join(raiz, lead["carpeta"], sub)
    # OneDrive puede ocultar la carpeta unos segundos mientras sincroniza (#53: error pasajero en GD 0091)
    for _ in range(5):
        if os.path.isdir(os.path.join(raiz, lead["carpeta"])):
            break
        time.sleep(1.5)
    if not os.path.isdir(os.path.join(raiz, lead["carpeta"])):
        raise ValueError(f"No se encuentra la carpeta del proyecto ({lead['carpeta']}); puede haberse movido de etapa.")
    os.makedirs(carpeta, exist_ok=True)
    desc = _nombre_carpeta(descripcion or desc)
    with _lock:
        for v in range(1, 100):
            nombre = f"{date.today():%Y%m%d} E1 {desc} v{v:02d}{ext}"
            ruta = os.path.join(carpeta, nombre)
            if not os.path.exists(ruta):
                break
        else:
            raise ValueError("Demasiadas versiones del mismo entregable hoy.")
        if len(ruta) > 255:
            raise ValueError("La ruta del archivo supera el límite de Windows; acorta el nombre del proyecto.")
        with open(ruta, "wb") as f:
            f.write(datos)
        rel = os.path.join(lead["carpeta"], sub, nombre)
        with _db() as c:
            c.execute("insert into entregables(lead_id, tipo, archivo, por, fecha) values (?,?,?,?,?)",
                      (lid, tipo, rel, u.get("nombre") or u.get("correo") or "", _ahora()))
            c.execute("update leads set modificado=? where id=?", (_ahora(), lid))
    return rel
