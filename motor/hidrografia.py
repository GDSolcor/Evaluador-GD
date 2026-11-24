"""
Hidrografía IGAC 1:100.000 en el evaluador: capas para el mapa y rondas hídricas para el layout.

Fuentes (decisión del 24-sep-2026):
  - Drenaje sencillo y drenaje doble: GeoPackage exportado de la GDB del IGAC (CTM12), porque el
    servicio del IGAC no alcanzó a entregarlos (agotaba el tiempo). Diferencias con el servicio < 0,2 %.
    datos/igac/hidrografia_gdb/Hidrografia_completa.gpkg  (índice R-tree propio del GeoPackage)
  - Demás capas: extraídas del servicio vigente del IGAC (WGS84) a shapefile.
    datos/igac/hidrografia_100k/*.shp

Lee geometrías GeoPackage (WKB ISO, incluidas curvas: CircularString, CompoundCurve, CurvePolygon,
MultiSurface) sin GDAL; los arcos se densifican a tramos rectos.
"""
import math
import os
import sqlite3
import struct
import threading

import proyeccion as P
from capas_shp import CapaShp

BASE = os.path.dirname(os.path.abspath(__file__))
GPKG = os.path.join(BASE, "..", "datos", "igac", "hidrografia_gdb", "Hidrografia_completa.gpkg")
SHP_DIR = os.path.join(BASE, "..", "datos", "igac", "hidrografia_100k")
FUENTE_GPKG = "IGAC 1:100.000 · GDB (GeoPackage)"
FUENTE_SHP = "IGAC 1:100.000 · servicio vigente"

# id: (nombre, color, zoom mínimo, origen, tabla/archivo, ¿genera ronda?)
CAPAS = {
    "drenaje_sencillo": ("Ríos y quebradas (drenaje sencillo)", "#38bdf8", 12, "gpkg", "Drenaje_Sencillo", True),
    "drenaje_doble": ("Ríos anchos (drenaje doble)", "#0ea5e9", 9, "gpkg", "Drenaje_Doble", True),
    "laguna": ("Lagunas", "#2563eb", 10, "shp", "laguna", True),
    "cienaga": ("Ciénagas", "#1d4ed8", 9, "shp", "cienaga", True),
    "embalse": ("Embalses", "#1e40af", 8, "shp", "embalse", True),
    "pantano": ("Pantanos", "#0891b2", 11, "shp", "pantano", True),
    "humedal": ("Humedales", "#0e7490", 9, "shp", "humedal", True),
    "morichal": ("Morichales", "#059669", 10, "shp", "morichal", True),
    "madrevieja_r": ("Madreviejas (áreas)", "#0284c7", 11, "shp", "madrevieja_r", True),
    "madrevieja_l": ("Madreviejas (líneas)", "#0284c7", 11, "shp", "madrevieja_l", True),
    "canal_sencillo": ("Canales", "#7dd3fc", 11, "shp", "canal_sencillo", True),
    "canal_doble": ("Canales anchos", "#7dd3fc", 10, "shp", "canal_doble", True),
    "jaguey_r": ("Jagüeyes", "#60a5fa", 12, "shp", "jaguey_r", True),
    "otros_cuerpos_agua": ("Otros cuerpos de agua", "#3b82f6", 10, "shp", "otros_cuerpos_agua", True),
    "manglar": ("Manglares", "#15803d", 9, "shp", "manglar", False),
    "isla": ("Islas", "#a3a3a3", 10, "shp", "isla", False),
    "banco_arena": ("Bancos de arena", "#d6d3d1", 11, "shp", "banco_arena", False),
    "raudal_rapido": ("Raudales y rápidos", "#67e8f9", 11, "shp", "raudal_rapido", False),
    "linea_costera": ("Línea costera", "#0369a1", 7, "shp", "linea_costera", False),
}
# El GeoPackage no trae el dominio de ESTADO_DRENAJE; códigos del catálogo de objetos IGAC
# (421.982 elementos 5101, 4.829 elementos 5102). Se muestra el código junto al texto.
ESTADO_DRENAJE = {5101: "Permanente (5101)", 5102: "Intermitente (5102)"}

_local = threading.local()
_shp = {}


def _gpkg():
    if not hasattr(_local, "g"):
        _local.g = sqlite3.connect(f"file:{GPKG}?mode=ro", uri=True, check_same_thread=False)
    return _local.g


def _capa_shp(archivo):
    if archivo not in _shp:
        base = os.path.join(SHP_DIR, archivo)
        _shp[archivo] = CapaShp(base, crs="wgs84") if os.path.isfile(base + ".shp") else None
    return _shp[archivo]


def disponible_capa(cid):
    nombre, color, zoom, origen, fuente, _ = CAPAS[cid]
    return os.path.isfile(GPKG) if origen == "gpkg" else _capa_shp(fuente) is not None


# --------------------------------------------------------------------------- WKB (GeoPackage)
def _arco(p0, p1, p2, seg=8):
    """Densifica el arco circular que pasa por p0, p1, p2."""
    ax, ay = p0
    bx, by = p1
    cx, cy = p2
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        return [p0, p1, p2]
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / d
    r = math.hypot(ax - ux, ay - uy)
    t0, t1, t2 = (math.atan2(p[1] - uy, p[0] - ux) for p in (p0, p1, p2))
    # sentido del arco: el que pasa por p1
    def entre(a, b, x):
        return (x - a) % (2 * math.pi) <= (b - a) % (2 * math.pi)
    sentido = 1 if entre(t0, t2, t1) else -1
    barrido = ((t2 - t0) % (2 * math.pi)) if sentido == 1 else -((t0 - t2) % (2 * math.pi))
    return [(ux + r * math.cos(t0 + barrido * k / seg), uy + r * math.sin(t0 + barrido * k / seg)) for k in range(seg + 1)]


def _wkb(buf, off=0):
    """Devuelve (tipo_base, geometría, nuevo_offset). geometría: puntos | lista de partes."""
    bo = "<" if buf[off] == 1 else ">"
    t = struct.unpack_from(bo + "I", buf, off + 1)[0]
    off += 5
    t &= 0x0FFFFFFF
    dims = {0: 2, 1: 3, 2: 3, 3: 4}[t // 1000] if t >= 1000 else 2
    base = t % 1000

    def puntos(o):
        n = struct.unpack_from(bo + "I", buf, o)[0]
        o += 4
        v = struct.unpack_from(bo + f"{n * dims}d", buf, o)
        return [(v[i * dims], v[i * dims + 1]) for i in range(n)], o + 8 * n * dims

    if base == 1:
        x, y = struct.unpack_from(bo + "2d", buf, off)
        return 1, [(x, y)], off + 8 * dims
    if base == 2:
        pts, off = puntos(off)
        return 2, pts, off
    if base == 8:  # CircularString
        pts, off = puntos(off)
        out = [pts[0]]
        for i in range(0, len(pts) - 2, 2):
            out += _arco(pts[i], pts[i + 1], pts[i + 2])[1:]
        return 2, out, off
    if base == 3:
        n = struct.unpack_from(bo + "I", buf, off)[0]
        off += 4
        anillos = []
        for _ in range(n):
            pts, off = puntos(off)
            anillos.append(pts)
        return 3, anillos, off
    # colecciones y curvas compuestas: sub-geometrías WKB completas
    n = struct.unpack_from(bo + "I", buf, off)[0]
    off += 4
    hijos = []
    for _ in range(n):
        tb, g, off = _wkb(buf, off)
        hijos.append((tb, g))
    if base == 9:  # CompoundCurve -> una sola línea
        linea = []
        for _, g in hijos:
            linea += g if not linea else g[1:]
        return 2, linea, off
    if base == 10:  # CurvePolygon -> anillos
        return 3, [g for _, g in hijos], off
    if base in (4, 5, 11):  # Multi*
        return 5, [g for _, g in hijos], off
    if base in (6, 12):  # MultiPolygon / MultiSurface
        return 6, [g for _, g in hijos], off
    return 7, hijos, off


def _gpkg_geom(blob):
    """(tipo, partes) con tipo 'LineString' (lista de trazos) o 'Polygon' (lista de polígonos = lista de anillos)."""
    if blob is None:
        return None, []
    env = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}[(blob[3] >> 1) & 7]
    tb, g, _ = _wkb(blob, 8 + env)
    if tb == 2:
        return "LineString", [g]
    if tb == 5:
        return "LineString", g
    if tb == 3:
        return "Polygon", [g]
    if tb == 6:
        return "Polygon", g
    return None, []


# --------------------------------------------------------------------------- utilidades
def _simplificar(pts, tol):
    if tol <= 0 or len(pts) <= 3:
        return pts
    out = [pts[0]]
    for p in pts[1:-1]:
        if abs(p[0] - out[-1][0]) + abs(p[1] - out[-1][1]) >= tol:
            out.append(p)
    out.append(pts[-1])
    return out


def _wgs(pts, crs):
    if crs == "wgs84":
        return [[round(x, 7), round(y, 7)] for x, y in pts]
    return [[round(v, 7) for v in P.a_wgs84(x, y)] for x, y in pts]


def _consultar(cid, bbox, limite, tol_grados=0.0):
    """Genera (tipo, partes_lonlat, props) de la capa en el bbox geográfico."""
    nombre, color, zoom, origen, fuente, _ = CAPAS[cid]
    x0, y0, x1, y1 = bbox
    if origen == "gpkg":
        e0, n0, e1, n1 = P.bbox_a_ctm12(x0, y0, x1, y1)
        tol = tol_grados * 111320
        cols = "OBJECTID, SHAPE, NOMBRE_GEOGRAFICO" + (", ESTADO_DRENAJE" if fuente == "Drenaje_Sencillo" else ", SHAPE_Area")
        q = (f'select {cols} from "{fuente}" where OBJECTID in (select id from "rtree_{fuente}_SHAPE" '
             f"where maxx>=? and minx<=? and maxy>=? and miny<=? limit ?)")
        for oid, blob, nom, extra in _gpkg().execute(q, (e0, e1, n0, n1, limite)):
            tipo, partes = _gpkg_geom(blob)
            if not tipo:
                continue
            if tipo == "LineString":
                geo = [_wgs(_simplificar(p, tol), "ctm12") for p in partes]
            else:
                geo = [[_wgs(_simplificar(r, tol), "ctm12") for r in pol] for pol in partes]
            props = {"Nombre": nom or "(sin nombre)"}
            if fuente == "Drenaje_Sencillo" and extra:
                props["Régimen"] = ESTADO_DRENAJE.get(int(extra), str(extra))
            if fuente == "Drenaje_Doble" and extra:
                props["Área"] = f"{extra / 10000:,.1f} ha".replace(",", "X").replace(".", ",").replace("X", ".")
            yield tipo, geo, props, oid
    else:
        c = _capa_shp(fuente)
        if not c:
            return
        if c.progreso < 1.0:
            c.construir_indice()  # capas pequeñas: se indexan en segundos la primera vez
        for i, tipo, partes, a in c.leer(c.ids_en(x0, y0, x1, y1, limite=limite)):
            nom = a.get("NOMBRE_GEO") or ""
            if tipo == "Polygon":
                geo = [[_wgs(_simplificar(r, tol_grados), "wgs84") for r in partes]]
            else:
                geo = [_wgs(_simplificar(p, tol_grados), "wgs84") for p in partes]
            yield tipo, geo, {"Nombre": nom or "(sin nombre)"}, a.get("OBJECTID")


# --------------------------------------------------------------------------- API
def catalogo():
    out = []
    for cid, (nombre, color, zoom, origen, fuente, ronda) in CAPAS.items():
        if disponible_capa(cid):
            out.append(dict(id=cid, nombre=nombre, color=color, zoom=zoom, ronda=ronda,
                            fuente=FUENTE_GPKG if origen == "gpkg" else FUENTE_SHP))
    return dict(disponible=bool(out), capas=out)


def features(cid, bbox, limite=4000):
    if cid not in CAPAS or not disponible_capa(cid):
        return dict(type="FeatureCollection", features=[], truncado=False)
    tol = (bbox[2] - bbox[0]) / 2500  # ~ medio píxel de la vista
    feats = []
    for n, (tipo, geo, props, _) in enumerate(_consultar(cid, bbox, limite + 1, tol)):
        if n >= limite:
            return dict(type="FeatureCollection", features=feats, truncado=True)
        props = dict(props, Capa=CAPAS[cid][0], Fuente=FUENTE_GPKG if CAPAS[cid][3] == "gpkg" else FUENTE_SHP)
        if tipo == "Polygon":
            feats += [dict(type="Feature", geometry=dict(type="Polygon", coordinates=pol), properties=props) for pol in geo]
        else:
            feats.append(dict(type="Feature", geometry=dict(type="MultiLineString", coordinates=geo), properties=props))
    return dict(type="FeatureCollection", features=feats, truncado=False)


def cercanos(poligonos, radio_m):
    """Cuerpos de agua que generan ronda, a menos de radio_m de los polígonos construibles.
    Devuelve elementos en lon/lat con su distancia mínima al área construible."""
    import gis
    if not poligonos:
        return []
    pts = [p for poly in poligonos for p in poly]
    lat_m = sum(p[1] for p in pts) / len(pts)
    dx = (radio_m + 50) / (111320 * math.cos(math.radians(lat_m)))
    dy = (radio_m + 50) / 111320
    bbox = (min(p[0] for p in pts) - dx, min(p[1] for p in pts) - dy, max(p[0] for p in pts) + dx, max(p[1] for p in pts) + dy)
    out = []
    for cid, (nombre, color, zoom, origen, fuente, ronda) in CAPAS.items():
        if not ronda or not disponible_capa(cid):
            continue
        for tipo, geo, props, oid in _consultar(cid, bbox, 2000):
            if tipo == "LineString":
                trazos = [[tuple(p) for p in t] for t in geo]
                d = min(gis._dist_poligono_m(poly, t) for poly in poligonos for t in trazos if len(t) > 1)
                elementos = [dict(tipo="LineString", coords=t) for t in trazos]
            else:
                anillos = [[tuple(p) for p in r] for pol in geo for r in pol[:1]]
                if any(gis._poligonos_se_tocan(poly, [a]) for poly in poligonos for a in anillos):
                    d = 0.0
                else:
                    d = min(gis._dist_poligono_m(poly, a + a[:1]) for poly in poligonos for a in anillos)
                elementos = [dict(tipo="Polygon", coords=a) for a in anillos]
            if d <= radio_m:
                out.append(dict(capa=nombre, id=cid, nombre=props.get("Nombre"), regimen=props.get("Régimen"),
                                distancia_m=round(d, 1), elementos=elementos))
    out.sort(key=lambda h: h["distancia_m"])
    return out
