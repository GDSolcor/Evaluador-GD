"""
Descarga los terrenos (urbanos y rurales) de Pereira, Dosquebradas y La Virginia, cuyo gestor catastral es el Área
Metropolitana Centro Occidente (AMCO), desde el WFS público de su IDE (el mismo que usa el geovisor
https://ideamco.gov.co/geovisor/ideep.php):
    https://geo.ideamco.gov.co:8443/geoserver/ows  capa amco:terreno_amco

Se piden páginas de 2.000 terrenos (startIndex, ordenados por gid) en WGS84. Para no cargar el servicio: una petición
a la vez, pausa entre peticiones (--pausa, 2 s por defecto), se detiene tras 3 fallos seguidos, y cada página
terminada queda anotada: se puede interrumpir y reanudar.

Resultado: datos/catastro_amco.sqlite, con el mismo formato que datos/catastro_acc.sqlite; lo lee
motor/catastro_acc.py como otra fuente complementaria del catastro.

    python herramientas/descargar_predios_amco.py
"""
import json
import math
import os
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import descargar_predios_acc as acc  # noqa: E402  (pedir, _redondear, Detenido)

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WFS = "https://geo.ideamco.gov.co:8443/geoserver/ows"
CAPA = "amco:terreno_amco"
DB = os.path.join(BASE, "datos", "catastro_amco.sqlite")
PAGINA = 2000
MUNICIPIOS = {"66001": "Pereira", "66170": "Dosquebradas", "66400": "La Virginia"}


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


def _area_m2(polys):
    """Área aproximada (equirrectangular local): el WFS no trae el área del terreno."""
    lat0 = math.radians(polys[0][0][0][1])
    kx, ky = 111320.0 * math.cos(lat0), 110574.0
    def anillo(r):
        return abs(sum(r[i][0] * r[i + 1][1] - r[i + 1][0] * r[i][1] for i in range(len(r) - 1))) / 2 * kx * ky
    return sum(anillo(p[0]) - sum(anillo(h) for h in p[1:]) for p in polys)


def _guardar(db, feats, hoy):
    n = 0
    for f in feats:
        p = f.get("properties") or {}
        g = f.get("geometry") or {}
        clave = str(p.get("codigo") or "").strip()
        if not g.get("coordinates") or len(clave) != 30:
            continue
        polys = acc._redondear(g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]])
        polys = [poly for poly in polys if poly and len(poly[0]) >= 4]
        if not polys:
            continue
        xs = [pt[0] for poly in polys for pt in poly[0]]
        ys = [pt[1] for poly in polys for pt in poly[0]]
        # posiciones 6-7 del número predial: 00 rural; 01 en adelante, cabecera o centro poblado
        clase = "Rural" if clave[5:7] == "00" else "Urbano"
        attrs = {"numero_predial": clave, "numero_anterior": p.get("codigo_ant"), "area": round(_area_m2(polys), 1),
                 "barrio": p.get("barrio"), "vereda": p.get("vereda")}
        cur = db.execute("insert into predios(clave, municipio, clase, attrs, geom, xmin, ymin, xmax, ymax, descargado, nombre) "
                         "values (?,?,?,?,?,?,?,?,?,?,?) on conflict(clave) do update set attrs=excluded.attrs, geom=excluded.geom, "
                         "xmin=excluded.xmin, ymin=excluded.ymin, xmax=excluded.xmax, ymax=excluded.ymax, descargado=excluded.descargado "
                         "returning id",
                         (clave, clave[:5], clase, json.dumps({k: v for k, v in attrs.items() if v not in (None, "")}, ensure_ascii=False),
                          zlib.compress(json.dumps(polys, separators=(",", ":")).encode(), 6),
                          min(xs), min(ys), max(xs), max(ys), hoy, ""))
        pid = cur.fetchone()[0]
        db.execute("insert or replace into predios_idx values (?,?,?,?,?)", (pid, min(xs), max(xs), min(ys), max(ys)))
        n += 1
    return n


def main():
    pausa = float(sys.argv[sys.argv.index("--pausa") + 1]) if "--pausa" in sys.argv else 2.0
    est = dict(ultima=0.0, pedidas=0, fallos=0, bytes=0)
    db = _db()
    hoy = time.strftime("%Y-%m-%d")
    base = dict(service="WFS", version="2.0.0", request="GetFeature", typeNames=CAPA)
    try:
        with urllib.request.urlopen(urllib.request.Request(f"{WFS}?{urllib.parse.urlencode({**base, 'resultType': 'hits'})}",
                                                           headers={"User-Agent": acc.UA}), timeout=120) as r:
            total = int(re.search(rb'numberMatched="(\d+)"', r.read()).group(1))
        hechas = {r[0] for r in db.execute("select desde from paginas where municipio='amco' and n > 0")}
        print(f"AMCO: {total} terrenos", flush=True)
        t0 = time.time()
        for desde in range(0, total, PAGINA):
            if desde in hechas:
                continue
            q = urllib.parse.urlencode({**base, "count": PAGINA, "startIndex": desde, "sortBy": "gid",
                                        "outputFormat": "application/json", "srsName": "EPSG:4326"})
            d = acc.pedir(f"{WFS}?{q}", pausa, est)
            n = _guardar(db, d.get("features") or [], hoy)
            db.execute("insert or replace into paginas values (?,?,?,?)", ("amco", desde, n, hoy))
            db.commit()
            print(f"  {min(desde + PAGINA, total)}/{total}, {est['bytes'] / 1e6:,.0f} MB, {time.time() - t0:,.0f} s", flush=True)
        tot = db.execute("select count(*) from predios").fetchone()[0]
        for cod, nom in MUNICIPIOS.items():
            n = db.execute("select count(*) from predios where municipio=?", (cod,)).fetchone()[0]
            db.execute("insert or replace into municipios values (?,?,?,?,?)",
                       (cod, nom, "completo" if tot >= 0.98 * total else f"{tot} de {total} (AMCO)", n, hoy))
        db.commit()
        print(f"AMCO: {tot} terrenos guardados", flush=True)
    except acc.Detenido as e:
        print("DETENIDO:", e)
        sys.exit(2)
    finally:
        print(f"{est['pedidas']} peticiones, {est['bytes'] / 1e6:,.0f} MB descargados", flush=True)
        db.close()


if __name__ == "__main__":
    main()
