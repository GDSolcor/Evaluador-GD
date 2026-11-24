"""
Bases de datos de la herramienta (panel de administración, ticket #40): de dónde sale cada una, cuándo se descargó por
última vez y cómo se actualiza.

  - REGISTRO: las bases conocidas, con su enlace público, los archivos que producen (la fecha de última descarga es la
    del archivo más reciente) y el método de actualización:
      auto    el servidor corre el descargador de herramientas/ en segundo plano (servicios públicos sin captcha)
      chrome  el portal pide captcha o sesión: se recorre desde el visor en Chrome y luego se importa (instrucciones)
      manual  archivo entregado o respaldo (Excel, shapefile, GIS Solcor): se reemplaza a mano (instrucciones)
  - datos/fuentes.json: enlaces y notas editados por el administrador, bases nuevas que él ingresa y el resultado de la
    última actualización de cada una.

Una actualización a la vez. Antes de correr el descargador, su caché se aparta (*.anterior) para que la descarga sea
completa y no solo reanude; si falla, se restaura. Las bases de predios (SQLite) conservan sus predios y solo
reinician el registro de avance. Tras una descarga de red se reconstruye datos/red_or/red_complementaria.db.
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time

BASE = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.abspath(os.path.join(BASE, ".."))
DATOS = os.path.join(RAIZ, "datos")
CONFIG = os.environ.get("EVALUADOR_FUENTES", os.path.join(DATOS, "fuentes.json"))
LOGS = os.path.join(DATOS, "logs")
RED = ["motor/red_complementaria.py"]

CATEGORIAS = ["Red eléctrica", "Predios", "Restricciones y capas", "Base cartográfica", "Otra"]


def _f(id, nombre, categoria, entidad, enlace, archivos, metodo, comando=None, despues=None, cache=None, sqlite_avance=None,
       instrucciones=""):
    return dict(id=id, nombre=nombre, categoria=categoria, entidad=entidad, enlace=enlace, archivos=archivos, metodo=metodo,
                comando=comando, despues=despues or [], cache=cache or [], sqlite_avance=sqlite_avance, instrucciones=instrucciones)


REGISTRO = [
    # ---------------------------------------------------------------- red eléctrica
    _f("gis_solcor", "Red ENEL y EBSA (GIS Solcor)", "Red eléctrica", "Solcor", "",
       ["gis/GIS_SOLCOR_Database.zip"], "manual",
       instrucciones="Respaldo del GIS Solcor (23-sep-2026). Para actualizarlo se reemplaza datos/gis/GIS_SOLCOR_Database.zip con un "
                     "respaldo nuevo y se reinicia el servidor."),
    _f("essa", "ESSA · geoportal CREG 174", "Red eléctrica", "ESSA", "https://geoportal.api-essa-electrosoftware.xyz",
       ["red_or/essa/autogenerador174_p1.json", "red_or/essa/subestaciones.json"], "auto",
       ["herramientas/descargar_red_essa.py"], RED, ["red_or/essa/teselas"]),
    _f("cens", "CENS · disponibilidad de la red", "Red eléctrica", "CENS (Grupo EPM)",
       "https://www.cens.com.co/Portals/_default/Skins/cens/html/Disponibilidad_de_la_red_Nivel1.html",
       ["red_or/cens/tramos_mt.json", "red_or/cens/transformadores.json"], "auto",
       ["herramientas/descargar_red_arcgis.py", "cens"], RED, ["red_or/cens/cache"]),
    _f("epm", "EPM · geoportal Grupo EPM", "Red eléctrica", "EPM",
       "https://experience.arcgis.com/experience/742f0b4891f346b68e5512f807b80387",
       ["red_or/epm/tramos_mt.json", "red_or/epm/transformadores.json"], "auto",
       ["herramientas/descargar_red_arcgis.py", "epm"], RED, ["red_or/epm/cache"]),
    _f("edeq", "EDEQ · mapa de disponibilidad", "Red eléctrica", "EDEQ",
       "https://aplicaciones.edeq.com.co/portal/mapadisponibilidad",
       ["red_or/edeq/transformers.json", "red_or/edeq/detalle"], "auto",
       ["herramientas/descargar_red_edeq.py", "--pausa", "4"], RED,
       ["red_or/edeq/transformers.json", "red_or/edeq/substations.json", "red_or/edeq/detalle"],
       instrucciones="Unas 8.700 consultas: tarda alrededor de 5 horas."),
    _f("afinia", "Afinia (y zona Cesar) · visor CREG 174", "Red eléctrica", "Afinia", "https://visor.confirma.com.co",
       ["red_or/afinia/filas.json"], "auto",
       ["herramientas/descargar_red_confirma.py", "afinia", "atenea"], RED, ["red_or/afinia/cache"]),
    _f("eep", "Energía de Pereira · disponibilidad de la red", "Red eléctrica", "Energía de Pereira",
       "https://solicitudes.eep.com.co/#/home", ["red_or/eep/filas.json"], "auto",
       ["herramientas/descargar_red_eep.py"], RED, ["red_or/eep/cache.json"],
       instrucciones="Pereira y Cartago: tarda alrededor de media hora."),
    _f("ebsa", "EBSA · visor GD-Connect", "Red eléctrica", "EBSA", "https://gdc.ebsa.com.co/solicitante/",
       ["red_or/ebsa/filas.json"], "chrome",
       instrucciones="El visor pide aceptar la política y a veces captcha. Se recorre en Chrome con herramientas/recolector_gdc.js "
                     "y se importa con «python herramientas/importar_ebsa.py <archivo>» y luego «python motor/red_complementaria.py». "
                     "Pídelo en un ticket de soporte."),
    _f("enerca", "ENERCA · visor GD-Connect", "Red eléctrica", "ENERCA", "http://34.68.97.30/solicitante/",
       ["red_or/enerca/filas.json"], "chrome",
       instrucciones="Visor GD-Connect con captcha: se recorre en Chrome con herramientas/recolector_gdc.js y se importa con "
                     "«python herramientas/importar_ebsa.py <archivo> enerca». Pídelo en un ticket de soporte."),
    _f("emsa", "EMSA · visor GD-Connect", "Red eléctrica", "EMSA", "http://35.193.94.55/solicitante/",
       ["red_or/emsa/filas.json"], "chrome",
       instrucciones="Visor GD-Connect con captcha: se recorre en Chrome con herramientas/recolector_gdc.js y se importa con "
                     "«python herramientas/importar_ebsa.py <archivo> emsa». Pídelo en un ticket de soporte."),
    _f("chec", "CHEC · visor CREG 174", "Red eléctrica", "CHEC", "https://portal.almeraim.com/sgichec/PAUTOG/external/r174",
       ["red_or/chec/filas.json"], "chrome",
       instrucciones="El servidor de CHEC entrega un token solo después de un reCAPTCHA: se recorre desde el visor en Chrome "
                     "(descarga chec_*.json a Descargas) y se importa con «python herramientas/importar_chec.py». Pídelo en un "
                     "ticket de soporte."),
    _f("aire", "Air-e · red MT (shapefile)", "Red eléctrica", "Air-e", "", ["red_or/aire/filas.json"], "manual",
       instrucciones="Shapefiles de líneas MT entregados por Air-e. Se importan con «python herramientas/importar_red_shp.py aire "
                     "\"Air-e\" <carpetas>»."),
    _f("celsia", "Celsia · transformadores (Excel público)", "Red eléctrica", "Celsia", "",
       ["red_or/celsia/Transformadores-de-distribucion-CELSIA.xlsx"], "manual",
       instrucciones="Excel público de transformadores de Celsia (dic-2021). Se reemplaza el archivo y se reconstruye la red."),
    # ---------------------------------------------------------------- predios
    _f("igac", "Catastro IGAC (predios, veredas, manzanas)", "Predios", "IGAC", "https://geoportal.igac.gov.co/contenido/datos-abiertos-catastro",
       ["igac/catastro"], "manual",
       instrucciones="Corte del 31-ago-2026. Se descarga el nuevo corte del catastro público del IGAC, se reemplaza "
                     "datos/igac/catastro y se reindexa con «python herramientas/indexar_catastro.py»."),
    _f("acc", "Catastro Cundinamarca (ACC)", "Predios", "Agencia Catastral de Cundinamarca", "https://www.acc.gov.co/geoportal/v1",
       ["catastro_acc.sqlite"], "auto", ["herramientas/descargar_predios_acc.py"], sqlite_avance="catastro_acc.sqlite",
       instrucciones="76 municipios: tarda varias horas."),
    _f("antioquia", "Catastro Antioquia (predios rurales)", "Predios", "Gerencia de Catastro de Antioquia",
       "https://geoags1.catastroantioquia-mas.com/arcgis/rest/services/Regiones_M",
       ["catastro_antioquia.sqlite"], "auto", ["herramientas/descargar_predios_antioquia.py"], sqlite_avance="catastro_antioquia.sqlite",
       instrucciones="113 municipios: tarda varias horas."),
    _f("amco", "Catastro AMCO (Pereira, Dosquebradas, La Virginia)", "Predios", "Área Metropolitana Centro Occidente",
       "https://ideamco.gov.co/geovisor/ideep.php?mun=pereira", ["catastro_amco.sqlite"], "auto",
       ["herramientas/descargar_predios_amco.py"], sqlite_avance="catastro_amco.sqlite"),
    _f("armenia", "Catastro Armenia", "Predios", "Alcaldía de Armenia (operador Catastro Bogotá)",
       "https://services1.arcgis.com/J5ltM0ovtzXUbp7B/arcgis/rest/services/CapasCatastroArmenia/FeatureServer",
       ["catastro_armenia.sqlite"], "auto", ["herramientas/descargar_predios_armenia.py"], sqlite_avance="catastro_armenia.sqlite"),
    # ---------------------------------------------------------------- restricciones
    _f("icanh", "ICANH · arqueología", "Restricciones y capas", "ICANH", "https://geovector.icanh.gov.co",
       ["capas_complementarias/icanh"], "auto", ["herramientas/descargar_capas_arcgis.py", "icanh"],
       cache=["capas_complementarias/icanh/cache"]),
    _f("anh", "ANH · tierras, pozos y sísmica", "Restricciones y capas", "ANH", "https://geovisor.anh.gov.co",
       ["capas_complementarias/anh"], "auto", ["herramientas/descargar_capas_arcgis.py", "anh"],
       cache=["capas_complementarias/anh/cache"]),
    _f("minambiente", "MinAmbiente · Reserva Forestal Ley 2ª", "Restricciones y capas", "MinAmbiente (SIAC)",
       "https://geo.minambiente.gov.co", ["capas_complementarias/minambiente"], "auto",
       ["herramientas/descargar_capas_arcgis.py", "minambiente"], cache=["capas_complementarias/minambiente/cache"]),
    _f("anla", "ANLA · proyectos licenciados", "Restricciones y capas", "ANLA", "https://portalsig.anla.gov.co",
       ["capas_complementarias/anla"], "auto", ["herramientas/descargar_capas_arcgis.py", "anla"],
       cache=["capas_complementarias/anla/cache"]),
    # autoridades ambientales regionales (#47): las capas tal como las publican sus visores
    _f("corpoboyaca", "Corpoboyacá · capas del visor público", "Restricciones y capas", "Corpoboyacá",
       "https://corpoboyaca-visorpublico.geoambiental.co/visor",
       ["autoridades_ambientales.db"], "auto", ["herramientas/descargar_autoridades_ambientales.py", "corpoboyaca", "--rehacer"],
       cache=["autoridades_ambientales/corpoboyaca"]),
    _f("car", "CAR Cundinamarca · datos geográficos abiertos", "Restricciones y capas", "CAR",
       "https://datosgeograficos.car.gov.co/",
       ["autoridades_ambientales.db"], "auto", ["herramientas/descargar_autoridades_ambientales.py", "car", "--rehacer"],
       cache=["autoridades_ambientales/car"]),
    # ---------------------------------------------------------------- cartografía
    _f("hidrografia", "Hidrografía IGAC 1:100.000", "Base cartográfica", "IGAC", "https://www.colombiaenmapas.gov.co",
       ["igac/hidrografia_gdb", "igac/hidrografia_100k"], "manual",
       instrucciones="GeoPackage de la hidrografía 1:100.000 del IGAC. Se reemplaza el archivo y se reinicia el servidor."),
]
_POR_ID = {f["id"]: f for f in REGISTRO}

_lock = threading.Lock()
_tarea = {}  # la actualización en curso (una a la vez)


# ------------------------------------------------------------------ configuración guardada
def _leer():
    try:
        with open(CONFIG, encoding="utf-8") as fh:
            c = json.load(fh)
    except (OSError, ValueError):
        c = {}
    for k in ("editadas", "nuevas", "ultima"):
        c.setdefault(k, [] if k == "nuevas" else {})
    return c


def _escribir(c):
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    tmp = CONFIG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(c, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, CONFIG)


# ------------------------------------------------------------------ listado
def _info_archivos(rutas):
    """(fecha del archivo más reciente, MB totales) de archivos o carpetas bajo datos/."""
    ultima, tam = 0.0, 0
    for r in rutas:
        p = os.path.join(DATOS, r)
        if os.path.isfile(p):
            ultima, tam = max(ultima, os.path.getmtime(p)), tam + os.path.getsize(p)
        elif os.path.isdir(p):
            for raiz, _, nombres in os.walk(p):
                for n in nombres:
                    q = os.path.join(raiz, n)
                    try:
                        ultima, tam = max(ultima, os.path.getmtime(q)), tam + os.path.getsize(q)
                    except OSError:
                        pass
    return (time.strftime("%Y-%m-%d %H:%M", time.localtime(ultima)) if ultima else None), round(tam / 1e6, 1)


def listar():
    c = _leer()
    out = []
    for f in REGISTRO:
        e = c["editadas"].get(f["id"], {})
        fecha, mb = _info_archivos(f["archivos"])
        out.append(dict(id=f["id"], nombre=f["nombre"], categoria=f["categoria"], entidad=f["entidad"],
                        enlace=e.get("enlace") or f["enlace"], notas=e.get("notas", ""), metodo=f["metodo"],
                        instrucciones=f["instrucciones"], fecha=fecha, mb=mb, ultima=c["ultima"].get(f["id"]), propia=False))
    for n in c["nuevas"]:
        out.append(dict(n, metodo="manual", mb=None, ultima=c["ultima"].get(n["id"]), propia=True,
                        instrucciones=n.get("instrucciones") or "Base ingresada por el administrador: se actualiza a mano y se "
                                                                  "registra la fecha con «Marcar como actualizada»."))
    return dict(fuentes=out, categorias=CATEGORIAS, tarea=estado_tarea())


# ------------------------------------------------------------------ ingresar y editar
def guardar(datos, por):
    """Edita enlace y notas de una base, o ingresa una base nueva (sin id)."""
    c = _leer()
    fid = (datos.get("id") or "").strip()
    enlace = (datos.get("enlace") or "").strip()[:500]
    if enlace and not enlace.startswith(("http://", "https://")):
        raise ValueError("El enlace debe empezar por http:// o https://")
    notas = (datos.get("notas") or "").strip()[:1000]
    if fid in _POR_ID:
        c["editadas"][fid] = dict(enlace=enlace, notas=notas, por=por, fecha=time.strftime("%Y-%m-%d %H:%M"))
    else:
        nombre = (datos.get("nombre") or "").strip()[:120]
        if not nombre:
            raise ValueError("Escribe el nombre de la base.")
        cat = datos.get("categoria") if datos.get("categoria") in CATEGORIAS else "Otra"
        existente = next((n for n in c["nuevas"] if n["id"] == fid), None)
        if existente:
            existente.update(nombre=nombre, categoria=cat, entidad=(datos.get("entidad") or "").strip()[:120], enlace=enlace, notas=notas)
        else:
            c["nuevas"].append(dict(id=f"propia_{int(time.time() * 1000)}", nombre=nombre, categoria=cat,
                                    entidad=(datos.get("entidad") or "").strip()[:120], enlace=enlace, notas=notas,
                                    fecha=None, creada_por=por, creada=time.strftime("%Y-%m-%d %H:%M")))
    _escribir(c)


def marcar_actualizada(fid, por):
    """Para bases manuales o propias: registra hoy como fecha de actualización."""
    c = _leer()
    n = next((x for x in c["nuevas"] if x["id"] == fid), None)
    if not n and fid not in _POR_ID:
        raise ValueError("Base desconocida.")
    hoy = time.strftime("%Y-%m-%d %H:%M")
    if n:
        n["fecha"] = hoy
    c["ultima"][fid] = dict(fecha=hoy, resultado="marcada a mano", por=por)
    _escribir(c)


def eliminar(fid):
    c = _leer()
    antes = len(c["nuevas"])
    c["nuevas"] = [n for n in c["nuevas"] if n["id"] != fid]
    if len(c["nuevas"]) == antes:
        raise ValueError("Solo se pueden quitar las bases ingresadas a mano.")
    c["ultima"].pop(fid, None)
    _escribir(c)


# ------------------------------------------------------------------ actualizar
def estado_tarea():
    t = _tarea.get("actual")
    if not t:
        return None
    log = ""
    try:
        with open(t["log"], encoding="utf-8", errors="replace") as fh:
            log = "".join(fh.readlines()[-12:])
    except OSError:
        pass
    return dict(id=t["id"], nombre=t["nombre"], inicio=t["inicio"], fin=t.get("fin"), estado=t["estado"], log=log)


def _apartar(rutas):
    movidas = []
    for r in rutas:
        p = os.path.join(DATOS, r)
        if os.path.exists(p):
            dest = p + ".anterior"
            if os.path.isdir(dest):
                shutil.rmtree(dest, ignore_errors=True)
            elif os.path.exists(dest):
                os.remove(dest)
            os.replace(p, dest)
            movidas.append((p, dest))
    return movidas


def _restaurar(movidas):
    for p, dest in movidas:
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)
        elif os.path.exists(p):
            os.remove(p)
        if os.path.exists(dest):
            os.replace(dest, p)


def _reiniciar_avance(sqlite_rel):
    """Bases de predios: conservan los predios (se actualizan por clave) y olvidan qué páginas o recuadros ya se bajaron."""
    p = os.path.join(DATOS, sqlite_rel)
    if not os.path.isfile(p):
        return
    db = sqlite3.connect(p, timeout=60)
    try:
        tablas = {r[0] for r in db.execute("select name from sqlite_master where type='table'")}
        for t in ("paginas", "celdas", "municipios"):
            if t in tablas:
                db.execute(f"delete from {t}")
        db.commit()
    finally:
        db.close()


def _correr(t, f, por):
    os.makedirs(LOGS, exist_ok=True)
    movidas = []
    ok = False
    try:
        movidas = _apartar(f["cache"])
        if f["sqlite_avance"]:
            _reiniciar_avance(f["sqlite_avance"])
        pasos = [f["comando"]] + [[d] for d in f["despues"]]
        with open(t["log"], "w", encoding="utf-8") as log:
            for paso in pasos:
                log.write(f"== {' '.join(paso)}\n")
                log.flush()
                p = subprocess.Popen([sys.executable, "-u", *paso], cwd=RAIZ, stdout=log, stderr=subprocess.STDOUT,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                t["pid"] = p.pid
                if p.wait() != 0:
                    log.write(f"\nTerminó con código {p.returncode}.\n")
                    raise RuntimeError(f"{paso[0]} terminó con código {p.returncode}")
        ok = True
        for _, dest in movidas:  # la caché anterior ya no hace falta
            shutil.rmtree(dest, ignore_errors=True) if os.path.isdir(dest) else os.remove(dest)
        if f["despues"] == RED:
            try:
                import red
                red._arbol = None  # el árbol de capas de la red se vuelve a armar con la base nueva
            except Exception:
                pass
    except Exception as e:
        _restaurar(movidas)
        t["error"] = str(e)[:300]
    finally:
        t["fin"] = time.strftime("%Y-%m-%d %H:%M")
        t["estado"] = "terminada" if ok else "con error"
        c = _leer()
        c["ultima"][f["id"]] = dict(fecha=t["fin"], resultado="ok" if ok else f"error: {t.get('error', '')}", por=por)
        _escribir(c)


def actualizar(fid, por):
    f = _POR_ID.get(fid)
    if not f:
        raise ValueError("Base desconocida.")
    if f["metodo"] != "auto":
        raise ValueError("Esta base no se descarga sola: sigue las instrucciones de la fila.")
    with _lock:
        actual = _tarea.get("actual")
        if actual and actual["estado"] == "en curso":
            raise ValueError(f"Ya se está actualizando «{actual['nombre']}». Espera a que termine.")
        t = dict(id=fid, nombre=f["nombre"], inicio=time.strftime("%Y-%m-%d %H:%M"), estado="en curso",
                 log=os.path.join(LOGS, f"fuente_{fid}.log"))
        _tarea["actual"] = t
    threading.Thread(target=_correr, args=(t, f, por), daemon=True).start()
    return estado_tarea()
