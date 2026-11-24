"""
Capas base GIS Solcor (respaldo 2026-09-23) para el evaluador.

Fuente: GIS_SOLCOR_Database.zip -> datos/gis/GIS_SOLCOR/
  data/coexistencia.db     tabla features(dataset, layer, name, attrs_json, geom_type, geom_json, bbox)
  rasters/topo*/slope_pct.npy   pendiente % (~30 m), 4 zonas (raster_meta.json)
  rasters/solar*/PVOUT.tif      Solargis PVOUT diario y anual (kWh/kWp)

Misma logica que core/query_engine.py del paquete, pero sin numpy ni Pillow:
el .npy se lee por ventanas con seek y el GeoTIFF (deflate, 1 fila por strip) con zlib.
Ademas del punto, aqui se cruza el POLIGONO del predio contra cada capa.
"""

import ast
import json
import math
import os
import re
import sqlite3
import struct
import threading
import zlib

BASE = os.path.dirname(os.path.abspath(__file__))
GIS_DIR = os.environ.get("GIS_SOLCOR", os.path.join(BASE, "..", "datos", "gis", "GIS_SOLCOR"))
DB_PATH = os.path.join(GIS_DIR, "data", "coexistencia.db")

# nivel: rojo = restriccion fuerte, ambar = revisar, info = contexto
DATASETS = {
    "ZONA_APTA_GD":             dict(nombre="Zonas aptas GD (Solcor)", color="#16a36b", grupo="Oportunidad", nivel="info", zoom=13),
    "RED_ELECTRICA":            dict(nombre="Red eléctrica (ENEL, EBSA, ESSA, CENS, EPM, EDEQ)", color="#f59e0b", grupo="Conexión", nivel="info", zoom=11),
    "MUNICIPIO":                dict(nombre="Municipios", color="#ffffff", grupo="Referencia", nivel="info", zoom=8),
    "PNN_LIMITES":              dict(nombre="Parques Nacionales Naturales", color="#b91c1c", grupo="Ambiental", nivel="rojo", zoom=6),
    "RUNAP":                    dict(nombre="Áreas protegidas RUNAP", color="#dc2626", grupo="Ambiental", nivel="rojo", zoom=8),
    "AREAS_PROTEGIDAS_CAR":     dict(nombre="Áreas protegidas CAR", color="#ef4444", grupo="Ambiental", nivel="rojo", zoom=8),
    "PRIORIDADES_CONSERVACION": dict(nombre="Prioridades de conservación", color="#a855f7", grupo="Ambiental", nivel="ambar", zoom=10),
    "REAA":                     dict(nombre="Ecosistemas (REAA)", color="#84cc16", grupo="Ambiental", nivel="info", zoom=12),
    "ANT":                      dict(nombre="Territorios étnicos y campesinos (ANT)", color="#ec4899", grupo="Social", nivel="rojo", zoom=8),
    "ANM_Titulo":               dict(nombre="Títulos mineros (ANM)", color="#78716c", grupo="Superposición", nivel="ambar", zoom=10),
    "ANLA_Area":                dict(nombre="Proyectos licenciados ANLA (áreas)", color="#0ea5e9", grupo="Superposición", nivel="ambar", zoom=10),
    "ANLA_Linea":               dict(nombre="Proyectos licenciados ANLA (líneas)", color="#0284c7", grupo="Superposición", nivel="ambar", zoom=10),
    "ANLA_Linea_Eval":          dict(nombre="Proyectos ANLA en evaluación (líneas)", color="#38bdf8", grupo="Superposición", nivel="ambar", zoom=10),
    "ANLA_Punto":               dict(nombre="Proyectos licenciados ANLA (puntos)", color="#0369a1", grupo="Superposición", nivel="info", zoom=10),
    # capas complementarias (herramientas/descargar_capas_arcgis.py -> datos/capas_complementarias.db)
    # buffer_m: puntos y líneas cuentan si quedan a esa distancia del predio
    "ICANH_Protegida":          dict(nombre="Áreas y parques arqueológicos protegidos (ICANH)", color="#9a3412", grupo="Arqueología", nivel="rojo", zoom=7),
    "ICANH_PMA":                dict(nombre="Zonificación arqueológica municipal (PMA, ICANH)", color="#c2410c", grupo="Arqueología", nivel="ambar", zoom=9),
    "ICANH_Hallazgo":           dict(nombre="Hallazgos arqueológicos positivos (PAP, ICANH)", color="#ea580c", grupo="Arqueología", nivel="ambar", zoom=12),
    "ICANH_Sitio":              dict(nombre="Sitios arqueológicos (ICANH)", color="#f97316", grupo="Arqueología", nivel="ambar", zoom=10, buffer_m=300),
    "ICANH_PAP":                dict(nombre="Programas de arqueología preventiva (ICANH)", color="#fdba74", grupo="Arqueología", nivel="info", zoom=10),
    "ANH_Tierras":              dict(nombre="Mapa de tierras de hidrocarburos (ANH)", color="#1e3a8a", grupo="Superposición", nivel="ambar", zoom=7),
    "ANH_Pozo":                 dict(nombre="Pozos de hidrocarburos (ANH)", color="#312e81", grupo="Superposición", nivel="ambar", zoom=11, buffer_m=200),
    "ANH_Sismica":              dict(nombre="Líneas sísmicas 2D (ANH)", color="#6366f1", grupo="Superposición", nivel="info", zoom=11, buffer_m=0),
    # reservas forestales de Ley 2ª de 1959: un proyecto solar dentro de la reserva exige sustracción previa ante MinAmbiente
    "MINAMBIENTE_Ley2":         dict(nombre="Reserva forestal Ley 2ª de 1959 (MinAmbiente)", color="#166534", grupo="Ambiental", nivel="rojo", zoom=7),
    "MINAMBIENTE_Ley2_Sustraccion": dict(nombre="Sustracciones definitivas de reserva Ley 2ª", color="#65a30d", grupo="Ambiental", nivel="info", zoom=9),
    # autoridades ambientales regionales, capas tal como las publican sus visores (#47,
    # herramientas/descargar_autoridades_ambientales.py -> datos/autoridades_ambientales.db)
    "CORPOBOYACA_Hidrografia":   dict(nombre="Drenajes y fuentes hídricas 1:10.000 a 1:100.000", color="#0ea5e9", grupo="Corpoboyacá", nivel="ambar", zoom=12, buffer_m=30),
    "CORPOBOYACA_Rondas":        dict(nombre="Rondas hídricas acotadas, cauces y cotas de inundación", color="#1d4ed8", grupo="Corpoboyacá", nivel="rojo", zoom=11),
    "CORPOBOYACA_Paramos_Humedales": dict(nombre="Páramos y humedales delimitados", color="#7c3aed", grupo="Corpoboyacá", nivel="rojo", zoom=9),
    "CORPOBOYACA_Areas_Protegidas": dict(nombre="Áreas protegidas, SIRAP y reservas Ley 2ª", color="#dc2626", grupo="Corpoboyacá", nivel="rojo", zoom=9),
    "CORPOBOYACA_Recarga":       dict(nombre="Recarga de acuíferos e interés hídrico", color="#06b6d4", grupo="Corpoboyacá", nivel="ambar", zoom=10),
    "CORPOBOYACA_POMCA":         dict(nombre="Zonificación POMCA y ordenación forestal", color="#65a30d", grupo="Corpoboyacá", nivel="ambar", zoom=11),
    "CORPOBOYACA_Riesgo":        dict(nombre="Gestión del riesgo 2024-2026 (incendios, movimientos en masa)", color="#ea580c", grupo="Corpoboyacá", nivel="ambar", zoom=10),
    "CORPOBOYACA_Geologia":      dict(nombre="Geología e hidrogeología", color="#a16207", grupo="Corpoboyacá", nivel="info", zoom=11),
    "CORPOBOYACA_Referencia":    dict(nombre="Jurisdicción, municipios, veredas y cuencas", color="#94a3b8", grupo="Corpoboyacá", nivel="info", zoom=9),
    "CORPOBOYACA_Predial":       dict(nombre="Predial urbano y rural (Corpoboyacá)", color="#f472b6", grupo="Corpoboyacá", nivel="info", zoom=15),
    "CAR_Rondas":                dict(nombre="Delimitación de rondas hídricas", color="#1d4ed8", grupo="CAR Cundinamarca", nivel="rojo", zoom=11),
    "CAR_Paramos":               dict(nombre="Páramos", color="#7c3aed", grupo="CAR Cundinamarca", nivel="rojo", zoom=8),
    "CAR_Areas_Protegidas":      dict(nombre="Áreas protegidas y su zonificación ambiental", color="#dc2626", grupo="CAR Cundinamarca", nivel="rojo", zoom=9),
    "CAR_POMCA":                 dict(nombre="Zonificación ambiental POMCA", color="#65a30d", grupo="CAR Cundinamarca", nivel="ambar", zoom=11),
    "CAR_Riesgo":                dict(nombre="Amenazas municipales y registro de eventos", color="#ea580c", grupo="CAR Cundinamarca", nivel="ambar", zoom=11),
    "CAR_Ecosistemas":           dict(nombre="Estructura ecológica, aguas subterráneas y restauración", color="#0d9488", grupo="CAR Cundinamarca", nivel="ambar", zoom=10),
    "CAR_Riesgo_Incendios":      dict(nombre="Riesgo por incendios de cobertura vegetal (2014)", color="#f97316", grupo="CAR Cundinamarca", nivel="info", zoom=10),
    "CAR_Clima_Suelos":          dict(nombre="Cambio climático, clima, suelos y cambio de uso", color="#a3a3a3", grupo="CAR Cundinamarca", nivel="info", zoom=10),
    "CAR_Referencia":            dict(nombre="Jurisdicción CAR, cuencas y subcuencas 1:25.000", color="#94a3b8", grupo="CAR Cundinamarca", nivel="info", zoom=9),
}
# Zonas de reserva campesina y territorios agroalimentarios no exigen consulta previa: revisar, no bloquear
ANT_AMBAR = ("Zona_de_Reserva_Campesina", "Territorios_Campesinos")

_local = threading.local()


def disponible():
    return os.path.isfile(DB_PATH)


# Red eléctrica completada desde los geoportales de los OR (herramientas/descargar_red_*.py +
# motor/red_complementaria.py). Va en una base aparte para no modificar el respaldo GIS Solcor;
# una vista temporal "features" une ambas y el resto del código no nota la diferencia.
COMPLEMENTO = os.path.join(BASE, "..", "datos", "red_or", "red_complementaria.db")
# Capas de restricción de otras entidades (ICANH, ANH), mismo esquema (herramientas/descargar_capas_arcgis.py)
CAPAS_EXTRA = os.path.join(BASE, "..", "datos", "capas_complementarias.db")
# Capas de Corpoboyacá y la CAR (herramientas/descargar_autoridades_ambientales.py), mismo esquema
AUTORIDADES = os.environ.get("EVALUADOR_AUTORIDADES_DB", os.path.join(BASE, "..", "datos", "autoridades_ambientales.db"))
REEMPLAZADAS_POR_COMPLEMENTO = ("ANLA_Linea",)
ANLA_LINEAS = ("ANLA_Linea", "ANLA_Linea_Eval")  # cuentan si pasan a menos de buffer_anla_linea_m del predio


def _db():
    if not hasattr(_local, "conn"):
        c = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, check_same_thread=False)
        partes, filtro = [], ""
        for alias, ruta in (("rc", COMPLEMENTO), ("cc", CAPAS_EXTRA), ("aa", AUTORIDADES)):
            if os.path.isfile(ruta):
                c.execute(f"ATTACH DATABASE 'file:{os.path.abspath(ruta).replace(chr(92), '/')}?mode=ro' AS {alias}")
                partes.append(f"SELECT * FROM {alias}.features")
        if "SELECT * FROM rc.features" in partes and \
                c.execute("SELECT 1 FROM rc.sqlite_master WHERE name='duplicados'").fetchone():
            # capas o tramos repetidos en otra capa más completa/reciente (motor/red_duplicados.py)
            filtro = (" WHERE id NOT IN (SELECT fid FROM rc.duplicados WHERE tipo='elemento')"
                      " AND layer NOT IN (SELECT layer FROM rc.duplicados WHERE tipo='capa')")
        if "SELECT * FROM cc.features" in partes:
            # capas que la base complementaria trae más completas y recientes que el respaldo GIS Solcor:
            # se toma solo la complementaria (p. ej. líneas ANLA del servicio vigente: 534 expedientes contra 295)
            reemplazadas = [ds for ds in REEMPLAZADAS_POR_COMPLEMENTO
                            if c.execute("SELECT 1 FROM cc.features WHERE dataset=? LIMIT 1", (ds,)).fetchone()]
            if reemplazadas:
                cond = "dataset NOT IN (" + ",".join(f"'{ds}'" for ds in reemplazadas) + ")"
                filtro = filtro + " AND " + cond if filtro else " WHERE " + cond
        if partes:
            c.execute(f"CREATE TEMP VIEW features AS SELECT * FROM main.features{filtro} UNION ALL " + " UNION ALL ".join(partes))
        _local.conn = c
    return _local.conn


# --------------------------------------------------------------------------
# Rasteres sin dependencias
# --------------------------------------------------------------------------

class _Npy:
    """Lector por ventanas de un .npy float32 2D (C-order)."""

    def __init__(self, path, geo):
        self.path, self.geo = path, geo
        with open(path, "rb") as f:
            magic = f.read(6)
            assert magic == b"\x93NUMPY", "no es .npy"
            major = f.read(2)[0]
            hlen = struct.unpack("<H" if major == 1 else "<I", f.read(2 if major == 1 else 4))[0]
            hdr = ast.literal_eval(f.read(hlen).decode("latin1"))
            self.offset = f.tell()
        assert hdr["descr"] == "<f4" and not hdr["fortran_order"]
        self.h, self.w = hdr["shape"]

    def contiene(self, lon, lat):
        g = self.geo
        c, r = int((lon - g["lon0"]) / g["sx"]), int((g["lat0"] - lat) / g["sy"])
        return 0 <= r < self.h and 0 <= c < self.w

    def ventana(self, lon_min, lat_min, lon_max, lat_max):
        g = self.geo
        c0 = max(0, int((lon_min - g["lon0"]) / g["sx"]))
        c1 = min(self.w - 1, int((lon_max - g["lon0"]) / g["sx"]))
        r0 = max(0, int((g["lat0"] - lat_max) / g["sy"]))
        r1 = min(self.h - 1, int((g["lat0"] - lat_min) / g["sy"]))
        vals = []
        with open(self.path, "rb") as f:
            n = c1 - c0 + 1
            for r in range(r0, r1 + 1):
                f.seek(self.offset + (r * self.w + c0) * 4)
                vals.extend(v for v in struct.unpack(f"<{n}f", f.read(4 * n)) if not math.isnan(v))
        return vals


class _GeoTiff:
    """GeoTIFF float32, deflate/sin compresion, organizado en strips (caso Solargis)."""

    def __init__(self, path):
        with open(path, "rb") as f:
            self.b = f.read()
        b = self.b
        self.bo = "<" if b[:2] == b"II" else ">"
        off = struct.unpack(self.bo + "I", b[4:8])[0]
        n = struct.unpack(self.bo + "H", b[off:off + 2])[0]
        sizes = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 11: 4, 12: 8, 16: 8}
        fmts = {3: "H", 4: "I", 11: "f", 12: "d", 16: "Q"}
        tags = {}
        for i in range(n):
            e = off + 2 + 12 * i
            tag, typ, cnt = struct.unpack(self.bo + "HHI", b[e:e + 8])
            size = sizes.get(typ, 1) * cnt
            data = b[e + 8:e + 8 + size] if size <= 4 else \
                b[struct.unpack(self.bo + "I", b[e + 8:e + 12])[0]:][:size]
            tags[tag] = struct.unpack(self.bo + fmts[typ] * cnt, data) if typ in fmts else data
        self.w, self.h = tags[256][0], tags[257][0]
        self.comp = tags[259][0]
        self.rps = tags.get(278, (self.h,))[0]
        self.offs, self.cnts = tags[273], tags[279]
        self.sx, self.sy = tags[33550][0], tags[33550][1]
        self.lon0, self.lat0 = tags[33922][3], tags[33922][4]
        assert tags[258][0] == 32 and tags.get(339, (1,))[0] == 3 and tags.get(317, (1,))[0] == 1
        self._cache = {}

    def _strip(self, i):
        if i not in self._cache:
            raw = self.b[self.offs[i]:self.offs[i] + self.cnts[i]]
            if self.comp in (8, 32946):
                raw = zlib.decompress(raw)
            elif self.comp != 1:
                raise ValueError(f"compresion TIFF {self.comp} no soportada")
            self._cache[i] = raw
        return self._cache[i]

    def valor(self, lon, lat):
        c, r = int((lon - self.lon0) / self.sx), int((self.lat0 - lat) / self.sy)
        if not (0 <= r < self.h and 0 <= c < self.w):
            return None
        s = self._strip(r // self.rps)
        v = struct.unpack(self.bo + "f", s[((r % self.rps) * self.w + c) * 4:][:4])[0]
        return None if math.isnan(v) or v < 1e-30 else v


_rasters = {}


def _pvout(nombre):
    if nombre not in _rasters:
        p = os.path.join(GIS_DIR, "rasters", nombre, "PVOUT.tif")
        _rasters[nombre] = _GeoTiff(p) if os.path.isfile(p) else None
    return _rasters[nombre]


# Mapa de calor del recurso solar: el raster PVOUT anual pintado como imagen para sobreponerla en el mapa.
RAMPA_PVOUT = [(1000, (49, 54, 149)), (1200, (69, 117, 180)), (1350, (116, 173, 209)), (1450, (171, 217, 233)),
               (1550, (255, 255, 191)), (1650, (254, 224, 144)), (1750, (253, 174, 97)), (1850, (244, 109, 67)), (1950, (215, 48, 39))]
_pvout_img = {}


def _color_pvout(v):
    if v <= RAMPA_PVOUT[0][0]:
        return RAMPA_PVOUT[0][1]
    for (v0, c0), (v1, c1) in zip(RAMPA_PVOUT, RAMPA_PVOUT[1:]):
        if v <= v1:
            t = (v - v0) / (v1 - v0)
            return tuple(int(a + (b - a) * t) for a, b in zip(c0, c1))
    return RAMPA_PVOUT[-1][1]


def pvout_mapa(ancho=1920):
    """(png, info) del PVOUT anual de todo el raster. Las filas de la imagen van espaciadas en
    Mercator, que es como la estira el mapa, para que el calor caiga donde corresponde.
    Se calcula una vez y queda en datos/cache_pvout.png."""
    if "png" in _pvout_img:
        return _pvout_img["png"], _pvout_img["info"]
    t = _pvout("solar_yearly")
    if t is None:
        return None, dict(disponible=False)
    norte, oeste = t.lat0, t.lon0
    sur, este = t.lat0 - t.h * t.sy, t.lon0 + t.w * t.sx
    info = dict(disponible=True, bounds=[[sur, oeste], [norte, este]], rampa=[[v, "#%02x%02x%02x" % c] for v, c in RAMPA_PVOUT],
                unidad="kWh/kWp·año", fuente="Solargis PVOUT (GIS Solcor)")
    cache = os.path.join(BASE, "..", "datos", "cache_pvout.png")
    if os.path.isfile(cache) and os.path.getmtime(cache) >= os.path.getmtime(os.path.join(GIS_DIR, "rasters", "solar_yearly", "PVOUT.tif")):
        with open(cache, "rb") as f:
            png = f.read()
    else:
        merc = lambda lat: math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))  # noqa: E731
        y_n, y_s = merc(norte), merc(sur)
        alto = max(1, round(ancho * (y_n - y_s) / math.radians(este - oeste)))
        cols = [min(t.w - 1, int((i + 0.5) * t.w / ancho)) for i in range(ancho)]
        filas = bytearray()
        for j in range(alto):
            lat = math.degrees(2 * math.atan(math.exp(y_n - (j + 0.5) * (y_n - y_s) / alto)) - math.pi / 2)
            r = min(t.h - 1, max(0, int((norte - lat) / t.sy)))
            s = t._strip(r // t.rps)
            base = (r % t.rps) * t.w * 4
            vals = struct.unpack(t.bo + f"{t.w}f", s[base:base + t.w * 4])
            filas.append(0)
            for c in cols:
                v = vals[c]
                filas += b"\0\0\0\0" if (v != v or v < 1e-30) else bytes(_color_pvout(v)) + b"\xff"
        t._cache.clear()

        def bloque(tipo, d):
            return struct.pack(">I", len(d)) + tipo + d + struct.pack(">I", zlib.crc32(tipo + d))
        png = (b"\x89PNG\r\n\x1a\n" + bloque(b"IHDR", struct.pack(">IIBBBBB", ancho, alto, 8, 6, 0, 0, 0))
               + bloque(b"IDAT", zlib.compress(bytes(filas), 9)) + bloque(b"IEND", b""))
        try:
            with open(cache, "wb") as f:
                f.write(png)
        except OSError:
            pass
    _pvout_img.update(png=png, info=info)
    return png, info


def _pendientes():
    if "slope" not in _rasters:
        meta_p = os.path.join(GIS_DIR, "data", "raster_meta.json")
        zonas = []
        if os.path.isfile(meta_p):
            with open(meta_p, encoding="utf-8") as f:
                meta = json.load(f)
            for z, geo in meta.items():
                p = os.path.join(GIS_DIR, "rasters", z, "slope_pct.npy")
                if os.path.isfile(p):
                    zonas.append(_Npy(p, geo))
        _rasters["slope"] = zonas
    return _rasters["slope"]


def pendiente(poligonos):
    """Estadisticas de pendiente (%) de las celdas (~30 m) dentro del bbox del predio."""
    pts = [p for poly in poligonos for p in poly]
    lon = sum(p[0] for p in pts) / len(pts)
    lat = sum(p[1] for p in pts) / len(pts)
    for z in _pendientes():
        if z.contiene(lon, lat):
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            vals = z.ventana(min(xs), min(ys), max(xs), max(ys))
            if not vals:
                return None
            vals.sort()
            return dict(promedio=sum(vals) / len(vals), p90=vals[int(0.9 * (len(vals) - 1))],
                        maximo=vals[-1], celdas=len(vals))
    return None


# --------------------------------------------------------------------------
# Geometria (lon/lat, distancias en metros con proyeccion local)
# --------------------------------------------------------------------------

def _pir(x, y, ring):
    ins, j = False, len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi + 1e-15) + xi:
            ins = not ins
        j = i
    return ins


def _cruzan(a, b, c, d):
    def o(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    return o(a, b, c) * o(a, b, d) < 0 and o(c, d, a) * o(c, d, b) < 0


def _poligonos_se_tocan(p, rings):
    """p: anillo del predio; rings: poligono de la capa (exterior + huecos)."""
    ext = rings[0]
    # descartes por recuadro envolvente: un humedal o un río grande trae miles de vértices y un predio
    # catastral cientos; sin esto el cruce hidrografía-predio tomaba decenas de segundos
    ex0, ey0 = min(q[0] for q in ext), min(q[1] for q in ext)
    ex1, ey1 = max(q[0] for q in ext), max(q[1] for q in ext)
    px0, py0 = min(q[0] for q in p), min(q[1] for q in p)
    px1, py1 = max(q[0] for q in p), max(q[1] for q in p)
    if ex0 > px1 or ex1 < px0 or ey0 > py1 or ey1 < py0:
        return False
    if any(ex0 <= x <= ex1 and ey0 <= y <= ey1 and _pir(x, y, ext) and not any(_pir(x, y, h) for h in rings[1:]) for x, y in p):
        return True
    if any(px0 <= pt[0] <= px1 and py0 <= pt[1] <= py1 and _pir(pt[0], pt[1], p) for pt in ext[::max(1, len(ext) // 400)]):
        return True
    pe = list(zip(p, p[1:] + p[:1]))
    for i in range(len(ext) - 1):
        a, b = ext[i], ext[i + 1]
        if max(a[0], b[0]) < px0 or min(a[0], b[0]) > px1 or max(a[1], b[1]) < py0 or min(a[1], b[1]) > py1:
            continue
        for c, d in pe:
            if _cruzan(a, b, c, d):
                return True
    return False


def _dist_m(lon, lat, path):
    mx = 111320 * math.cos(math.radians(lat))
    best = float("inf")
    for i in range(len(path) - 1):
        ax, ay = (path[i][0] - lon) * mx, (path[i][1] - lat) * 111320
        bx, by = (path[i + 1][0] - lon) * mx, (path[i + 1][1] - lat) * 111320
        dx, dy = bx - ax, by - ay
        L = dx * dx + dy * dy
        t = 0 if L == 0 else max(0, min(1, -(ax * dx + ay * dy) / L))
        best = min(best, math.hypot(ax + t * dx, ay + t * dy))
    return best


def _dist_poligono_m(poly, path):
    """Distancia minima de una linea al poligono del predio (0 si lo cruza o entra)."""
    px0, py0 = min(q[0] for q in poly), min(q[1] for q in poly)
    px1, py1 = max(q[0] for q in poly), max(q[1] for q in poly)
    if any(px0 <= p[0] <= px1 and py0 <= p[1] <= py1 and _pir(p[0], p[1], poly) for p in path):
        return 0.0
    # cota inferior por tramo (distancia al recuadro del tramo): se saltan los tramos que no pueden mejorar
    lat = poly[0][1]
    mx, my = 111320 * math.cos(math.radians(lat)), 111320
    tramos = []
    for i in range(len(path) - 1):
        a, b = path[i], path[i + 1]
        tramos.append((min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]), i))
    best = float("inf")
    for x, y in poly:
        for tx0, ty0, tx1, ty1, i in tramos:
            ddx = (tx0 - x if x < tx0 else x - tx1 if x > tx1 else 0) * mx
            ddy = (ty0 - y if y < ty0 else y - ty1 if y > ty1 else 0) * my
            if ddx >= best or ddy >= best or ddx * ddx + ddy * ddy >= best * best:
                continue
            d = _dist_m(x, y, path[i:i + 2])
            if d < best:
                best = d
    return best


def _etiqueta(ds, layer, name, attrs):
    for k in ("_name", "ap_nombre", "NOMBRE", "nombre", "Proyecto", "unidad_analisis"):
        if attrs.get(k):
            return str(attrs[k])
    return name or layer


def _operador(layer, name):
    lyr, nm = str(layer).upper(), str(name).upper()
    if lyr.startswith("EBSA_"):
        return "EBSA"
    if lyr.startswith("CENS_"):
        return "CENS"
    if lyr.startswith("EPM_"):
        return "EPM"
    if lyr.startswith("EDEQ_"):
        return "EDEQ"
    if lyr.startswith("CELSIA_"):
        return "CELSIA"
    if lyr.startswith(("ENERCA_", "EMSA_")):
        return lyr.split("_")[0]
    if lyr.startswith("AIRE_"):
        return "AIR-E"
    if lyr.startswith("AFINIA_"):
        return "AFINIA"
    if lyr.startswith("CHEC_"):
        return "CHEC"
    if lyr.startswith("EEP_"):
        return "EEP"
    if "ESSA" in lyr or lyr.startswith(("LINEAS_34", "LINEAS_115", "LINEAS_230")):  # líneas de Santander (ticket #44)
        return "ESSA"
    if "ENEL" in lyr or "CODENSA" in lyr or lyr.startswith("REDES_") and "34" not in lyr or "COTA" in lyr or "--" in nm:
        return "ENEL"
    if any(e in lyr for e in ("REDES_34", "LINEAS_", "SUBESTACION", "HIGUERAS")) or lyr.startswith("CTO_1"):
        return "EBSA"
    return "sin identificar"


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------

def _conteos():
    """Geometrias por capa. Contarlas toma ~4 s, asi que se guardan junto a la base."""
    ruta = os.path.join(GIS_DIR, "..", "_conteos.json")
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        pass
    conteos = {ds: n for ds, n in _db().execute("SELECT dataset, count(*) FROM features GROUP BY dataset")}
    try:
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump(conteos, f)
    except OSError:
        pass
    return conteos


def catalogo():
    if not disponible():
        return dict(disponible=False, datasets=[])
    n = _conteos()
    return dict(disponible=True,
                datasets=[dict(id=k, geometrias=n.get(k, 0), **v) for k, v in DATASETS.items()])


def _simplificar(coords, max_pts):
    if len(coords) <= max_pts:
        return coords
    k = math.ceil(len(coords) / max_pts)
    out = coords[::k]
    if out[-1] != coords[-1]:
        out.append(coords[-1])
    return out


def features(ds, bbox, limite=2500):
    """GeoJSON de un dataset dentro de la vista del mapa."""
    if ds not in DATASETS or not disponible():
        return dict(type="FeatureCollection", features=[], truncado=False)
    x0, y0, x1, y1 = bbox
    rows = _db().execute(
        "SELECT layer, name, attrs_json, geom_type, geom_json FROM features "
        "WHERE dataset=? AND xmax>=? AND xmin<=? AND ymax>=? AND ymin<=? LIMIT ?",
        (ds, x0, x1, y0, y1, limite + 1)).fetchall()
    truncado = len(rows) > limite
    feats = []
    max_pts = 600 if ds == "MUNICIPIO" else 1500
    for layer, name, attrs_json, gt, gj in rows[:limite]:
        g = json.loads(gj)
        attrs = json.loads(attrs_json)
        if gt == "Polygon":
            geom = dict(type="Polygon", coordinates=[_simplificar(r, max_pts) for r in g])
        elif gt == "LineString":
            geom = dict(type="MultiLineString", coordinates=[_simplificar(p, max_pts) for p in g])
        else:
            geom = dict(type="Point", coordinates=g)
        props = dict(etiqueta=_etiqueta(ds, layer, name, attrs), capa=layer)
        props.update({k: v for k, v in list(attrs.items())[:8] if not str(k).startswith(("AREA_ha_", "Shape"))})
        feats.append(dict(type="Feature", geometry=geom, properties=props))
    return dict(type="FeatureCollection", features=feats, truncado=truncado)


def analizar(poligonos, buffer_red_m=3000, buffer_anla_linea_m=50):
    """Cruza los poligonos construibles del predio contra todas las capas.
    Devuelve hallazgos con nivel (rojo/ambar/info), PVOUT y pendiente."""
    if not disponible() or not poligonos:
        return dict(disponible=disponible())
    pts = [p for poly in poligonos for p in poly]
    lon = sum(p[0] for p in pts) / len(pts)
    lat = sum(p[1] for p in pts) / len(pts)
    x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
    y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
    buf = buffer_red_m / 111320.0
    db = _db()
    hallazgos, municipio, zonas_aptas, redes = [], None, [], []
    vistos = set()

    def cand(ds, b=0.0):
        return db.execute(
            "SELECT layer, name, attrs_json, geom_type, geom_json FROM features "
            "WHERE dataset=? AND xmax>=? AND xmin<=? AND ymax>=? AND ymin<=?",
            (ds, x0 - b, x1 + b, y0 - b, y1 + b))

    for ds, meta in DATASETS.items():
        if ds == "RED_ELECTRICA":
            continue
        b = buffer_anla_linea_m / 111320.0 if ds in ANLA_LINEAS else meta.get("buffer_m", 0) / 111320.0 * 1.3
        for layer, name, aj, gt, gj in cand(ds, b):
            g = json.loads(gj)
            attrs = json.loads(aj)
            et = _etiqueta(ds, layer, name, attrs)
            if gt == "Polygon":
                if ds == "MUNICIPIO":
                    # la capa mezcla departamentos y municipios: gana el poligono mas pequeno
                    if _pir(lon, lat, g[0]):
                        xs, ys = [p[0] for p in g[0]], [p[1] for p in g[0]]
                        area_bb = (max(xs) - min(xs)) * (max(ys) - min(ys))
                        if municipio is None or area_bb < municipio["_bb"]:
                            municipio = dict(nombre=attrs.get("nombre", et), depto=attrs.get("depto", ""),
                                             codigo=attrs.get("codigo", ""), _bb=area_bb)
                    continue
                if not any(_poligonos_se_tocan(poly, g) for poly in poligonos):
                    continue
                if ds == "ZONA_APTA_GD":
                    zonas_aptas.append(attrs)
                    continue
                nivel = meta["nivel"]
                if ds == "ANT" and layer.startswith(ANT_AMBAR):
                    nivel = "ambar"
                if ds == "REAA":
                    et = attrs.get("NM_CAPA_20") or attrs.get("CAT_ECO_20") or et
                if ds == "ANH_Tierras" and attrs.get("Clasificación") != "ASIGNADA":
                    nivel = "info"  # el mapa de tierras cubre todo el país: solo las áreas con contrato son de revisar
                if ds in ("CORPOBOYACA_POMCA", "CAR_POMCA", "CAR_Riesgo"):
                    # cubren todo el territorio: solo las zonas que condicionan el uso son de revisar (#47)
                    texto = " ".join(str(v) for v in attrs.values() if isinstance(v, str)).lower()
                    patron = r"\balta\b" if ds == "CAR_Riesgo" else r"protecci|conservaci|restauraci|preservaci|amenaza alta"
                    nivel = meta["nivel"] if re.search(patron, texto) else "info"
                clave = (ds, et)
                if clave in vistos:
                    continue
                vistos.add(clave)
                extra = layer if ds == "ANT" else attrs.get("ap_categoria") or attrs.get("CATEGORIA") or \
                    attrs.get("Modalidad") or attrs.get("prioridad") or attrs.get("Operador") or ""
                if ds.startswith("MINAMBIENTE_"):
                    extra = " · ".join(str(x) for x in (attrs.get("Acto administrativo") or attrs.get("Resolución de zonificación"),
                                                        attrs.get("Entidad que sustrajo")) if x)
                elif ds.startswith(("CORPOBOYACA_", "CAR_")):  # autoridades ambientales: capa del visor
                    extra = layer
                elif ds.startswith(("ICANH_", "ANH_")):  # capas complementarias: capa de origen + estado/tipo
                    extra = " · ".join(str(x) for x in (layer if ds != "ANH_Tierras" else attrs.get("Leyenda"),
                                                        attrs.get("Tipo de contrato") if attrs.get("Tipo de contrato") != "NO APLICA" else None,
                                                        attrs.get("Operador") if attrs.get("Clasificación") == "ASIGNADA" else None,
                                                        attrs.get("Zona") or attrs.get("Tipo")) if x)
                hallazgos.append(dict(dataset=ds, capa=meta["nombre"], nivel=nivel, detalle=et, extra=extra))
            elif gt == "LineString" and ds in ANLA_LINEAS:
                d = min(_dist_poligono_m(poly, path) for poly in poligonos for path in g)
                if d <= buffer_anla_linea_m:
                    hallazgos.append(dict(dataset=ds, capa=meta["nombre"], nivel="ambar", detalle=et,
                                          extra=f"a {d:.0f} m"))
            elif "buffer_m" in meta and gt in ("Point", "LineString"):
                # sitios arqueológicos, pozos, líneas sísmicas: cuentan si están dentro o cerca del predio
                if gt == "Point":
                    d = min(0.0 if _pir(g[0], g[1], poly) else _dist_m(g[0], g[1], poly + poly[:1]) for poly in poligonos)
                else:
                    d = min(_dist_poligono_m(poly, path) for poly in poligonos for path in g)
                if d > meta["buffer_m"]:
                    continue
                clave = (ds, et)
                if clave in vistos:
                    continue
                vistos.add(clave)
                hallazgos.append(dict(dataset=ds, capa=meta["nombre"], nivel=meta["nivel"], detalle=et,
                                      extra="dentro del predio" if d == 0 else f"a {d:.0f} m del predio"))

    for layer, name, aj, gt, gj in cand("RED_ELECTRICA", buf):
        g = json.loads(gj)
        attrs = json.loads(aj)
        if gt == "LineString":
            d = min(_dist_poligono_m(poly, path) for poly in poligonos for path in g)
        else:
            d = min(_dist_m(g[0], g[1], poly + poly[:1]) for poly in poligonos)
        if d <= buffer_red_m:
            trafo = layer.endswith("_Transformadores")
            if layer == "CELSIA_Transformadores":  # Celsia no publica el circuito: zona y tensión primaria
                nombre = f"red {name} ({attrs.get('Capacidad (kVA)', '?')} kVA)"
            elif trafo or layer in ("ESSA_Circuitos_estimados", "EBSA_Circuitos_GDC", "CENS_Circuitos_MT", "EPM_Circuitos_MT",
                                  "EDEQ_Circuitos_estimados", "CHEC_Circuitos_MT", "EEP_Circuitos_MT") or layer.endswith("_Circuitos_GDC"):
                nombre = f"circuito {name}"
            elif layer.upper().startswith("CTO_1"):  # capa = circuito, elemento = tramo
                nombre = f"circuito {layer.split('.')[0].replace('CTO_', '')} (tramo {name})"
            else:
                nombre = _etiqueta("RED_ELECTRICA", layer, name, attrs)
            redes.append(dict(operador=_operador(layer, name), capa=layer, nombre=nombre,
                              tipo="transformador" if trafo else "subestación" if gt == "Point" else
                              "circuito (trazado estimado)" if layer.endswith("_Circuitos_estimados") else "línea", distancia_m=round(d)))
    redes.sort(key=lambda r: (r["distancia_m"], r["capa"].startswith("Redes_ENEL_MT")))
    unicas, vistas = [], set()
    for r in redes:  # la misma linea viene en la capa del circuito y en la capa general del OR
        k1, k2 = (r["operador"], r["tipo"], r["distancia_m"]), (r["operador"], r["tipo"], r["nombre"])
        if k1 not in vistas and k2 not in vistas:
            vistas.update((k1, k2))
            unicas.append(r)
    redes = unicas
    if municipio:
        municipio.pop("_bb")

    pv_a, pv_d = _pvout("solar_yearly"), _pvout("solar")
    pvout = dict(anual=pv_a.valor(lon, lat) if pv_a else None, diario=pv_d.valor(lon, lat) if pv_d else None)
    pend = pendiente(poligonos)

    # autoridades ambientales (#47): los hallazgos informativos se agrupan en una fila por grupo de capas,
    # para que el reporte no se llene con decenas de capas de contexto (clima, suelos, amenaza baja…)
    info_aut = {}
    for h in hallazgos:
        if h["nivel"] == "info" and h["dataset"].startswith(("CAR_", "CORPOBOYACA_")):
            info_aut.setdefault(h["dataset"], []).append(h)
    if info_aut:
        hallazgos = [h for h in hallazgos if not (h["nivel"] == "info" and h["dataset"] in info_aut)]
        for ds, hs in info_aut.items():
            capas = sorted({h["extra"] or h["detalle"] for h in hs if h["extra"] or h["detalle"]})
            if len(hs) == 1:
                hallazgos.append(hs[0])
            else:
                hallazgos.append(dict(dataset=ds, capa=DATASETS[ds]["nombre"], nivel="info",
                                      detalle=f"{len(capas)} capa{'s' if len(capas) != 1 else ''} sobre el predio",
                                      extra="; ".join(capas[:12]) + (f"; y {len(capas) - 12} más" if len(capas) > 12 else "")))
    for h in hallazgos:
        if str(h.get("detalle")) in ("<Null>", "<null>", "None", ""):
            h["detalle"] = h.get("extra") or h["capa"]
    orden = {"rojo": 0, "ambar": 1, "info": 2}
    hallazgos.sort(key=lambda h: (orden[h["nivel"]], h["capa"]))
    niveles = {h["nivel"] for h in hallazgos}
    if pend and pend["p90"] > 15:
        niveles.add("ambar")
    semaforo = "rojo" if "rojo" in niveles else "ambar" if "ambar" in niveles else "verde"
    return dict(disponible=True, centro=[lon, lat], municipio=municipio, semaforo=semaforo,
                hallazgos=hallazgos, zonas_aptas=zonas_aptas[:5], red_electrica=redes[:8],
                red_cobertura=bool(redes) or _hay_red_en_region(lon, lat),
                pvout=pvout, pendiente=pend)


# --------------------------------------------------------------------------- buscador de lugares
_lugares = None


def _sin_tildes(s):
    import unicodedata
    return unicodedata.normalize("NFD", str(s or "")).encode("ascii", "ignore").decode().lower()


def _indice_lugares():
    """Municipios (código DANE de 5 dígitos) y departamentos (2 dígitos) de la capa MUNICIPIO."""
    global _lugares
    if _lugares is None:
        _lugares = []
        if disponible():
            for aj, x0, y0, x1, y1 in _db().execute(
                    "select attrs_json, xmin, ymin, xmax, ymax from features where dataset='MUNICIPIO'"):
                a = json.loads(aj)
                cod = str(a.get("codigo", ""))
                tipo = "departamento" if len(cod) == 2 else "municipio"
                nombre = a.get("nombre", "")
                depto = a.get("depto", "") if tipo == "municipio" else ""
                _lugares.append(dict(tipo=tipo, nombre=nombre, depto=depto, codigo=cod, bbox=[x0, y0, x1, y1],
                                     _n=_sin_tildes(nombre), _t=_sin_tildes(f"{nombre} {depto}")))
    return _lugares


def municipio_en(lon, lat):
    """Municipio que contiene el punto: dict(codigo, nombre, depto) o None."""
    if not disponible():
        return None
    for aj, gj in _db().execute("select attrs_json, geom_json from features where dataset='MUNICIPIO' "
                                "and xmin<=? and xmax>=? and ymin<=? and ymax>=?", (lon, lon, lat, lat)):
        a = json.loads(aj)
        if len(str(a.get("codigo", ""))) == 5 and any(_pir(lon, lat, anillo) for anillo in json.loads(gj)[:1]):
            return dict(codigo=str(a["codigo"]), nombre=a.get("nombre", ""), depto=a.get("depto", ""))
    return None


def buscar_lugares(q, limite=12):
    q = _sin_tildes(q).replace(",", " ").strip()
    if len(q) < 2:
        return []
    fichas = q.split()
    res = []
    for l in _indice_lugares():
        if all(f in l["_t"] for f in fichas):
            # prioridad: nombre exacto, nombre que empieza por el texto, palabra que empieza, resto
            n = l["_n"]
            rango = 0 if n == q else 1 if n.startswith(q) else 2 if any(p.startswith(fichas[0]) for p in n.split()) else 3
            res.append((rango, l["tipo"] != "departamento", len(n), l))
    res.sort(key=lambda r: r[:3])
    return [{k: v for k, v in l.items() if not k.startswith("_")} for *_, l in res[:limite]]


def geometria_lugar(codigo):
    r = _db().execute("select geom_json from features where dataset='MUNICIPIO' and json_extract(attrs_json, '$.codigo')=?",
                      (str(codigo),)).fetchone()
    if not r:
        return None
    g = json.loads(r[0])
    return dict(type="Polygon", coordinates=[_simplificar(anillo, 1500) for anillo in g])


def _hay_red_en_region(lon, lat, grados=0.5):
    """Distingue 'no hay red cerca' de 'la base no cubre esta region'."""
    r = _db().execute("SELECT 1 FROM features WHERE dataset='RED_ELECTRICA' AND xmax>=? AND xmin<=? "
                      "AND ymax>=? AND ymin<=? LIMIT 1", (lon - grados, lon + grados, lat - grados, lat + grados)).fetchone()
    return r is not None
