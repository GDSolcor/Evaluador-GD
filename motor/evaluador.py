"""
Motor del Evaluador de predios GD.

Flujo:  KMZ/KML  ->  capas clasificadas (construible / exclusion / mesa existente / ignorar)
        ->  grilla de mesas por area construible  ->  capacidad (terreno vs inversor)
        ->  energia estimada  ->  oferta de arriendo.

Reutiliza la proyeccion local y la geometria basica de layout_engine.py (skill
layout-preliminar). Lo nuevo aqui es:
  - parseo por ElementTree conservando carpetas (el parser por regex no ve Folders),
  - chequeo de mesas contra el poligono completo (no solo sus 4 esquinas), para que
    una franja angosta que cruza por el medio de una mesa si la descarte,
  - exclusiones por linea con buffer (canales, redes),
  - busqueda del mejor desfase de la grilla.
"""

import io
import math
import os
import re
import unicodedata
import zipfile
from xml.etree import ElementTree as ET

from layout_engine import LocalProjection, polygon_area



# --------------------------------------------------------------------------
# Presets de mesa
# --------------------------------------------------------------------------

PRESETS = {
    "mavalle_longi_650": dict(
        nombre="Mavalle · LONGi LR8-66HYD-650M · 2V×14",
        modulo_largo=2.382, modulo_ancho=1.134, modulo_wp=650,
        filas=2, columnas=14, sep_columnas=0.02, sep_filas=0.05,
        sep_mesas_eo=0.20, inclinacion=10.0, pasillo=1.90, retiro=0.0, orientacion="vertical", modulo_id="longi_lr8_66hyd_650",
    ),
    "solcor_jinko_730": dict(
        nombre="Estándar SOLCOR · Jinko Tiger Neo 730 · 2V×10",
        modulo_largo=2.384, modulo_ancho=1.303, modulo_wp=730,
        filas=2, columnas=10, sep_columnas=0.20, sep_filas=0.02,
        sep_mesas_eo=0.02, inclinacion=9.0, pasillo=2.80, retiro=3.0, orientacion="vertical", modulo_id="jinko_jkm730n_66hl5",
    ),
}

PARAMS_DEFECTO = dict(
    PRESETS["mavalle_longi_650"],
    ac_por_gd_kw=990.0,          # 3x Huawei SUN2000-330KTL-H1 (tope CREG 174/2021 < 1 MW)
    potencia_inversor_kw=330.0,  # potencia AC de cada inversor -> inversores por GD = ceil(AC por GD / potencia)
    dc_ac_max=1.40,              # ratio DC/AC maximo aceptado por bloque GD
    buffer_lineas=5.0,           # m a cada lado de una linea marcada como exclusion
    ronda_hidrica_m=30.0,        # franja a cada lado de rios, quebradas y cuerpos de agua IGAC (0 = no aplicar)
    margen_exclusion=0.0,        # m adicionales alrededor de poligonos de exclusion
    rendimiento_kwh_kwp=1650.0,  # P50 kWh/kWp/año (Lérida); ajustar con PVsyst o PVOUT
    tarifa_arriendo_ha_mes=2000000.0,  # COP por hectarea al mes (canon Lérida)
    plazo_anios=25,
    base_arriendo="construible",  # construible | util (área útil a arrendar; «total» en evaluaciones antiguas)
    modulos_objetivo=0,          # paneles deseados en el area (0 = llenar el area)
    max_gd=0,                    # numero maximo de GDs (0 = sin limite)
    trafo_kva_gd="",             # capacidad del transformador del CT de cada GD, en orden ("0, 630"): limita su AC (#48)
)


def trafos_ct(valor):
    """«800, , 630» o [800, 0, 630] -> [800.0, 0.0, 630.0] (0 o vacío = sin límite para ese GD)."""
    if isinstance(valor, (int, float)):
        return [float(valor)] if valor > 0 else []
    partes = valor if isinstance(valor, (list, tuple)) else str(valor or "").replace(";", ",").split(",")
    out = []
    for x in partes:
        try:
            v = float(str(x).strip().replace(" ", "")) if str(x).strip() else 0.0
        except ValueError:
            raise ValueError(f"Transformador CT por GD: «{str(x).strip()}» no es un número de kVA.")
        out.append(max(0.0, v))
    while out and out[-1] == 0:
        out.pop()
    return out


# --------------------------------------------------------------------------
# Parseo del KMZ/KML
# --------------------------------------------------------------------------

def _norm(s):
    s = unicodedata.normalize("NFD", s or "").encode("ascii", "ignore").decode()
    return s.lower()


def _read_kml_text(data: bytes) -> str:
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            kmls = [n for n in z.namelist() if n.lower().endswith(".kml")]
            if not kmls:
                raise ValueError("El KMZ no contiene ningún archivo .kml.")
            # Google Earth guarda el principal como doc.kml; los demás suelen ser recursos enlazados
            name = next((n for n in kmls if os.path.basename(n).lower() == "doc.kml"), kmls[0])
            data = z.read(name)
    return data.decode("utf-8", errors="replace")


def _parse_coords(text):
    pts = []
    for tok in (text or "").split():
        parts = tok.split(",")
        if len(parts) >= 2:
            pts.append((float(parts[0]), float(parts[1])))
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    return pts


def parse_features(data: bytes):
    """Devuelve lista de capas: {id, nombre, carpeta, tipo, coords, descripcion}.
    Un Placemark con MultiGeometry produce una capa por geometria."""
    root = ET.fromstring(_read_kml_text(data).encode("utf-8"))
    # sin espacios de nombres: así se leen igual los KML 2.2 de OGC, los de Google Earth antiguo
    # (earth.google.com/kml/2.0–2.2) y los que no declaran ninguno (ticket #44: un KML así salía vacío)
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    out = []

    def walk(el, path):
        for ch in el:
            tag = ch.tag if isinstance(ch.tag, str) else ""
            if tag in ("kml", "Document", "Folder"):
                n = ch.find("name")
                walk(ch, path + [n.text.strip() if n is not None and n.text else ""])
            elif tag == "Placemark":
                n = ch.find("name")
                d = ch.find("description")
                nombre = (n.text or "").strip() if n is not None else ""
                carpeta = " / ".join(p for p in path[1:] if p)
                for geom in ch.iter():
                    gt = geom.tag if isinstance(geom.tag, str) else ""
                    if gt == "Polygon":
                        c = geom.find("outerBoundaryIs/LinearRing/coordinates")
                        tipo = "Polygon"
                    elif gt in ("LineString", "Point"):
                        c = geom.find("coordinates")
                        tipo = gt
                    else:
                        continue
                    coords = _parse_coords(c.text if c is not None else "")
                    if not coords:
                        continue
                    out.append(dict(id=len(out), nombre=nombre or "(sin nombre)", carpeta=carpeta,
                                    tipo=tipo, coords=coords,
                                    descripcion=(d.text or "").strip() if d is not None else ""))

    walk([root] if root.tag != "kml" else root, [])
    return out


# --------------------------------------------------------------------------
# Clasificacion automatica de capas
# --------------------------------------------------------------------------

_RE_MESA = re.compile(r"^[a-z]{0,3}\d{2,5}$")
_KW_CONSTRUIBLE_1 = ("bloque", "area util", "zona util", "construible", "implantacion")
_KW_CONSTRUIBLE_2 = ("cerramiento", "predio", "lote", "poligono", "area", "terreno", "finca")
_KW_EXCLUSION = ("franja", "retiro", "ronda", "exclus", "arbol", "bosque", "humedal",
                 "servidumbre", "restric", "nacimiento", "quebrada", "drenaje", "cuerpo de agua",
                 "via ", "via de", "via interna", "vias", "camino", "acceso")
_KW_ARRIENDO = ("reservar", "reserva", "arrendar", "arriendo", "a arrendar")
_KW_LINEA_EXCL = ("canal", "red ", "linea", "34,5", "34.5", "13,2", "13.2", "rio", "quebrada", "via")


def clasificar(features):
    """Asigna un rol por defecto a cada capa. El usuario lo puede cambiar en la UI."""
    proj = LocalProjection([p for f in features for p in f["coords"]])
    for f in features:
        f["area_m2"] = polygon_area(proj.project_all(f["coords"])) if f["tipo"] == "Polygon" else 0.0
        f["rol"] = "ignorar"

    polys = [f for f in features if f["tipo"] == "Polygon"]
    # Mesas de un layout existente: poligonos pequenos con nombre tipo M001 / R025
    for f in polys:
        if f["area_m2"] < 200 and _RE_MESA.match(_norm(f["nombre"]).replace(" ", "")):
            f["rol"] = "mesa"
    carpetas_diseno = {f["carpeta"] for f in polys if f["rol"] == "mesa"}

    def txt(f):
        return _norm(f["nombre"] + " " + f["carpeta"])

    # área útil a arrendar («Área reservar», «Área a arrendar»): entra en el canon pero no lleva mesas
    for f in polys:
        if f["rol"] == "ignorar" and any(k in _norm(f["nombre"]) for k in _KW_ARRIENDO):
            f["rol"] = "arriendo"
    libres = [f for f in polys if f["rol"] == "ignorar"]
    # solo el nombre: una carpeta "Cerramiento y bloques" no convierte al cerramiento en bloque
    nivel1 = [f for f in libres if any(k in _norm(f["nombre"]) for k in _KW_CONSTRUIBLE_1)]
    nivel2 = [f for f in libres if any(k in _norm(f["nombre"]) for k in _KW_CONSTRUIBLE_2)
              and not any(k in txt(f) for k in _KW_EXCLUSION)]
    construibles = nivel1 or nivel2 or sorted(libres, key=lambda f: -f["area_m2"])[:1]
    for f in construibles:
        f["rol"] = "construible"

    for f in libres:
        if f["rol"] != "ignorar":
            continue
        # lo que cuelga de las carpetas de un GD ya disenado (CT, franja de su red MT)
        # es decision del diseno existente, no una restriccion del predio
        if f["carpeta"] and any(f["carpeta"].startswith(c.split(" / ")[0]) for c in carpetas_diseno):
            continue
        if any(k in _norm(f["nombre"]) + " " for k in _KW_EXCLUSION):
            f["rol"] = "exclusion"

    for f in features:
        if f["tipo"] == "LineString" and f["rol"] == "ignorar":
            en_diseno = f["carpeta"] and any(f["carpeta"].startswith(c.split(" / ")[0]) for c in carpetas_diseno)
            if not en_diseno and any(k in " " + _norm(f["nombre"]) + " " for k in _KW_LINEA_EXCL):
                f["rol"] = "exclusion_linea"
    return features


def desde_poligonos(poligonos):
    """Capas a partir de poligonos dibujados en el mapa o tomados del catastro.
    poligonos: [{nombre, coords [[lon,lat],...], rol}] -> mismo formato que cargar()."""
    feats = []
    for i, p in enumerate(poligonos):
        coords = [tuple(c[:2]) for c in p.get("coords", [])]
        if len(coords) > 1 and coords[0] == coords[-1]:
            coords = coords[:-1]
        if len(coords) < 3:
            continue
        feats.append(dict(id=i, nombre=p.get("nombre") or f"Polígono {i + 1}", carpeta=p.get("origen", "Dibujado en el mapa"),
                          tipo="Polygon", coords=coords, descripcion="", rol=p.get("rol", "construible")))
    if not feats:
        raise ValueError("Dibuja al menos un polígono de 3 vértices.")
    proj = LocalProjection([pt for f in feats for pt in f["coords"]])
    for f in feats:
        f["area_m2"] = polygon_area(proj.project_all(f["coords"]))
    lons = [pt[0] for f in feats for pt in f["coords"]]
    lats = [pt[1] for f in feats for pt in f["coords"]]
    return dict(capas=feats, bbox=[min(lons), min(lats), max(lons), max(lats)], presets=PRESETS, params=PARAMS_DEFECTO)


def cargar(data: bytes):
    feats = parse_features(data)
    if not feats:
        raise ValueError("El archivo no trae polígonos, líneas ni puntos con coordenadas que el evaluador pueda leer "
                         "(por ejemplo, solo trae imágenes superpuestas, rutas GPS o enlaces a otros archivos). "
                         "Ábrelo en Google Earth, guarda el polígono del predio como KMZ y cárgalo de nuevo.")
    feats = clasificar(feats)
    lons = [p[0] for f in feats for p in f["coords"]]
    lats = [p[1] for f in feats for p in f["coords"]]
    return dict(capas=feats, bbox=[min(lons), min(lats), max(lons), max(lats)],
                presets=PRESETS, params=PARAMS_DEFECTO)


# --------------------------------------------------------------------------
# Geometria: rectangulo de mesa vs poligonos / lineas
# --------------------------------------------------------------------------

def _seg_intersect(a, b, c, d):
    def orient(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    o1, o2, o3, o4 = orient(a, b, c), orient(a, b, d), orient(c, d, a), orient(c, d, b)
    return (o1 * o2 < 0) and (o3 * o4 < 0)


def _pt_seg(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def _seg_seg(a, b, c, d):
    if _seg_intersect(a, b, c, d):
        return 0.0
    return min(_pt_seg(a, c, d), _pt_seg(b, c, d), _pt_seg(c, a, b), _pt_seg(d, a, b))


def _edges(pts, closed=True):
    n = len(pts)
    rng = range(n) if closed else range(n - 1)
    return [(pts[i], pts[(i + 1) % n]) for i in rng]


class _Shape:
    """Poligono o linea proyectado, con bbox para descartar rapido e indice de bordes por celdas
    (un predio catastral grande o un drenaje traen cientos de vertices: sin indice, cada mesa
    candidata recorria todos los bordes y la grilla de 200+ ha tardaba minutos)."""

    CEL = 30.0

    def __init__(self, pts, closed):
        self.pts = pts
        self.closed = closed
        self.edges = _edges(pts, closed)
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        self.bb = (min(xs), min(ys), max(xs), max(ys))
        C = self.CEL
        self.celdas, self.franjas = {}, {}
        for k, (a, b) in enumerate(self.edges):
            j0, j1 = int(min(a[1], b[1]) // C), int(max(a[1], b[1]) // C)
            for i in range(int(min(a[0], b[0]) // C), int(max(a[0], b[0]) // C) + 1):
                for j in range(j0, j1 + 1):
                    self.celdas.setdefault((i, j), []).append(k)
            for j in range(j0, j1 + 1):  # franjas horizontales para el punto-en-poligono
                self.franjas.setdefault(j, []).append(k)

    def _bordes_en(self, x0, y0, x1, y1):
        C = self.CEL
        ks = set()
        for i in range(int(x0 // C), int(x1 // C) + 1):
            for j in range(int(y0 // C), int(y1 // C) + 1):
                ks.update(self.celdas.get((i, j), ()))
        return ks

    def dist_rect(self, rect, cutoff):
        """Distancia minima entre el borde de la forma y el borde del rectangulo
        (0 si se cruzan). Devuelve cutoff si esta claramente mas lejos."""
        x0, y0, x1, y1 = rect
        bx0, by0, bx1, by1 = self.bb
        if bx0 - cutoff > x1 or bx1 + cutoff < x0 or by0 - cutoff > y1 or by1 + cutoff < y0:
            return cutoff
        corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        redges = _edges(corners)
        best = cutoff
        for k in self._bordes_en(x0 - cutoff, y0 - cutoff, x1 + cutoff, y1 + cutoff):
            a, b = self.edges[k]
            ex0, ex1 = min(a[0], b[0]) - cutoff, max(a[0], b[0]) + cutoff
            ey0, ey1 = min(a[1], b[1]) - cutoff, max(a[1], b[1]) + cutoff
            if ex0 > x1 or ex1 < x0 or ey0 > y1 or ey1 < y0:
                continue
            for c, d in redges:
                best = min(best, _seg_seg(a, b, c, d))
                if best == 0.0:
                    return 0.0
        return best

    def contains(self, x, y):
        if not self.closed:
            return False
        bx0, by0, bx1, by1 = self.bb
        if x < bx0 or x > bx1 or y < by0 or y > by1:
            return False
        dentro = False  # rayo hacia +x, solo con los bordes de la franja de y
        for k in self.franjas.get(int(y // self.CEL), ()):
            (xa, ya), (xb, yb) = self.edges[k]
            if (ya > y) != (yb > y) and x < (xb - xa) * (y - ya) / (yb - ya) + xa:
                dentro = not dentro
        return dentro


def _rect_ok(rect, area, exclusiones, retiro):
    x0, y0, x1, y1 = rect
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    if not all(area.contains(x, y) for x, y in corners):
        return False
    if area.dist_rect(rect, retiro + 1e-6) < max(retiro, 1e-6):
        return False  # cruza el lindero (poligono concavo) o invade el retiro
    for shp, margen in exclusiones:
        if shp.dist_rect(rect, margen + 1e-6) < max(margen, 1e-6):
            return False
        bx0, by0, bx1, by1 = shp.bb
        if shp.closed and not (bx0 > x1 or bx1 < x0 or by0 > y1 or by1 < y0) and \
                (any(shp.contains(x, y) for x, y in corners)
                 or any(x0 <= px <= x1 and y0 <= py <= y1 for px, py in shp.pts)):
            return False
    return True


# --------------------------------------------------------------------------
# Layout y evaluacion
# --------------------------------------------------------------------------

def lados_modulo(p):
    """(lado a lo ancho de la mesa E-O, lado en pendiente N-S) del modulo segun la orientacion.
    Vertical (retrato, "2V"): el lado largo va en la pendiente. Horizontal (paisaje, "2H"): el corto."""
    if str(p.get("orientacion", "vertical")).lower().startswith("h"):
        return p["modulo_largo"], p["modulo_ancho"]
    return p["modulo_ancho"], p["modulo_largo"]


def dimensiones_mesa(p):
    lado_eo, lado_ns = lados_modulo(p)
    prof_modulo = lado_ns * math.cos(math.radians(p["inclinacion"]))
    prof = p["filas"] * prof_modulo + (p["filas"] - 1) * p["sep_filas"]
    ancho = p["columnas"] * lado_eo + (p["columnas"] - 1) * p["sep_columnas"]
    return ancho, prof


def _recortar_mesas(mesas_xy, n, ancla=None):
    """Se queda con n mesas compactas: las mas cercanas al ancla (punto de conexion) o al centro."""
    if n >= len(mesas_xy):
        return list(range(len(mesas_xy)))
    cx = [(r[0] + r[2]) / 2 for r in mesas_xy]
    cy = [(r[1] + r[3]) / 2 for r in mesas_xy]
    ax, ay = ancla if ancla else (sum(cx) / len(cx), sum(cy) / len(cy))
    orden = sorted(range(len(mesas_xy)), key=lambda i: (cx[i] - ax) ** 2 + (cy[i] - ay) ** 2)
    return sorted(orden[:n])


def _topes(tope, n_gd):
    """Tope de mesas de cada bloque: un número para todos o una lista por GD (#48)."""
    return list(tope[:n_gd]) + [tope[-1]] * (n_gd - len(tope)) if isinstance(tope, (list, tuple)) else [tope] * n_gd


def _reparto_por_filas(filas, n_gd, tope):
    """Parte la secuencia de filas físicas (cantidad de mesas de cada una) en n_gd bloques contiguos
    de máximo `tope` mesas (un número, o una lista con el tope de cada GD), lo más parejos posible
    en proporción a su tope. Devuelve cuántas filas lleva cada bloque, o None si no se puede
    (p. ej. una sola fila ya supera el tope)."""
    n = len(filas)
    topes = _topes(tope, n_gd) if n_gd >= 1 else []
    if n_gd < 1 or n < n_gd or max(filas) > max(topes):
        return None
    acum = [0]
    for f in filas:
        acum.append(acum[-1] + f)
    metas = [acum[-1] * t / sum(topes) for t in topes]
    INF = float("inf")
    costo = [[INF] * (n + 1) for _ in range(n_gd + 1)]
    corte = [[0] * (n + 1) for _ in range(n_gd + 1)]
    costo[0][0] = 0.0
    for g in range(1, n_gd + 1):
        tope_g, meta = topes[g - 1], metas[g - 1]
        for j in range(g, n + 1):
            for i in range(j - 1, g - 2, -1):
                s = acum[j] - acum[i]
                if s > tope_g:
                    break
                if costo[g - 1][i] < INF and costo[g - 1][i] + (s - meta) ** 2 < costo[g][j]:
                    costo[g][j] = costo[g - 1][i] + (s - meta) ** 2
                    corte[g][j] = i
    if costo[n_gd][n] == INF:
        return None
    cortes, j = [], n
    for g in range(n_gd, 0, -1):
        cortes.append(j - corte[g][j])
        j = corte[g][j]
    return cortes[::-1]


def _reparto_recortando(filas, n_gd, tope):
    """Cuando no hay corte por filas completas que respete los topes (p. ej. con máximo de GDs y cada GD lleno
    hasta su tope), cada bloque toma filas completas contiguas y deja sin usar las mesas que pasan de su tope:
    así dos GD nunca comparten fila (#49). Maximiza las mesas conservadas y, a igualdad, el equilibrio entre
    bloques. Devuelve [(filas del bloque, mesas que conserva)] o None."""
    n = len(filas)
    if n_gd < 1 or n < n_gd:
        return None
    topes = _topes(tope, n_gd)
    acum = [0]
    for f in filas:
        acum.append(acum[-1] + f)
    metas = [min(t, acum[-1] * t / sum(topes)) for t in topes]
    PEOR = (float("inf"), float("inf"))
    mejor = [[PEOR] * (n + 1) for _ in range(n_gd + 1)]
    corte = [[0] * (n + 1) for _ in range(n_gd + 1)]
    mejor[0][0] = (0, 0.0)
    for g in range(1, n_gd + 1):
        for j in range(g, n + 1):
            for i in range(g - 1, j):
                if mejor[g - 1][i] == PEOR:
                    continue
                k = min(acum[j] - acum[i], topes[g - 1])
                if k <= 0:
                    continue
                c = (mejor[g - 1][i][0] - k, mejor[g - 1][i][1] + (k - metas[g - 1]) ** 2)  # (-conservadas, desequilibrio)
                if c < mejor[g][j]:
                    mejor[g][j], corte[g][j] = c, i
    if mejor[n_gd][n] == PEOR:
        return None
    bloques, j = [], n
    for g in range(n_gd, 0, -1):
        i = corte[g][j]
        bloques.append((j - i, min(acum[j] - acum[i], topes[g - 1])))
        j = i
    return bloques[::-1]


def _grilla(area, exclusiones, p, ancho, prof, dx, dy):
    paso_x = ancho + p["sep_mesas_eo"]
    paso_y = prof + p["pasillo"]
    x_min, y_min, x_max, y_max = area.bb
    mesas = []
    y = y_min + dy
    while y + prof <= y_max + 1e-6:
        x = x_min + dx
        while x + ancho <= x_max + 1e-6:
            r = (x, y, x + ancho, y + prof)
            if _rect_ok(r, area, exclusiones, p["retiro"]):
                mesas.append(r)
            x += paso_x
        y += paso_y
    return mesas


def _filas(area, exclusiones, p, ancho, prof, dy, fino=0.5):
    """Como _grilla, pero cada fila se acomoda por separado: la mesa se corre de a `fino` metros
    hasta que cabe y desde ahí se sigue con el paso normal. En polígonos irregulares aprovecha
    los rincones donde la columna fija de la grilla dejaba una esquina por fuera del lindero."""
    paso_x = ancho + p["sep_mesas_eo"]
    paso_y = prof + p["pasillo"]
    x_min, y_min, x_max, y_max = area.bb
    mesas = []
    y = y_min + dy
    while y + prof <= y_max + 1e-6:
        x = x_min
        while x + ancho <= x_max + 1e-6:
            r = (x, y, x + ancho, y + prof)
            if _rect_ok(r, area, exclusiones, p["retiro"]):
                mesas.append(r)
                x += paso_x
            else:
                x += fino
        y += paso_y
    return mesas


def layout_area(area, exclusiones, p, pasos=None):
    """Prueba pasos x pasos desfases de la grilla y se queda con el que mas mesas da.
    En áreas grandes el desfase casi no cambia el total (<1 %), así que se prueban menos.
    En áreas de menos de 30 ha además se prueba el acomodo fila por fila (_filas)."""
    ha = polygon_area(area.pts) / 10000.0
    if pasos is None:
        pasos = 6 if ha < 30 else 4 if ha < 100 else 3
    ancho, prof = dimensiones_mesa(p)
    paso_x = ancho + p["sep_mesas_eo"]
    paso_y = prof + p["pasillo"]
    mejor = []
    if ha < 30:
        for j in range(pasos * 2):
            m = _filas(area, exclusiones, p, ancho, prof, paso_y * j / (pasos * 2))
            if len(m) > len(mejor):
                mejor = m
    for i in range(pasos):
        for j in range(pasos):
            m = _grilla(area, exclusiones, p, ancho, prof, paso_x * i / pasos, paso_y * j / pasos)
            if len(m) > len(mejor):
                mejor = m
    return mejor


def evaluar(capas, params):
    p = dict(PARAMS_DEFECTO)
    p.update({k: v for k, v in (params or {}).items() if v is not None})
    for k, v in p.items():
        if k not in ("nombre", "base_arriendo", "orientacion", "modulo_id", "modulo_nombre", "trafo_kva_gd") and isinstance(v, str):
            p[k] = float(v.replace(",", ".")) if v.strip() else 0.0
    p["filas"], p["columnas"] = int(p["filas"]), int(p["columnas"])
    if p["filas"] < 1 or p["columnas"] < 1:
        raise ValueError("La mesa necesita al menos 1 fila y 1 columna de módulos.")
    p["orientacion"] = "horizontal" if str(p.get("orientacion", "vertical")).lower().startswith("h") else "vertical"
    p["modulos_objetivo"] = int(float(p.get("modulos_objetivo") or 0))
    p["max_gd"] = int(float(p.get("max_gd") or 0))

    todos = [pt for c in capas for pt in c["coords"]]
    proj = LocalProjection(todos)

    def shape(c, closed):
        return _Shape(proj.project_all(c["coords"]), closed)

    exclusiones = []
    for c in capas:
        # "margen" propio: elementos del proyecto (CT, vía, árboles…) traen su propio retiro
        if c["rol"] == "exclusion" and c["tipo"] == "Polygon":
            exclusiones.append((shape(c, True), float(c.get("margen", p["margen_exclusion"]))))
        elif c["rol"] in ("exclusion", "exclusion_linea") and c["tipo"] == "LineString":
            exclusiones.append((shape(c, False), float(c.get("margen", p["buffer_lineas"]))))
        elif c["rol"] == "ronda" and p["ronda_hidrica_m"] > 0:
            exclusiones.append((shape(c, c["tipo"] == "Polygon"), p["ronda_hidrica_m"]))

    modulos_mesa = p["filas"] * p["columnas"]
    kwp_mesa = modulos_mesa * p["modulo_wp"] / 1000.0
    ancho, prof = dimensiones_mesa(p)

    areas_out, mesas_ll, rects = [], [], []
    colocadas = {}  # celdas de 50 m -> mesas ya colocadas (para no duplicar en áreas traslapadas)

    def _cercanas(idx, r):
        cx0, cy0, cx1, cy1 = int(r[0] // 50) - 1, int(r[1] // 50) - 1, int(r[2] // 50) + 1, int(r[3] // 50) + 1
        for i in range(cx0, cx1 + 1):
            for j in range(cy0, cy1 + 1):
                yield from idx.get((i, j), ())

    vistos = set()
    for c in capas:
        if c["rol"] != "construible" or c["tipo"] != "Polygon" or len(c["coords"]) < 3:
            continue
        firma = tuple((round(x, 7), round(y, 7)) for x, y in c["coords"])
        if firma in vistos:  # mismo polígono repetido: no sumar su área dos veces
            continue
        vistos.add(firma)
        shp = shape(c, True)
        mesas = layout_area(shp, exclusiones, p)
        # si las áreas construibles se traslapan (p. ej. el mismo predio agregado dos veces o un
        # polígono dibujado encima de un predio), no repetir mesas donde ya hay una colocada
        mesas = [r for r in mesas if not any(r[0] < q[2] and q[0] < r[2] and r[1] < q[3] and q[1] < r[3]
                                             for q in _cercanas(colocadas, r))]
        for r in mesas:
            colocadas.setdefault((int(r[0] // 50), int(r[1] // 50)), []).append(r)
            x0, y0, x1, y1 = r
            rects.append(r)
            mesas_ll.append(dict(area=c["nombre"], coords=[proj.to_lonlat(x, y) for x, y in
                                                            [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]]))
        areas_out.append(dict(nombre=c["nombre"], area_m2=polygon_area(shp.pts), mesas=len(mesas),
                              kwp=len(mesas) * kwp_mesa))

    dc_max_gd = p["ac_por_gd_kw"] * p["dc_ac_max"]
    mesas_max_gd = int(dc_max_gd // kwp_mesa) if kwp_mesa else 0
    # transformador del CT de cada GD (#48): limita la AC de ese bloque y, con el DC/AC máximo, sus mesas
    kva_gd = trafos_ct(p.get("trafo_kva_gd"))
    ac_gd = lambda g: min(p["ac_por_gd_kw"], kva_gd[g]) if g < len(kva_gd) and kva_gd[g] > 0 else p["ac_por_gd_kw"]  # noqa: E731
    mesas_gd = lambda g: int(ac_gd(g) * p["dc_ac_max"] // kwp_mesa) if kwp_mesa else 0  # noqa: E731
    # limites del usuario: cantidad de paneles deseada y numero maximo de GDs
    disponibles = len(mesas_ll)
    limites = []
    if p["modulos_objetivo"] > 0:
        limites.append((math.ceil(p["modulos_objetivo"] / modulos_mesa), "paneles objetivo"))
    if p["max_gd"] > 0 and mesas_max_gd:
        limites.append((sum(mesas_gd(g) for g in range(int(p["max_gd"]))), "máximo de GDs"))
    limitado_por = None
    if limites:
        n_lim, limitado_por = min(limites)
        if n_lim < disponibles:
            ancla = p.get("ancla")
            ancla_xy = proj.to_xy(float(ancla[0]), float(ancla[1])) if isinstance(ancla, (list, tuple)) and len(ancla) == 2 else None
            keep = _recortar_mesas(rects, n_lim, ancla_xy)
            mesas_ll = [mesas_ll[i] for i in keep]
            rects = [rects[i] for i in keep]
            for a in areas_out:
                a["mesas"] = sum(1 for m in mesas_ll if m["area"] == a["nombre"])
                a["kwp"] = a["mesas"] * kwp_mesa
        else:
            limitado_por = None

    n_mesas = len(mesas_ll)
    dc_terreno = n_mesas * kwp_mesa
    # Reparto en bloques GD: el minimo numero de GD que respeta el tope DC por bloque,
    # con las mesas repartidas lo mas parejo posible (como Mavalle: 3 x 72)
    n_gd = 0
    if mesas_max_gd and n_mesas:
        while sum(mesas_gd(g) for g in range(n_gd)) < n_mesas and n_gd < n_mesas:
            n_gd += 1
    topes = [mesas_gd(g) for g in range(n_gd + 1)]  # uno de más: por si hay que probar con otro GD
    if n_gd and len(set(topes[:n_gd])) == 1:
        reparto = [n_mesas // n_gd + (1 if i < n_mesas % n_gd else 0) for i in range(n_gd)]
    elif n_gd:
        # topes distintos: reparto proporcional al tope de cada GD, sin pasarlo
        reparto = [min(topes[g], int(n_mesas * topes[g] / sum(topes[:n_gd]))) for g in range(n_gd)]
        g = 0
        while sum(reparto) < n_mesas:
            if reparto[g % n_gd] < topes[g % n_gd]:
                reparto[g % n_gd] += 1
            g += 1
    else:
        reparto = []
    # Dos GD no comparten fila física: el corte entre bloques cae siempre al terminar una fila.
    # Si con el mínimo de GD no hay corte posible, se prueba con uno más (cuando el usuario no lo limita).
    filas_fis = []  # mesas de cada fila, en el orden del layout (área y luego sur → norte)
    for k, (m, r) in enumerate(zip(mesas_ll, rects)):
        clave = (m["area"], round(r[1], 2))
        if k and clave == filas_fis[-1][0]:
            filas_fis[-1][1] += 1
        else:
            filas_fis.append([clave, 1])
    filas_compartidas = False
    quitadas_fila = 0
    if n_gd > 1:
        tam = [f[1] for f in filas_fis]
        otro_gd = p["max_gd"] <= 0 or n_gd + 1 <= p["max_gd"]
        por_filas = _reparto_por_filas(tam, n_gd, topes[:n_gd])
        if por_filas is None and otro_gd:
            por_filas = _reparto_por_filas(tam, n_gd + 1, topes[:n_gd + 1])
        if por_filas:
            n_gd, reparto, i = len(por_filas), [], 0
            for nf in por_filas:
                reparto.append(sum(tam[i:i + nf]))
                i += nf
        else:
            # sin corte exacto: cada GD toma filas completas hasta su tope y se dejan libres las mesas que sobran
            # en la fila del corte, para que nunca compartan fila (#49)
            opciones = [b for b in (_reparto_recortando(tam, n_gd, topes[:n_gd]),
                                    _reparto_recortando(tam, n_gd + 1, topes[:n_gd + 1]) if otro_gd else None) if b]
            bloques = max(opciones, key=lambda b: (sum(k for _, k in b), -len(b))) if opciones else None
            if bloques:
                keep, i, inicio = [], 0, [0]
                for f in tam:
                    inicio.append(inicio[-1] + f)
                for nf, k in bloques:
                    keep += list(range(inicio[i], inicio[i + nf]))[:k]
                    i += nf
                quitadas_fila = len(mesas_ll) - len(keep)
                mesas_ll = [mesas_ll[q] for q in keep]
                rects = [rects[q] for q in keep]
                n_gd, reparto = len(bloques), [k for _, k in bloques]
                n_mesas = len(mesas_ll)
                dc_terreno = n_mesas * kwp_mesa
                for a in areas_out:
                    a["mesas"] = sum(1 for m in mesas_ll if m["area"] == a["nombre"])
                    a["kwp"] = a["mesas"] * kwp_mesa
            else:
                filas_compartidas = True
    gd_mesa = [g for g, m in enumerate(reparto) for _ in range(m)]
    for m, g in zip(mesas_ll, gd_mesa):
        m["gd"] = g
    pot_inv = p["potencia_inversor_kw"] if p.get("potencia_inversor_kw", 0) > 0 else 330.0
    gds = [dict(mesas=m, dc_kwp=m * kwp_mesa, ac_kw=min(ac_gd(g), m * kwp_mesa),
                dc_ac=m * kwp_mesa / ac_gd(g) if ac_gd(g) else 0.0,
                inversores=max(1, math.ceil(min(ac_gd(g), m * kwp_mesa) / pot_inv - 1e-9)),
                trafo_kva=kva_gd[g] if g < len(kva_gd) and kva_gd[g] > 0 else None)
           for g, m in enumerate(reparto)]
    ac_total = sum(g["ac_kw"] for g in gds)

    area_construible = sum(a["area_m2"] for a in areas_out)
    # área útil a arrendar: capas con rol «arriendo» (p. ej. «Área reservar», que incluye franjas no construibles
    # que el propietario no quiere dejar sueltas); si no hay, un polígono «cerramiento»; si tampoco, la construible
    util = [c for c in capas if c["tipo"] == "Polygon" and c["rol"] == "arriendo"] \
        or [c for c in capas if c["tipo"] == "Polygon" and "cerramiento" in _norm(c["nombre"])]
    area_util = sum(polygon_area(proj.project_all(c["coords"])) for c in util) if util else area_construible
    # «total» es el nombre anterior de la opción (evaluaciones guardadas antes del 19-oct-2026)
    area_base = area_construible if p["base_arriendo"] == "construible" else area_util
    # el área a arrendar es la que se negocia con el propietario: no se recorta cuando el layout queda limitado
    # por el máximo de GDs o de paneles
    ha = area_base / 10000.0
    arriendo_mes = ha * p["tarifa_arriendo_ha_mes"]

    existentes = [c for c in capas if c["rol"] == "mesa"]

    return dict(
        params=p,
        mesa=dict(ancho=ancho, profundidad=prof, modulos=modulos_mesa, kwp=kwp_mesa),
        areas=areas_out,
        gds=gds,
        mesas=mesas_ll,
        resumen=dict(
            mesas=n_mesas, modulos=n_mesas * modulos_mesa, dc_kwp=dc_terreno,
            dc_max_por_gd=dc_max_gd, mesas_max_por_gd=mesas_max_gd, n_gd=n_gd,
            ac_kw_total=ac_total,
            energia_mwh_anio=dc_terreno * p["rendimiento_kwh_kwp"] / 1000.0,
            densidad_kwp_ha=dc_terreno / (area_construible / 10000.0) if area_construible else 0.0,
            area_construible_ha=area_construible / 10000.0, area_total_ha=area_util / 10000.0,
            area_util_ha=area_util / 10000.0, area_util_capas=len(util),
            area_arriendo_ha=ha, arriendo_mes=arriendo_mes, arriendo_anio=arriendo_mes * 12,
            arriendo_contrato=arriendo_mes * 12 * p["plazo_anios"],
            mesas_existentes=len(existentes),
            mesas_disponibles=disponibles, modulos_disponibles=disponibles * modulos_mesa,
            limitado_por=limitado_por, modulos_objetivo=p["modulos_objetivo"], max_gd=p["max_gd"],
            filas_fisicas=len(filas_fis), filas_compartidas=filas_compartidas, mesas_quitadas_por_fila=quitadas_fila,
        ),
    )
