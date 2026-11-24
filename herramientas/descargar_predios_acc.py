"""
Descarga los predios (terrenos) de los municipios de Cundinamarca cuyo gestor catastral es la Agencia Catastral de
Cundinamarca (ACC), desde su geoportal público https://www.acc.gov.co/geoportal/v1 (sin ingreso ni captcha).

El API del geoportal (backtransacts.acc.gov.co/layers/land/?bbox=…) entrega los predios de un recuadro en GeoJSON
WGS84 y responde «reach_limit» si el recuadro tiene demasiados: ese recuadro se parte en cuatro. Para no cargar el
servicio: una petición a la vez, pausa entre peticiones (--pausa, 2 s por defecto), se detiene tras 3 fallos
seguidos, y cada recuadro terminado queda anotado en la base: se puede interrumpir y reanudar.

Resultado: datos/catastro_acc.sqlite (tabla predios con índice espacial), que lee motor/catastro_acc.py.

    python herramientas/descargar_predios_acc.py                 # los 76 municipios de la ACC
    python herramientas/descargar_predios_acc.py Sutatausa Tausa # solo esos
    python herramientas/descargar_predios_acc.py --lista          # municipios y avance
    python herramientas/descargar_predios_acc.py --indexar        # solo llena la columna del buscador
"""
import json
import os
import sqlite3
import sys
import time
import unicodedata
import urllib.error
import urllib.request
import zlib

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "motor"))
import gis  # noqa: E402

API = "https://backtransacts.acc.gov.co"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) EvaluadorGD-Solcor/2.2 (consulta de predios para evaluacion de proyectos, descarga lenta)"
DB = os.path.join(BASE, "datos", "catastro_acc.sqlite")
CELDA = 0.02          # ~2,2 km; la que llega llena se parte en cuatro
MIN_CELDA = 0.0012    # ~130 m: por debajo de esto no se parte más (se anota como incompleta)
DEPTO = "25"


class Detenido(Exception):
    pass


def _db():
    c = sqlite3.connect(DB, timeout=60)
    c.executescript("""
        create table if not exists predios (id integer primary key, clave text unique, municipio text, clase text,
            attrs text, geom blob, xmin real, ymin real, xmax real, ymax real, descargado text);
        create virtual table if not exists predios_idx using rtree(id, xmin, xmax, ymin, ymax);
        create table if not exists celdas (clave text primary key, municipio text, n integer, estado text, fecha text);
        create table if not exists municipios (codigo text primary key, nombre text, estado text, predios integer, fecha text);
    """)
    if "nombre" not in [r[1] for r in c.execute("pragma table_info(predios)")]:
        c.execute("alter table predios add column nombre text")  # nombre o dirección en mayúsculas y sin tildes (buscador)
    return c


def indexar(db):
    """Llena la columna nombre de los predios que no la tengan."""
    filas = db.execute("select id, json_extract(attrs, '$.direccion') from predios where nombre is null").fetchall()
    db.executemany("update predios set nombre=? where id=?", [(_sin_tildes(d).upper(), i) for i, d in filas])
    db.commit()
    print(f"{len(filas)} nombres indexados", flush=True)


def _sin_tildes(s):
    return "".join(ch for ch in unicodedata.normalize("NFD", s or "") if unicodedata.category(ch) != "Mn").lower().strip()


def pedir(url, pausa, est):
    espera = 30
    while True:
        dt = time.time() - est["ultima"]
        if dt < pausa:
            time.sleep(pausa - dt)
        est["ultima"] = time.time()
        est["pedidas"] += 1
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"}), timeout=120) as r:
                b = r.read()
            est["bytes"] += len(b)
            est["fallos"] = 0
            return json.loads(b)
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, ValueError) as e:
            est["fallos"] += 1
            if est["fallos"] >= 3:
                raise Detenido(f"3 fallos seguidos ({str(e)[:80]}); se detiene para no forzar el servicio. Reintentar más tarde.")
            print(f"    aviso: {str(e)[:80]}; esperando {espera} s", flush=True)
            time.sleep(espera)
            espera *= 2


def municipios_acc(est, pausa):
    d = pedir(f"{API}/towns-jurisdiction/list", pausa, est)
    return [(DEPTO + m["code"], m["name"]) for m in d["data"]]


def _poligono_municipio(codigo):
    fila = gis._db().execute("select geom_type, geom_json, xmin, ymin, xmax, ymax from features "
                             "where dataset='MUNICIPIO' and json_extract(attrs_json, '$.codigo')=?", (codigo,)).fetchone()
    if not fila:
        return None, None
    g = json.loads(fila[1])
    anillos = [g[0]] if fila[0] == "Polygon" else [p[0] for p in g]
    return anillos, fila[2:]


def _toca(b, anillos):
    """¿El recuadro b toca el municipio? (esquinas o centro dentro, o algún vértice del límite dentro del recuadro)."""
    pts = [(b[0], b[1]), (b[2], b[1]), (b[0], b[3]), (b[2], b[3]), ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)]
    for a in anillos:
        if any(gis._pir(x, y, a) for x, y in pts):
            return True
        if any(b[0] <= p[0] <= b[2] and b[1] <= p[1] <= b[3] for p in a):
            return True
    return False


def _redondear(c):
    if not c:
        return c
    if isinstance(c[0], (int, float)):
        return [round(c[0], 7), round(c[1], 7)]
    return [_redondear(x) for x in c]


def _guardar(db, feats, hoy):
    n = 0
    for f in feats:
        p = f.get("properties") or {}
        g = f.get("geometry") or {}
        if g.get("type") not in ("Polygon", "MultiPolygon"):
            continue
        clave = p.get("numero_predial") or p.get("numero_predial_anterior") or p.get("nupre")
        if not clave:
            continue
        coords = _redondear(g["coordinates"])
        polys = [coords] if g["type"] == "Polygon" else coords
        # el geoportal trae a veces anillos vacíos o de menos de 4 vértices: se descartan
        polys = [[r for r in poly if len(r) >= 4] for poly in polys if poly and len(poly[0]) >= 4]
        if not polys:
            continue
        xs = [pt[0] for poly in polys for pt in poly[0]]
        ys = [pt[1] for poly in polys for pt in poly[0]]
        mun = DEPTO + str(p.get("municipio") or "").zfill(3)
        cur = db.execute("insert into predios(clave, municipio, clase, attrs, geom, xmin, ymin, xmax, ymax, descargado, nombre) "
                         "values (?,?,?,?,?,?,?,?,?,?,?) on conflict(clave) do update set attrs=excluded.attrs, geom=excluded.geom, "
                         "xmin=excluded.xmin, ymin=excluded.ymin, xmax=excluded.xmax, ymax=excluded.ymax, descargado=excluded.descargado, "
                         "nombre=excluded.nombre returning id",
                         (str(clave), mun, p.get("clase_suelo"), json.dumps(p, ensure_ascii=False),
                          zlib.compress(json.dumps(polys, separators=(",", ":")).encode(), 6),
                          min(xs), min(ys), max(xs), max(ys), hoy, _sin_tildes(p.get("direccion")).upper()))
        pid = cur.fetchone()[0]
        db.execute("insert or replace into predios_idx values (?,?,?,?,?)", (pid, min(xs), max(xs), min(ys), max(ys)))
        n += 1
    return n


def recorrer(db, codigo, nombre, pausa, est):
    anillos, bb = _poligono_municipio(codigo)
    if not anillos:
        print(f"  {nombre} ({codigo}): sin polígono en la capa de municipios, se omite", flush=True)
        return
    hoy = time.strftime("%Y-%m-%d")
    hechas = {r[0] for r in db.execute("select clave from celdas where municipio=?", (codigo,))}
    pend = []
    x = bb[0]
    while x < bb[2]:
        y = bb[1]
        while y < bb[3]:
            pend.append((round(x, 6), round(y, 6), round(min(x + CELDA, bb[2]), 6), round(min(y + CELDA, bb[3]), 6)))
            y += CELDA
        x += CELDA
    pend = [b for b in pend if _toca(b, anillos)]
    print(f"  {nombre} ({codigo}): {len(pend)} recuadros iniciales", flush=True)
    t0, nuevas = time.time(), 0
    while pend:
        b = pend.pop()
        clave = ",".join(f"{v:.6f}" for v in b)
        if clave in hechas:
            continue
        d = pedir(f"{API}/layers/land/?bbox={clave}&screen=1280,720&zoom=16", pausa, est)
        if not d.get("success", True) and d.get("message") == "reach_limit":
            if b[2] - b[0] > MIN_CELDA:
                mx, my = round((b[0] + b[2]) / 2, 6), round((b[1] + b[3]) / 2, 6)
                pend += [q for q in [(b[0], b[1], mx, my), (mx, b[1], b[2], my), (b[0], my, mx, b[3]), (mx, my, b[2], b[3])]
                         if _toca(q, anillos)]
                estado, n = "partida", 0
            else:
                estado, n = "incompleta", 0
        elif isinstance(d.get("data"), dict):
            n = _guardar(db, d["data"].get("features") or [], hoy)
            estado = "ok"
        else:
            estado, n = "sin_datos:" + str(d.get("message"))[:40], 0
        db.execute("insert or replace into celdas values (?,?,?,?,?)", (clave, codigo, n, estado, hoy))
        db.commit()
        nuevas += 1
        if nuevas % 20 == 0:
            tot = db.execute("select count(*) from predios where municipio=?", (codigo,)).fetchone()[0]
            print(f"    {nombre}: {nuevas} recuadros, {len(pend)} por revisar, {tot} predios, "
                  f"{est['bytes'] / 1e6:,.0f} MB, {time.time() - t0:,.0f} s", flush=True)
    tot = db.execute("select count(*) from predios where municipio=?", (codigo,)).fetchone()[0]
    inc = db.execute("select count(*) from celdas where municipio=? and estado='incompleta'", (codigo,)).fetchone()[0]
    db.execute("insert or replace into municipios values (?,?,?,?,?)", (codigo, nombre, "completo" if not inc else f"{inc} recuadros incompletos", tot, hoy))
    db.commit()
    print(f"  {nombre}: {tot} predios ({nuevas} recuadros nuevos)", flush=True)


def main():
    pausa = float(sys.argv[sys.argv.index("--pausa") + 1]) if "--pausa" in sys.argv else 2.0
    est = dict(ultima=0.0, pedidas=0, fallos=0, bytes=0)
    db = _db()
    if "--indexar" in sys.argv:
        indexar(db)
        return
    try:
        muns = municipios_acc(est, pausa)
        if "--lista" in sys.argv:
            hechos = {r[0]: r for r in db.execute("select codigo, nombre, estado, predios from municipios")}
            for cod, nom in muns:
                h = hechos.get(cod)
                print(f"{cod} {nom}: " + (f"{h[3]} predios, {h[2]}" if h else "pendiente"))
            return
        pedidos = [_sin_tildes(a) for a in sys.argv[1:] if not a.startswith("--") and not a.replace(".", "").isdigit()]
        if pedidos:
            muns = [m for m in muns if _sin_tildes(m[1]) in pedidos]
        hechos = {r[0] for r in db.execute("select codigo from municipios where estado='completo'")}
        for cod, nom in muns:
            if cod in hechos and not pedidos:
                continue
            recorrer(db, cod, nom, pausa, est)
        indexar(db)
    except Detenido as e:
        print("DETENIDO:", e)
        sys.exit(2)
    finally:
        print(f"{est['pedidas']} peticiones, {est['bytes'] / 1e6:,.0f} MB descargados", flush=True)
        db.close()


if __name__ == "__main__":
    main()
