"""Carga los datos que el Evaluador usa al funcionar en Postgres (Supabase), para correrlo en la nube.

    set EVALUADOR_DB_URL=postgresql://...   (cadena de conexión; no se escribe en ningún archivo)
    python herramientas/migrar_a_nube.py                 # todo lo que falte
    python herramientas/migrar_a_nube.py --solo gis      # una parte: comun, negocio, gis, shp, hidro, catastro, acc, raster
    python herramientas/migrar_a_nube.py --rehacer gis   # vuelve a cargar una parte aunque ya esté
    python herramientas/migrar_a_nube.py --estado        # qué hay cargado y cuánto ocupa

Es reanudable: cada tabla cargada queda anotada en ev_comun.cargas y no se vuelve a subir.
Cada base SQLite queda en su propio esquema (ev_*) para no mezclarse con las tablas de la Ruta (public).
Lo que no se sube (descargas en JSON, ZIP, temporales, insumos ya convertidos) sigue en datos/ como respaldo.
"""
import argparse
import array
import json
import os
import re
import sqlite3
import struct
import sys
import time
import zlib

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATOS = os.path.join(BASE, "datos")
GIS_DIR = os.path.join(DATOS, "gis", "GIS_SOLCOR")
CAT_DIR = os.path.join(DATOS, "igac", "catastro", "CatastroPubliconAgosto2026")

NEGOCIO = {"ev_usuarios": "usuarios.db", "ev_evaluaciones": "evaluaciones.db", "ev_leads": "leads.db",
           "ev_soporte": "soporte.db"}
ACC = {"ev_cat_acc": "catastro_acc.sqlite", "ev_cat_antioquia": "catastro_antioquia.sqlite",
       "ev_cat_amco": "catastro_amco.sqlite", "ev_cat_armenia": "catastro_armenia.sqlite"}
CAPAS_FEATURES = [  # (origen, ruta) en el orden de la vista «features» de motor/gis.py
    ("main", os.path.join(GIS_DIR, "data", "coexistencia.db")),
    ("rc", os.path.join(DATOS, "red_or", "red_complementaria.db")),
    ("cc", os.path.join(DATOS, "capas_complementarias.db")),
    ("aa", os.path.join(DATOS, "autoridades_ambientales.db")),
]
REEMPLAZADAS_POR_COMPLEMENTO = ("ANLA_Linea",)  # igual que motor/gis.py
SHP_CATASTRO = ("R_TERRENO", "U_TERRENO", "R_VEREDA", "U_MANZANA")
GPKG = os.path.join(DATOS, "igac", "hidrografia_gdb", "Hidrografia_completa.gpkg")
SHP_HIDRO = os.path.join(DATOS, "igac", "hidrografia_100k")


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def conectar_pg():
    import psycopg
    url = os.environ.get("EVALUADOR_DB_URL", "").strip()
    if not url:
        sys.exit("Falta la variable EVALUADOR_DB_URL con la cadena de conexión de Postgres (Supabase).")
    pg = psycopg.connect(url, autocommit=True, prepare_threshold=None)
    pg.execute("set statement_timeout = 0")  # crear los índices de 8 millones de filas toma varios minutos
    try:
        pg.execute("set maintenance_work_mem = '512MB'")
    except Exception:
        pass
    return pg


RESERVADAS = {"xmin", "xmax", "cmin", "cmax", "ctid", "tableoid", "oid"}  # columnas de sistema en Postgres


def col_pg(nombre):
    n = nombre.lower()
    return n + "_" if n in RESERVADAS else n


def ro(ruta):
    return sqlite3.connect(f"file:{ruta}?mode=ro", uri=True)


# --------------------------------------------------------------------------- registro de cargas
def hecho(pg, clave):
    return pg.execute("select 1 from ev_comun.cargas where clave=%s", (clave,)).fetchone() is not None


def marcar(pg, clave, filas):
    pg.execute("insert into ev_comun.cargas(clave, filas, fecha) values (%s,%s,now()) "
               "on conflict (clave) do update set filas=excluded.filas, fecha=now()", (clave, filas))


def comun(pg):
    pg.execute("create schema if not exists ev_comun")
    pg.execute("create table if not exists ev_comun.cargas(clave text primary key, filas bigint, fecha timestamptz)")
    # caja del recuadro consultado: los elementos que la tocan cumplen max>=a, min<=b, max>=c, min<=d
    pg.execute("""create or replace function ev_comun.ev_caja(a float8, b float8, c float8, d float8) returns box
                  language sql immutable parallel safe as $$ select box(point(a, c), point(b, d)) $$""")
    # mismo recuadro escrito al revés (min<=a, max>=b, min<=c, max>=d)
    pg.execute("""create or replace function ev_comun.ev_caja_inv(a float8, b float8, c float8, d float8) returns box
                  language sql immutable parallel safe as $$ select box(point(b, d), point(a, c)) $$""")
    # igual que _sin_tildes de Python: quita tildes y pasa a mayúsculas
    pg.execute("""create or replace function ev_comun.sin_tildes(s text) returns text language sql immutable parallel safe as
                  $$ select upper(translate(coalesce(s, ''), 'áéíóúàèìòùäëïöüâêîôûãõñçÁÉÍÓÚÀÈÌÒÙÄËÏÖÜÂÊÎÔÛÃÕÑÇ',
                                                         'aeiouaeiouaeiouaeiouaonnAEIOUAEIOUAEIOUAEIOUAONC')) $$""")
    try:
        pg.execute("create extension if not exists btree_gist")
    except Exception as e:  # sin btree_gist el índice espacial no incluye el dataset (funciona, algo más lento)
        log("aviso: sin btree_gist:", e)
    pg.execute("create schema if not exists ev_archivos")
    pg.execute("create table if not exists ev_archivos.archivos(ruta text primary key, datos bytea not null)")


# --------------------------------------------------------------------------- copia genérica SQLite -> Postgres
def tipo_pg(decl):
    d = (decl or "").upper()
    if "INT" in d:
        return "bigint"
    if any(k in d for k in ("REAL", "FLOA", "DOUB", "NUMERIC", "DECIMAL")):
        return "double precision"
    if "BLOB" in d or "GEOMETRY" in d or d in ("POINT", "LINESTRING", "POLYGON", "MULTIPOLYGON", "MULTILINESTRING"):
        return "bytea"
    if d == "":
        return None
    return "text"


def _inferir(src, tabla, col):
    tipos = {r[0] for r in src.execute(f'select distinct typeof("{col}") from (select "{col}" from "{tabla}" limit 2000)')}
    tipos.discard("null")
    if tipos <= {"integer"} and tipos:
        return "bigint"
    if tipos <= {"integer", "real"} and tipos:
        return "double precision"
    if tipos == {"blob"}:
        return "bytea"
    return "text"


def _conv(tipo):
    if tipo == "bigint":
        def f(v):
            if v is None or isinstance(v, int):
                return v
            try:
                return int(float(v))
            except (TypeError, ValueError):
                return None
    elif tipo == "double precision":
        def f(v):
            if v is None or isinstance(v, float):
                return v
            try:
                return float(v)
            except (TypeError, ValueError):
                return None
    elif tipo == "bytea":
        def f(v):
            return v.encode() if isinstance(v, str) else v
    else:
        def f(v):
            if v is None or isinstance(v, str):
                return v if v is None or "\x00" not in v else v.replace("\x00", "")
            if isinstance(v, bytes):
                return v.decode("utf-8", "replace").replace("\x00", "")
            return str(v)
    return f


def copiar_tabla(pg, src, esquema, tabla, destino=None, columnas=None, where="", pk=None, rtree=None,
                 compresion=False, lote=20000):
    """Copia una tabla (o una tabla R-tree como tabla normal) de SQLite a esquema.destino."""
    destino = destino or tabla
    clave = f"{esquema}.{destino}"
    info = list(src.execute(f'pragma table_info("{tabla}")'))
    cols = [(r[1], r[2], r[5]) for r in info if columnas is None or r[1] in columnas]
    defectos = {r[1]: r[4] for r in info}  # «default» de cada columna: el código cuenta con ellos al insertar
    tipos = []
    for nombre, decl, es_pk in cols:
        t = "double precision" if rtree and nombre != cols[0][0] else (tipo_pg(decl) or _inferir(src, tabla, nombre))
        if rtree and nombre == cols[0][0]:
            t = "bigint"
        tipos.append(t)
    pks = [col_pg(c[0]) for c in cols if c[2]] if pk is None else pk
    if rtree:
        pks = [col_pg(cols[0][0])]
    defs = []
    for (nombre, decl, es_pk), t in zip(cols, tipos):
        if len(pks) == 1 and nombre.lower() == pks[0] and t == "bigint" and (decl or "").upper().startswith("INTEGER"):
            defs.append(f'"{col_pg(nombre)}" bigint generated by default as identity')
        else:
            defs.append(f'"{col_pg(nombre)}" {t}' + _defecto(defectos.get(nombre), t))
    if pks:
        defs.append("primary key (" + ", ".join(f'"{p}"' for p in pks) + ")")
    pg.execute(f"create schema if not exists {esquema}")
    pg.execute(f'drop table if exists {esquema}."{destino}" cascade')
    pg.execute(f'create table {esquema}."{destino}" ({", ".join(defs)})')
    if compresion:
        pg.execute(f'alter table {esquema}."{destino}" set (toast_tuple_target = 128)')
    convs = [_conv(t) for t in tipos]
    sel = ", ".join(f'"{c[0]}"' for c in cols)
    n, t0 = 0, time.time()
    with pg.cursor() as cur:
        with cur.copy(f'copy {esquema}."{destino}" ({", ".join(chr(34) + col_pg(c[0]) + chr(34) for c in cols)}) from stdin') as cp:
            for fila in src.execute(f'select {sel} from "{tabla}" {where}'):
                cp.write_row([f(v) for f, v in zip(convs, fila)])
                n += 1
                if n % 200000 == 0:
                    log(f"  {clave}: {n:,} filas ({n / (time.time() - t0):,.0f}/s)")
    _secuencia(pg, esquema, destino, cols, pks)
    if rtree:
        c = [col_pg(x[0]) for x in cols]
        pg.execute(f'create index on {esquema}."{destino}" using gist (box(point("{c[1]}","{c[3]}"),point("{c[2]}","{c[4]}")))')
    log(f"  {clave}: {n:,} filas")
    return n


def _defecto(valor, tipo):
    """DEFAULT de SQLite en Postgres: números y textos entre comillas tal cual; CURRENT_TIMESTAMP como texto ISO."""
    if valor is None:
        return ""
    v = str(valor).strip()
    if v.upper() in ("CURRENT_TIMESTAMP", "(DATETIME('NOW'))", "DATETIME('NOW')"):
        return " default to_char(now(), 'YYYY-MM-DD HH24:MI:SS')" if tipo == "text" else ""
    if re.fullmatch(r"\(?-?\d+(\.\d+)?\)?", v) or re.fullmatch(r"'(?:[^']|'')*'", v) or v.upper() == "NULL":
        if tipo == "bytea" and v.startswith("'"):
            return ""
        return f" default {v.strip('()') if v.startswith('(') else v}"
    return ""


def _secuencia(pg, esquema, tabla, cols, pks):
    if len(pks) == 1:
        p = pks[0]
        tiene = pg.execute("select 1 from information_schema.columns where table_schema=%s and table_name=%s and column_name=%s "
                           "and is_identity='YES'", (esquema, tabla, p)).fetchone()
        if tiene:
            pg.execute(f"""select setval(pg_get_serial_sequence('{esquema}."{tabla}"', '{p}'),
                           greatest(coalesce((select max("{p}") from {esquema}."{tabla}"), 0), 1))""")


def copiar_indices(pg, src, esquema, tablas):
    """Recrea en Postgres los índices normales de SQLite (create index ... on tabla(cols))."""
    for nombre, tabla, sql in src.execute("select name, tbl_name, sql from sqlite_master where type='index' and sql is not null"):
        if tabla not in tablas:
            continue
        m = re.search(r"on\s+[\"']?(\w+)[\"']?\s*\((.+)\)\s*$", sql, re.I | re.S)
        if not m:
            continue
        cols = ", ".join(f'"{col_pg(c.strip().strip(chr(34)).split()[0])}"' + (" desc" if c.strip().lower().endswith(" desc") else "")
                         for c in m.group(2).split(","))
        unico = "unique " if re.match(r"\s*create\s+unique", sql, re.I) else ""
        try:
            pg.execute(f'create {unico}index if not exists "{nombre.lower()}" on {esquema}."{tabla}" ({cols})')
        except Exception as e:
            log(f"  aviso: índice {nombre} no creado: {e}")


def copiar_base(pg, esquema, ruta, solo=None, rehacer=False):
    """Copia todas las tablas normales (y las R-tree como tablas con índice GiST) de una base SQLite."""
    if not os.path.isfile(ruta):
        log(f"  (no existe {ruta})")
        return
    src = ro(ruta)
    virtuales = {}
    for nombre, sql in src.execute("select name, sql from sqlite_master where type='table'"):
        if sql and re.match(r"\s*create\s+virtual\s+table", sql, re.I):
            virtuales[nombre] = sql
    sombras = {f"{v}_{s}" for v in virtuales for s in ("node", "rowid", "parent", "data", "idx", "content", "docsize", "config")}
    tablas = []
    for (nombre,) in src.execute("select name from sqlite_master where type='table' order by name"):
        if nombre.startswith("sqlite_") or nombre in sombras or (solo and nombre not in solo):
            continue
        clave = f"{esquema}.{nombre}"
        if hecho(pg, clave) and not rehacer:
            log(f"  {clave}: ya cargada")
            continue
        sql = virtuales.get(nombre)
        if sql and "using fts" in sql.lower():
            continue  # las tablas de texto completo se cargan aparte (catastro)
        n = copiar_tabla(pg, src, esquema, nombre, rtree=bool(sql and "rtree" in sql.lower()))
        tablas.append(nombre)
        copiar_indices(pg, src, esquema, [nombre])
        pg.execute(f'analyze {esquema}."{nombre}"')
        marcar(pg, clave, n)
    src.close()


# --------------------------------------------------------------------------- partes
def negocio(pg, rehacer):
    for esquema, archivo in NEGOCIO.items():
        log(f"{archivo} -> {esquema}")
        copiar_base(pg, esquema, os.path.join(DATOS, archivo), rehacer=rehacer)
    # adjuntos de soporte (imágenes y archivos de los tickets)
    carpeta = os.path.join(DATOS, "soporte_adjuntos")
    if os.path.isdir(carpeta) and (rehacer or not hecho(pg, "ev_archivos.soporte_adjuntos")):
        n = 0
        for f in os.listdir(carpeta):
            with open(os.path.join(carpeta, f), "rb") as fh:
                pg.execute("insert into ev_archivos.archivos(ruta, datos) values (%s,%s) on conflict (ruta) do update set datos=excluded.datos",
                           (f"soporte_adjuntos/{f}", fh.read()))
            n += 1
        marcar(pg, "ev_archivos.soporte_adjuntos", n)
        log(f"  adjuntos de soporte: {n}")
    # archivos que la herramienta escribe (catálogo de módulos y sus fichas, fuentes, cuenta de correo): en la nube
    # los restaura y guarda motor/bd.py (espejo_archivos) con la carpeta EVALUADOR_DATOS_USUARIO=/app/datos_usuario
    n = 0
    for nombre in ("modulos.json", "fuentes.json", "config_correo.json"):
        ruta = os.path.join(DATOS, nombre)
        if os.path.isfile(ruta):
            with open(ruta, "rb") as fh:
                _guardar(pg, f"espejo/datos_usuario/{nombre}", fh.read())
            n += 1
    fichas = os.path.join(DATOS, "fichas_modulos")
    if os.path.isdir(fichas):
        for nombre in os.listdir(fichas):
            if os.path.isfile(os.path.join(fichas, nombre)):
                with open(os.path.join(fichas, nombre), "rb") as fh:
                    _guardar(pg, f"espejo/fichas_modulos/{nombre}", fh.read())
                n += 1
    log(f"  archivos de configuración y fichas de módulos: {n}")


# archivos pequeños de datos/ que no están en git y el servidor lee del disco (en este orden: la caché del mapa
# PVOUT debe quedar más nueva que el GeoTIFF). En la nube motor/bd.py (restaurar_estaticos) los escribe al arrancar.
ESTATICOS = ["gis/GIS_SOLCOR/rasters/solar/PVOUT.tif", "gis/GIS_SOLCOR/rasters/solar_yearly/PVOUT.tif",
             "gis/GIS_SOLCOR/data/raster_meta.json", "gis/_conteos.json", "cache_pvout.png"]


def estaticos(pg, rehacer):
    n = 0
    for rel in ESTATICOS:
        ruta = os.path.join(DATOS, rel)
        if os.path.isfile(ruta):
            with open(ruta, "rb") as fh:
                _guardar(pg, f"estaticos/{rel}", fh.read())
            n += 1
    marcar(pg, "ev_archivos.estaticos", n)
    log(f"archivos estáticos de datos/: {n}")


def _guardar(pg, ruta, datos):
    pg.execute("insert into ev_archivos.archivos(ruta, datos) values (%s,%s) on conflict (ruta) do update set datos=excluded.datos",
               (ruta, datos))


def gis(pg, rehacer):
    """Las cuatro bases de capas en una sola tabla ev_gis.features, ya sin los duplicados que filtra motor/gis.py."""
    clave = "ev_gis.features"
    if hecho(pg, clave) and not rehacer:
        log(f"{clave}: ya cargada")
        return
    esquema = "ev_gis"
    pg.execute(f"create schema if not exists {esquema}")
    pg.execute(f"drop table if exists {esquema}.features")
    pg.execute(f"""create table {esquema}.features(
        id bigint, origen text not null, dataset text, layer text, name text, attrs_json text, geom_type text,
        geom_json text, xmin_ double precision, ymin double precision, xmax_ double precision, ymax double precision)""")
    pg.execute(f"alter table {esquema}.features set (toast_tuple_target = 128)")
    try:
        pg.execute(f"alter table {esquema}.features alter column geom_json set compression lz4")
        pg.execute(f"alter table {esquema}.features alter column attrs_json set compression lz4")
    except Exception:
        pass  # Postgres sin lz4: queda pglz
    rc = dict(CAPAS_FEATURES)["rc"]
    dup_el, dup_capa = set(), set()
    if os.path.isfile(rc):
        s = ro(rc)
        if s.execute("select 1 from sqlite_master where name='duplicados'").fetchone():
            dup_el = {r[0] for r in s.execute("select fid from duplicados where tipo='elemento'")}
            dup_capa = {r[0] for r in s.execute("select layer from duplicados where tipo='capa'")}
        s.close()
    cc = dict(CAPAS_FEATURES)["cc"]
    reemplazadas = set()
    if os.path.isfile(cc):
        s = ro(cc)
        reemplazadas = {ds for ds in REEMPLAZADAS_POR_COMPLEMENTO
                        if s.execute("select 1 from features where dataset=? limit 1", (ds,)).fetchone()}
        s.close()
    cols = "id, dataset, layer, name, attrs_json, geom_type, geom_json, xmin, ymin, xmax, ymax"
    cols_pg = cols.replace("xmin", "xmin_").replace("xmax", "xmax_")
    total = 0
    for origen, ruta in CAPAS_FEATURES:
        if not os.path.isfile(ruta):
            continue
        log(f"{os.path.basename(ruta)} -> {esquema}.features ({origen})")
        src = ro(ruta)
        n = omit = 0
        t0 = time.time()
        with pg.cursor() as cur:
            with cur.copy(f"copy {esquema}.features (origen, {cols_pg}) from stdin") as cp:
                for fila in src.execute(f"select {cols} from features"):
                    if origen == "main" and (fila[0] in dup_el or fila[2] in dup_capa or fila[1] in reemplazadas):
                        omit += 1
                        continue
                    cp.write_row((origen,) + tuple(fila))
                    n += 1
                    if n % 250000 == 0:
                        log(f"  {n:,} elementos ({n / (time.time() - t0):,.0f}/s)")
        src.close()
        log(f"  {n:,} elementos cargados, {omit:,} duplicados omitidos")
        total += n
    log("  índices de ev_gis.features …")
    try:
        pg.execute(f"create index features_caja on {esquema}.features using gist (dataset, box(point(xmin_,ymin),point(xmax_,ymax)))")
    except Exception:
        pg.execute(f"create index features_caja on {esquema}.features using gist (box(point(xmin_,ymin),point(xmax_,ymax)))")
    pg.execute(f"create index features_capa on {esquema}.features (dataset, layer)")
    pg.execute(f"create index features_mun on {esquema}.features ((attrs_json::jsonb->>'codigo')) where dataset='MUNICIPIO'")
    pg.execute(f"analyze {esquema}.features")
    marcar(pg, clave, total)


def _capa_shp(pg, clave, base):
    """Un shapefile en ev_shp: cabecera del DBF en ev_shp.capas y cada registro (geometría y atributos en
    bytes tal como están en el .shp/.dbf) en ev_shp.registros, con su recuadro para el índice GiST."""
    if hecho(pg, f"ev_shp.{clave}"):
        log(f"  ev_shp.{clave}: ya cargada")
        return
    with open(base + ".dbf", "rb") as f:
        h = f.read(32)
        n, hl, rl = struct.unpack("<IHH", h[4:12])
        cab = h + f.read(hl - 32)
    cpg = base + ".cpg"
    enc = open(cpg).read().strip() if os.path.isfile(cpg) else "latin1"
    prj = open(base + ".prj").read() if os.path.isfile(base + ".prj") else ""
    pg.execute("delete from ev_shp.registros where capa=%s", (clave,))
    pg.execute("insert into ev_shp.capas(capa, n, dbf_cabecera, cpg, prj) values (%s,%s,%s,%s,%s) "
               "on conflict (capa) do update set n=excluded.n, dbf_cabecera=excluded.dbf_cabecera, cpg=excluded.cpg, prj=excluded.prj",
               (clave, n, cab, enc, prj))
    with open(base + ".shx", "rb") as fx:
        fx.seek(100)
        shx = fx.read()
    total = len(shx) // 8
    k = 0
    t0 = time.time()
    with open(base + ".shp", "rb", buffering=1 << 20) as fs, open(base + ".dbf", "rb", buffering=1 << 20) as fd, \
            pg.cursor() as cur, cur.copy("copy ev_shp.registros (capa, i, x0, y0, x1, y1, shp, dbf) from stdin") as cp:
        fd.seek(hl)
        for i in range(total):
            off, largo = struct.unpack_from(">ii", shx, 8 * i)
            fs.seek(off * 2 + 8)
            c = fs.read(largo * 2)
            d = fd.read(rl) if i < n else b""
            tipo = struct.unpack_from("<i", c)[0] if len(c) >= 4 else 0
            if tipo == 0:
                caja = (None, None, None, None)
            elif tipo in (1, 11, 21):
                x, y = struct.unpack_from("<2d", c, 4)
                caja = (x, y, x, y)
            else:
                x0, y0, x1, y1 = struct.unpack_from("<4d", c, 4)
                caja = (x0, y0, x1, y1)
            cp.write_row((clave, i) + caja + (c, d))
            k += 1
            if k % 200000 == 0:
                log(f"  ev_shp.{clave}: {k:,}/{total:,} ({k / (time.time() - t0):,.0f}/s)")
    marcar(pg, f"ev_shp.{clave}", k)
    log(f"  ev_shp.{clave}: {k:,} registros")


def shp(pg, rehacer):
    pg.execute("create schema if not exists ev_shp")
    if rehacer:
        pg.execute("drop table if exists ev_shp.registros, ev_shp.capas")
        pg.execute("delete from ev_comun.cargas where clave like 'ev_shp.%%'")
    pg.execute("create table if not exists ev_shp.capas(capa text primary key, n bigint, dbf_cabecera bytea, cpg text, prj text)")
    pg.execute("""create table if not exists ev_shp.registros(capa text not null, i integer not null,
                  x0 double precision, y0 double precision, x1 double precision, y1 double precision,
                  shp bytea, dbf bytea, primary key (capa, i))""")
    capas = [(os.path.relpath(os.path.join(CAT_DIR, c), DATOS).replace("\\", "/"), os.path.join(CAT_DIR, c)) for c in SHP_CATASTRO]
    if os.path.isdir(SHP_HIDRO):
        for f in sorted(os.listdir(SHP_HIDRO)):
            if f.endswith(".shp"):
                b = os.path.join(SHP_HIDRO, f[:-4])
                capas.append((os.path.relpath(b, DATOS).replace("\\", "/"), b))
    for clave, base in capas:
        if os.path.isfile(base + ".shp"):
            log(f"{clave}.shp -> ev_shp")
            _capa_shp(pg, clave, base)
    try:
        pg.execute("create index if not exists registros_caja on ev_shp.registros using gist (capa, box(point(x0,y0),point(x1,y1)))")
    except Exception:  # sin btree_gist
        pg.execute("create index if not exists registros_caja on ev_shp.registros using gist (box(point(x0,y0),point(x1,y1)))")
    pg.execute("analyze ev_shp.registros")


def hidro(pg, rehacer):
    """Tablas del GeoPackage de hidrografía que usa motor/hidrografia.py, con sus índices R-tree."""
    if not os.path.isfile(GPKG):
        return
    sys.path.insert(0, os.path.join(BASE, "motor"))
    import hidrografia
    fuentes = sorted({v[4] for v in hidrografia.CAPAS.values() if v[3] == "gpkg"})
    src = ro(GPKG)
    log(f"Hidrografia_completa.gpkg -> ev_hidro ({', '.join(fuentes)})")
    for f in fuentes:
        for tabla, rt in ((f, False), (f"rtree_{f}_SHAPE", True)):
            clave = f"ev_hidro.{tabla}"
            if hecho(pg, clave) and not rehacer:
                log(f"  {clave}: ya cargada")
                continue
            n = copiar_tabla(pg, src, "ev_hidro", tabla, rtree=rt, compresion=not rt)
            if not rt:
                pg.execute(f'create index if not exists "{f.lower()}_oid" on ev_hidro."{f}" (objectid)')
            marcar(pg, clave, n)
    src.close()


def catastro(pg, rehacer):
    log("catastro_codigos.sqlite -> ev_cat_codigos")
    copiar_base(pg, "ev_cat_codigos", os.path.join(CAT_DIR, "catastro_codigos.sqlite"), rehacer=rehacer)
    ruta = os.path.join(CAT_DIR, "catastro_busqueda.sqlite")
    clave = "ev_cat_busqueda.nombres"
    if os.path.isfile(ruta) and (rehacer or not hecho(pg, clave)):
        log("catastro_busqueda.sqlite -> ev_cat_busqueda.nombres (texto completo)")
        src = ro(ruta)
        pg.execute("create schema if not exists ev_cat_busqueda")
        pg.execute("drop table if exists ev_cat_busqueda.nombres")
        pg.execute("create table ev_cat_busqueda.nombres(texto text, terreno text, mun text, tipo text)")
        n = 0
        with pg.cursor() as cur, cur.copy("copy ev_cat_busqueda.nombres from stdin") as cp:
            for fila in src.execute("select texto, terreno, mun, tipo from nombres"):
                cp.write_row([None if v is None else str(v) for v in fila])
                n += 1
        pg.execute("create index nombres_fts on ev_cat_busqueda.nombres using gin (to_tsvector('simple', texto))")
        pg.execute("analyze ev_cat_busqueda.nombres")
        marcar(pg, clave, n)
        log(f"  {n:,} nombres")
    cob = os.path.join(CAT_DIR, "catastro_cobertura.json")
    if os.path.isfile(cob):
        with open(cob, "rb") as f:
            pg.execute("insert into ev_archivos.archivos(ruta, datos) values (%s,%s) on conflict (ruta) do update set datos=excluded.datos",
                       ("catastro_cobertura.json", f.read()))


def acc(pg, rehacer):
    for esquema, archivo in ACC.items():
        log(f"{archivo} -> {esquema}")
        copiar_base(pg, esquema, os.path.join(DATOS, archivo), rehacer=rehacer)


def raster(pg, rehacer):
    """Pendientes (.npy float32) fila por fila comprimida, para leer solo la ventana del predio."""
    import ast
    meta_p = os.path.join(GIS_DIR, "data", "raster_meta.json")
    if not os.path.isfile(meta_p):
        return
    pg.execute("create schema if not exists ev_raster")
    pg.execute("create table if not exists ev_raster.zonas(zona text primary key, geo jsonb, w integer, h integer)")
    pg.execute("create table if not exists ev_raster.filas(zona text, r integer, datos bytea, primary key (zona, r))")
    meta = json.load(open(meta_p, encoding="utf-8"))
    for z, geo in meta.items():
        clave = f"ev_raster.{z}"
        p = os.path.join(GIS_DIR, "rasters", z, "slope_pct.npy")
        if not os.path.isfile(p) or (hecho(pg, clave) and not rehacer):
            continue
        with open(p, "rb") as f:
            f.read(6)
            major = f.read(2)[0]
            hlen = struct.unpack("<H" if major == 1 else "<I", f.read(2 if major == 1 else 4))[0]
            hdr = ast.literal_eval(f.read(hlen).decode("latin1"))
            h, w = hdr["shape"]
            pg.execute("delete from ev_raster.filas where zona=%s", (z,))
            pg.execute("insert into ev_raster.zonas values (%s,%s,%s,%s) on conflict (zona) do update set geo=excluded.geo, w=excluded.w, h=excluded.h",
                       (z, json.dumps(geo), w, h))
            log(f"{z}/slope_pct.npy -> ev_raster ({w}x{h})")
            # pendiente en centésimas de % como entero de 16 bits (65535 = sin dato): la mitad de bytes y comprime
            # mucho mejor que float32; el lector (motor/gis.py, _NpyNube) la devuelve en %
            fmt = struct.Struct(f"<{w}f")
            with pg.cursor() as cur, cur.copy("copy ev_raster.filas (zona, r, datos) from stdin") as cp:
                for r in range(h):
                    vals = fmt.unpack(f.read(4 * w))
                    q = array.array("H", (65535 if v != v else max(0, min(65534, int(round(v * 100)))) for v in vals))
                    if sys.byteorder != "little":
                        q.byteswap()
                    cp.write_row((z, r, zlib.compress(q.tobytes(), 6)))
        marcar(pg, clave, h)


def estado(pg):
    for clave, filas, fecha in pg.execute("select clave, filas, fecha from ev_comun.cargas order by clave"):
        print(f"{clave:55s} {filas:>12,}  {fecha:%Y-%m-%d %H:%M}")
    print()
    for esquema, tam in pg.execute("""select n.nspname, sum(pg_total_relation_size(c.oid)) from pg_class c
            join pg_namespace n on n.oid=c.relnamespace where n.nspname like 'ev\\_%%' and c.relkind in ('r','m')
            group by 1 order by 2 desc"""):
        print(f"{esquema:30s} {tam / 2**30:8.2f} GB")
    print(f"{'base completa':30s} {pg.execute('select pg_database_size(current_database())').fetchone()[0] / 2**30:8.2f} GB")


PARTES = dict(estaticos=estaticos, negocio=negocio, gis=gis, shp=shp, hidro=hidro, catastro=catastro, acc=acc, raster=raster)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--solo", help="partes separadas por coma: " + ", ".join(PARTES))
    ap.add_argument("--rehacer", help="partes que se vuelven a cargar aunque ya estén")
    ap.add_argument("--estado", action="store_true")
    a = ap.parse_args()
    pg = conectar_pg()
    comun(pg)
    if a.estado:
        return estado(pg)
    rehacer = set((a.rehacer or "").split(",")) - {""}
    partes = (a.solo or a.rehacer or ",".join(PARTES)).split(",")
    t0 = time.time()
    for p in partes:
        PARTES[p.strip()](pg, p.strip() in rehacer)
    log(f"listo en {(time.time() - t0) / 60:,.1f} min")
    estado(pg)


if __name__ == "__main__":
    main()
