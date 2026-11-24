"""
Descarga los predios rurales de los 113 municipios de Antioquia cuyo gestor catastral es la Gerencia de Catastro
del departamento, desde sus servicios ArcGIS públicos (un MapServer por municipio, capa «Predios_Rurales»):
    https://geoags1.catastroantioquia-mas.com/arcgis/rest/services/Regiones_M/M<código>/MapServer

Se piden páginas de 1.000 predios (resultOffset) en WGS84, con la geometría simplificada a ~0,3 m. Para no cargar
el servicio: una petición a la vez, pausa entre peticiones (--pausa, 2 s por defecto), se detiene tras 3 fallos
seguidos, y cada página terminada queda anotada: se puede interrumpir y reanudar.

Resultado: datos/catastro_antioquia.sqlite, con el mismo formato que datos/catastro_acc.sqlite; lo lee
motor/catastro_acc.py como otra fuente complementaria del catastro.

    python herramientas/descargar_predios_antioquia.py               # los 113 municipios
    python herramientas/descargar_predios_antioquia.py Sonsón Urrao  # solo esos
    python herramientas/descargar_predios_antioquia.py --lista
"""
import json
import os
import sqlite3
import sys
import time
import urllib.parse
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import descargar_predios_acc as acc  # noqa: E402  (pedir, _redondear, _sin_tildes, gis)

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = "https://geoags1.catastroantioquia-mas.com/arcgis/rest/services/Regiones_M"
DB = os.path.join(BASE, "datos", "catastro_antioquia.sqlite")
PAGINA = 1000
DEPTO = "05"


def _db():
    c = sqlite3.connect(DB, timeout=60)
    c.executescript("""
        create table if not exists predios (id integer primary key, clave text unique, municipio text, clase text,
            attrs text, geom blob, xmin real, ymin real, xmax real, ymax real, descargado text, nombre text);
        create virtual table if not exists predios_idx using rtree(id, xmin, xmax, ymin, ymax);
        create table if not exists paginas (municipio text, desde integer, n integer, fecha text, primary key (municipio, desde));
        create table if not exists municipios (codigo text primary key, nombre text, estado text, predios integer, fecha text);
    """)
    return c


def _nombre(codigo):
    fila = acc.gis._db().execute("select name from features where dataset='MUNICIPIO' and json_extract(attrs_json, '$.codigo')=?",
                                 (codigo,)).fetchone()
    return fila[0] if fila else codigo


def _poligonos_esri(rings):
    """Anillos Esri -> [[exterior, huecos…], …]. En Esri el exterior va en sentido horario y los huecos al revés."""
    polys = []
    for r in rings:
        if len(r) < 4:
            continue
        horario = sum(r[i][0] * r[i + 1][1] - r[i + 1][0] * r[i][1] for i in range(len(r) - 1)) < 0
        if horario or not polys:
            polys.append([r])
        else:
            polys[-1].append(r)
    return polys


def _guardar(db, codigo, feats, hoy):
    n = 0
    for f in feats:
        # Esri JSON (f=json): el servidor falla al convertir a GeoJSON algunas geometrías
        p = f.get("attributes") or {}
        g = f.get("geometry") or {}
        clave = (p.get("TERRENO_CODIGO") or "").strip()
        if not g.get("rings") or not clave:
            continue
        polys = _poligonos_esri(acc._redondear(g["rings"]))
        if not polys:
            continue
        xs = [pt[0] for poly in polys for pt in poly[0]]
        ys = [pt[1] for poly in polys for pt in poly[0]]
        attrs = {"numero_predial": clave, "area": p.get("SHAPE.AREA"), "local_id": p.get("LOCAL_ID"), "etiqueta": p.get("ETIQUETA")}
        cur = db.execute("insert into predios(clave, municipio, clase, attrs, geom, xmin, ymin, xmax, ymax, descargado, nombre) "
                         "values (?,?,?,?,?,?,?,?,?,?,?) on conflict(clave) do update set attrs=excluded.attrs, geom=excluded.geom, "
                         "xmin=excluded.xmin, ymin=excluded.ymin, xmax=excluded.xmax, ymax=excluded.ymax, descargado=excluded.descargado "
                         "returning id",
                         (clave, codigo, "Rural", json.dumps(attrs, ensure_ascii=False),
                          zlib.compress(json.dumps(polys, separators=(",", ":")).encode(), 6),
                          min(xs), min(ys), max(xs), max(ys), hoy, ""))
        pid = cur.fetchone()[0]
        db.execute("insert or replace into predios_idx values (?,?,?,?,?)", (pid, min(xs), max(xs), min(ys), max(ys)))
        n += 1
    return n


def recorrer(db, servicio, pausa, est):
    codigo = DEPTO + servicio[1:]
    nombre = _nombre(codigo)
    hoy = time.strftime("%Y-%m-%d")
    meta = acc.pedir(f"{API}/{servicio}/MapServer?f=json", pausa, est)
    capa = next((l["id"] for l in meta.get("layers", []) if l["name"].lower() == "predios_rurales"), None)
    if capa is None:
        print(f"  {nombre} ({codigo}): sin capa Predios_Rurales, se omite", flush=True)
        return
    url = f"{API}/{servicio}/MapServer/{capa}/query?"
    total = acc.pedir(url + "where=1%3D1&returnCountOnly=true&f=json", pausa, est).get("count", 0)
    hechas = {r[0] for r in db.execute("select desde from paginas where municipio=? and n > 0", (codigo,))}
    print(f"  {nombre} ({codigo}): {total} predios rurales", flush=True)
    t0 = time.time()
    for desde in range(0, total, PAGINA):
        if desde in hechas:
            continue
        q = urllib.parse.urlencode(dict(where="1=1", outFields="TERRENO_CODIGO,ETIQUETA,LOCAL_ID,SHAPE.AREA", outSR=4326,
                                        orderByFields="OBJECTID", resultOffset=desde, resultRecordCount=PAGINA,
                                        maxAllowableOffset="0.000003", geometryPrecision=7, f="json"))
        d = acc.pedir(url + q, pausa, est)
        if "error" in d:
            print(f"    {nombre}: la página desde {desde} respondió error ({str(d['error'].get('message'))[:60]}); se reintenta en otra corrida", flush=True)
            continue
        n = _guardar(db, codigo, d.get("features") or [], hoy)
        db.execute("insert or replace into paginas values (?,?,?,?)", (codigo, desde, n, hoy))
        db.commit()
        print(f"    {nombre}: {min(desde + PAGINA, total)}/{total}, {est['bytes'] / 1e6:,.0f} MB, {time.time() - t0:,.0f} s", flush=True)
    tot = db.execute("select count(*) from predios where municipio=?", (codigo,)).fetchone()[0]
    db.execute("insert or replace into municipios values (?,?,?,?,?)",
               (codigo, nombre, "completo" if tot >= 0.98 * total else f"{tot} de {total}", tot, hoy))
    db.commit()
    print(f"  {nombre}: {tot} predios guardados", flush=True)


def main():
    pausa = float(sys.argv[sys.argv.index("--pausa") + 1]) if "--pausa" in sys.argv else 2.0
    est = dict(ultima=0.0, pedidas=0, fallos=0, bytes=0)
    db = _db()
    try:
        servicios = [s["name"].split("/")[1] for s in acc.pedir(f"{API}?f=json", pausa, est)["services"]]
        if "--lista" in sys.argv:
            hechos = {r[0]: r for r in db.execute("select codigo, nombre, estado, predios from municipios")}
            for s in servicios:
                h = hechos.get(DEPTO + s[1:])
                print(f"{DEPTO + s[1:]} {_nombre(DEPTO + s[1:])}: " + (f"{h[3]} predios, {h[2]}" if h else "pendiente"))
            return
        pedidos = [acc._sin_tildes(a) for a in sys.argv[1:] if not a.startswith("--") and not a.replace(".", "").isdigit()]
        if pedidos:
            servicios = [s for s in servicios if acc._sin_tildes(_nombre(DEPTO + s[1:])) in pedidos]
        hechos = {r[0] for r in db.execute("select codigo from municipios where estado='completo'")}
        for s in servicios:
            if DEPTO + s[1:] in hechos and not pedidos:
                continue
            recorrer(db, s, pausa, est)
    except acc.Detenido as e:
        print("DETENIDO:", e)
        sys.exit(2)
    finally:
        print(f"{est['pedidas']} peticiones, {est['bytes'] / 1e6:,.0f} MB descargados", flush=True)
        db.close()


if __name__ == "__main__":
    main()
