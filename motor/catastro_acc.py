"""
Predios de gestores catastrales distintos al IGAC (no están en el catastro público del IGAC), descargados de sus
geoportales públicos a bases SQLite en WGS84 con índice espacial R-tree:

  - datos/catastro_acc.sqlite       Agencia Catastral de Cundinamarca (ACC), 76 municipios
                                    (herramientas/descargar_predios_acc.py). Clave: número predial anterior (20 dígitos).
  - datos/catastro_antioquia.sqlite Gerencia de Catastro de Antioquia, 113 municipios, predios rurales
                                    (herramientas/descargar_predios_antioquia.py). Clave: número predial (30 dígitos).
  - datos/catastro_amco.sqlite      AMCO (Pereira, Dosquebradas, La Virginia), terrenos urbanos y rurales
                                    (herramientas/descargar_predios_amco.py). Clave: número predial (30 dígitos).
  - datos/catastro_armenia.sqlite   Armenia (operador Catastro Bogotá), terrenos urbanos y rurales
                                    (herramientas/descargar_predios_armenia.py). Clave: número predial (30 dígitos).

motor/catastro.py los usa como complemento: capa de predios, predio en un punto, predios del área evaluada, ficha y
búsqueda. Todas las bases tienen la misma tabla predios(id, clave, municipio, clase, attrs, geom, …, nombre).
"""
import json
import os
import sqlite3
import zlib

BASE = os.path.dirname(os.path.abspath(__file__))
DATOS = os.path.join(BASE, "..", "datos")
FUENTES = [
    (os.environ.get("CATASTRO_ACC", os.path.join(DATOS, "catastro_acc.sqlite")),
     "Catastro ACC (Agencia Catastral de Cundinamarca, geoportal)", "Catastro ACC"),
    (os.environ.get("CATASTRO_ANTIOQUIA", os.path.join(DATOS, "catastro_antioquia.sqlite")),
     "Catastro Antioquia (Gerencia de Catastro departamental, servicio ArcGIS)", "Catastro Antioquia"),
    (os.environ.get("CATASTRO_AMCO", os.path.join(DATOS, "catastro_amco.sqlite")),
     "Catastro AMCO (Área Metropolitana Centro Occidente: Pereira, Dosquebradas y La Virginia; WFS de la IDE AMCO)", "Catastro AMCO"),
    (os.environ.get("CATASTRO_ARMENIA", os.path.join(DATOS, "catastro_armenia.sqlite")),
     "Catastro Armenia (operador Catastro Bogotá, servicio ArcGIS, vigencia 2023)", "Catastro Armenia"),
]
FUENTE = FUENTES[0][1]


def _bases():
    return [f for f in FUENTES if os.path.isfile(f[0])]


def disponible():
    return bool(_bases())


def _db(ruta):
    return sqlite3.connect(f"file:{ruta}?mode=ro", uri=True, timeout=30)


def _consultar(fn, vacio):
    """Aplica fn(db, fuente) a cada base y junta los resultados (listas)."""
    out = vacio
    for ruta, fuente, corto in _bases():
        db = _db(ruta)
        try:
            out = out + fn(db, (fuente, corto))
        except sqlite3.Error:
            pass
        finally:
            db.close()
    return out


def municipios():
    """{código DANE: predios} de los municipios ya descargados."""
    return dict(_consultar(lambda db, f: db.execute("select municipio, count(*) from predios group by 1").fetchall(), []))


def _fila(r, f):
    pid, clave, mun, clase, attrs, geom = r
    return dict(id=pid, clave=clave, municipio=mun, clase=clase, attrs=json.loads(attrs),
                polys=json.loads(zlib.decompress(geom)), fuente=f[0], fuente_corta=f[1])


def en_bbox(bbox, clase=None, limite=3000):
    """Predios cuyo recuadro toca bbox (lon/lat). clase: "R" rural, "U" urbano o None."""
    def fn(db, f):
        sql = ("select p.id, p.clave, p.municipio, p.clase, p.attrs, p.geom from predios_idx i join predios p on p.id = i.id "
               "where i.xmax >= ? and i.xmin <= ? and i.ymax >= ? and i.ymin <= ?")
        if clase:
            sql += " and p.clase " + ("= 'Urbano'" if clase == "U" else "<> 'Urbano'")
        return [_fila(r, f) for r in db.execute(sql + " limit ?", [bbox[0], bbox[2], bbox[1], bbox[3], limite + 1])]
    ps = _consultar(fn, [])
    return ps[:limite], len(ps) > limite


def en_punto(lon, lat):
    import gis
    for p in en_bbox((lon, lat, lon, lat), limite=50)[0]:
        for poly in p["polys"]:
            if gis._pir(lon, lat, poly[0]) and not any(gis._pir(lon, lat, h) for h in poly[1:]):
                return p
    return None


def por_codigo(cod):
    if not cod:
        return None
    def fn(db, f):
        r = db.execute("select id, clave, municipio, clase, attrs, geom from predios where clave=? "
                       "or json_extract(attrs, '$.nupre')=? or json_extract(attrs, '$.numero_anterior')=? limit 1",
                       (cod, cod, cod)).fetchone()
        return [_fila(r, f)] if r else []
    ps = _consultar(fn, [])
    return ps[0] if ps else None


def buscar(palabras, municipios_filtro=None, limite=15):
    """Predios cuyo nombre o dirección contiene todas las palabras (ya en mayúsculas y sin tildes)."""
    if not palabras:
        return []
    def fn(db, f):
        db.create_function("sin_tildes", 1, lambda s: _sin_tildes(s or ""), deterministic=True)
        # columna «nombre» ya normalizada (la llena el descargador); si falta, se normaliza al vuelo (más lento)
        con_col = "nombre" in [r[1] for r in db.execute("pragma table_info(predios)")]
        campo = "coalesce(nombre, sin_tildes(json_extract(attrs, '$.direccion')))" if con_col else "sin_tildes(json_extract(attrs, '$.direccion'))"
        sql = "select clave, municipio, clase, json_extract(attrs, '$.direccion') d from predios where " + \
              " and ".join(f"{campo} like ?" for _ in palabras)
        args = [f"%{w}%" for w in palabras]
        if municipios_filtro:
            sql += f" and municipio in ({','.join('?' * len(municipios_filtro))})"
            args += list(municipios_filtro)
        return db.execute(sql + " limit ?", args + [limite]).fetchall()
    return _consultar(fn, [])[:limite]


def _sin_tildes(s):
    import unicodedata
    return "".join(ch for ch in unicodedata.normalize("NFD", s) if unicodedata.category(ch) != "Mn").upper()


def area_m2(p):
    try:
        return float(p["attrs"].get("area") or 0) or None
    except (TypeError, ValueError):
        return None


def props(p, nombre_municipio):
    a = p["attrs"]
    out = {"Número predial" if len(p["clave"]) == 30 else "Número predial anterior": p["clave"]}
    if a.get("nupre"):
        out["NUPRE"] = a["nupre"]
    out["Municipio"] = nombre_municipio(p["municipio"]) or p["municipio"]
    if a.get("direccion"):
        out["Nombre o dirección"] = a["direccion"]
    if a.get("destinacion_economica"):
        out["Destinación"] = a["destinacion_economica"]
    if a.get("tipo"):
        out["Tipo"] = a["tipo"]
    ar = area_m2(p)
    if ar:
        out["Área catastral"] = f"{ar / 10000:,.4f} ha ({ar:,.0f} m²)".replace(",", "X").replace(".", ",").replace("X", ".")
    try:
        ac = float(a.get("area_construida") or 0)
        if ac:
            out["Área construida"] = f"{ac:,.0f} m²".replace(",", ".")
    except (TypeError, ValueError):
        pass
    out["Fuente"] = p["fuente"]
    return out
