"""
Descarga los terrenos (urbanos y rurales) de Armenia (Quindío), municipio gestor catastral cuyo operador es Catastro
Bogotá, desde el servicio ArcGIS público que este publica (capa LC_Terreno, vigencia 2023):
    https://services1.arcgis.com/J5ltM0ovtzXUbp7B/arcgis/rest/services/CapasCatastroArmenia/FeatureServer/1

Se piden páginas de 2.000 terrenos (resultOffset) en WGS84. Para no cargar el servicio: una petición a la vez, pausa
entre peticiones (--pausa, 2 s por defecto), se detiene tras 3 fallos seguidos, y cada página terminada queda
anotada: se puede interrumpir y reanudar.

Resultado: datos/catastro_armenia.sqlite, con el mismo formato que datos/catastro_acc.sqlite; lo lee
motor/catastro_acc.py como otra fuente complementaria del catastro.

    python herramientas/descargar_predios_armenia.py
"""
import json
import os
import sys
import time
import urllib.parse
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import descargar_predios_acc as acc  # noqa: E402  (pedir, _redondear, Detenido)
import descargar_predios_antioquia as ant  # noqa: E402  (_poligonos_esri)
import descargar_predios_amco as amco  # noqa: E402  (_db con la misma estructura)

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URL = "https://services1.arcgis.com/J5ltM0ovtzXUbp7B/arcgis/rest/services/CapasCatastroArmenia/FeatureServer/1/query?"
DB = os.path.join(BASE, "datos", "catastro_armenia.sqlite")
PAGINA = 2000
MUNICIPIO = ("63001", "Armenia")


def _guardar(db, feats, hoy):
    n = 0
    for f in feats:
        p = f.get("attributes") or {}
        g = f.get("geometry") or {}
        clave = str(p.get("NAME") or "").strip()
        if not g.get("rings") or len(clave) != 30:
            continue
        polys = ant._poligonos_esri(acc._redondear(g["rings"]))
        if not polys:
            continue
        xs = [pt[0] for poly in polys for pt in poly[0]]
        ys = [pt[1] for poly in polys for pt in poly[0]]
        clase = "Rural" if clave[5:7] == "00" else "Urbano"
        attrs = {"numero_predial": clave, "area": p.get("AREA_TERRENO") or round(p.get("Shape__Area") or 0, 1)}
        cur = db.execute("insert into predios(clave, municipio, clase, attrs, geom, xmin, ymin, xmax, ymax, descargado, nombre) "
                         "values (?,?,?,?,?,?,?,?,?,?,?) on conflict(clave) do update set attrs=excluded.attrs, geom=excluded.geom, "
                         "xmin=excluded.xmin, ymin=excluded.ymin, xmax=excluded.xmax, ymax=excluded.ymax, descargado=excluded.descargado "
                         "returning id",
                         (clave, clave[:5], clase, json.dumps(attrs, ensure_ascii=False),
                          zlib.compress(json.dumps(polys, separators=(",", ":")).encode(), 6),
                          min(xs), min(ys), max(xs), max(ys), hoy, ""))
        pid = cur.fetchone()[0]
        db.execute("insert or replace into predios_idx values (?,?,?,?,?)", (pid, min(xs), max(xs), min(ys), max(ys)))
        n += 1
    return n


def main():
    pausa = float(sys.argv[sys.argv.index("--pausa") + 1]) if "--pausa" in sys.argv else 2.0
    est = dict(ultima=0.0, pedidas=0, fallos=0, bytes=0)
    amco.DB = DB
    db = amco._db()
    hoy = time.strftime("%Y-%m-%d")
    try:
        total = acc.pedir(URL + "where=1%3D1&returnCountOnly=true&f=json", pausa, est).get("count", 0)
        hechas = {r[0] for r in db.execute("select desde from paginas where municipio=? and n > 0", (MUNICIPIO[0],))}
        print(f"{MUNICIPIO[1]}: {total} terrenos", flush=True)
        t0 = time.time()
        for desde in range(0, total, PAGINA):
            if desde in hechas:
                continue
            q = urllib.parse.urlencode(dict(where="1=1", outFields="NAME,AREA_TERRENO,Shape__Area", outSR=4326,
                                            orderByFields="OBJECTID", resultOffset=desde, resultRecordCount=PAGINA,
                                            geometryPrecision=7, f="json"))
            d = acc.pedir(URL + q, pausa, est)
            if "error" in d:
                print(f"  la página desde {desde} respondió error ({str(d['error'].get('message'))[:60]}); se reintenta en otra corrida", flush=True)
                continue
            n = _guardar(db, d.get("features") or [], hoy)
            db.execute("insert or replace into paginas values (?,?,?,?)", (MUNICIPIO[0], desde, n, hoy))
            db.commit()
            print(f"  {min(desde + PAGINA, total)}/{total}, {est['bytes'] / 1e6:,.0f} MB, {time.time() - t0:,.0f} s", flush=True)
        tot = db.execute("select count(*) from predios where municipio=?", (MUNICIPIO[0],)).fetchone()[0]
        db.execute("insert or replace into municipios values (?,?,?,?,?)",
                   (MUNICIPIO[0], MUNICIPIO[1], "completo" if tot >= 0.98 * total else f"{tot} de {total}", tot, hoy))
        db.commit()
        print(f"{MUNICIPIO[1]}: {tot} terrenos guardados", flush=True)
    except acc.Detenido as e:
        print("DETENIDO:", e)
        sys.exit(2)
    finally:
        print(f"{est['pedidas']} peticiones, {est['bytes'] / 1e6:,.0f} MB descargados", flush=True)
        db.close()


if __name__ == "__main__":
    main()
