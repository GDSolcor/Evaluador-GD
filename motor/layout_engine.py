"""
Motor geometrico reutilizable para la skill layout-preliminar (SOLCOR).

Uso tipico dentro de una conversacion:

    import sys
    sys.path.insert(0, "/mnt/skills/user/layout-preliminar/scripts")
    from layout_engine import *

    placemarks = parse_kml_or_kmz("/mnt/user-data/uploads/GD_Predio.kml")
    # ... clasificar placemarks segun el Paso 2 del SKILL.md ...
    proj = LocalProjection(all_lonlat_points)
    layout = generate_layout(
        build_poly_xy, tree_polys_xy=[...], river_cut=None,
        trafo_point_xy=conn_xy, pitch_ns=2.8, setback=3.0,
        target_panels=1880,
    )
    write_kml(layout, proj, "/mnt/user-data/outputs/Layout.kml", area_name="Predio")

Este script no asume nada sobre nombres de archivo especificos de un proyecto;
toda la logica de "que polígono es cual" vive en el SKILL.md y se decide en la
conversacion, no aqui. Aqui solo esta el algebra: parseo, proyeccion, generacion
de grilla y escritura de KML.
"""

import re
import math
import zipfile
import io


# --------------------------------------------------------------------------
# Parseo de KML / KMZ (incluye archivos .kml renombrados a .txt)
# --------------------------------------------------------------------------

def parse_kml_or_kmz(path):
    """Devuelve una lista de dicts: {name, geom_type, coords} por cada Placemark.
    coords es una lista de (lon, lat) para Point/LineString, o de (lon,lat) del
    anillo exterior para Polygon.
    Detecta automaticamente si el archivo es en realidad un .kmz (zip) aunque
    tenga extension .kml o .txt.
    """
    with open(path, "rb") as f:
        head = f.read(4)
    if head[:2] == b"PK":
        with zipfile.ZipFile(path) as z:
            kml_name = next(n for n in z.namelist() if n.lower().endswith(".kml"))
            kml_text = z.read(kml_name).decode("utf-8", errors="replace")
    else:
        with open(path, encoding="utf-8", errors="replace") as f:
            kml_text = f.read()

    pms = re.findall(r"<Placemark>(.*?)</Placemark>", kml_text, re.S)
    out = []
    for pm in pms:
        namem = re.search(r"<name>(.*?)</name>", pm, re.S)
        name = namem.group(1).strip() if namem else "(sin nombre)"
        if "<Polygon>" in pm:
            geom_type = "Polygon"
        elif "<Point>" in pm:
            geom_type = "Point"
        elif "<LineString>" in pm:
            geom_type = "LineString"
        else:
            geom_type = "?"
        coordm = re.search(r"<coordinates>\s*(.*?)\s*</coordinates>", pm, re.S)
        coords = []
        if coordm:
            for tok in coordm.group(1).split():
                parts = tok.split(",")
                coords.append((float(parts[0]), float(parts[1])))
        out.append(dict(name=name, geom_type=geom_type, coords=coords))
    return out


# --------------------------------------------------------------------------
# Proyeccion local (plano tangente) lon/lat <-> metros
# --------------------------------------------------------------------------

class LocalProjection:
    R = 111320.0

    def __init__(self, lonlat_points):
        self.lat0 = sum(p[1] for p in lonlat_points) / len(lonlat_points)
        self.lon0 = sum(p[0] for p in lonlat_points) / len(lonlat_points)
        self.coslat0 = math.cos(math.radians(self.lat0))

    def to_xy(self, lon, lat):
        return (
            (lon - self.lon0) * self.R * self.coslat0,
            (lat - self.lat0) * self.R,
        )

    def to_lonlat(self, x, y):
        return (self.lon0 + x / (self.R * self.coslat0), self.lat0 + y / self.R)

    def project_all(self, lonlat_list):
        return [self.to_xy(*p) for p in lonlat_list]


# --------------------------------------------------------------------------
# Geometria basica
# --------------------------------------------------------------------------

def polygon_area(poly_xy):
    s = 0.0
    n = len(poly_xy)
    for i in range(n):
        x1, y1 = poly_xy[i]
        x2, y2 = poly_xy[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2


def point_in_polygon(x, y, poly_xy):
    n = len(poly_xy)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = poly_xy[i]
        xj, yj = poly_xy[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi + 1e-15) + xi:
            inside = not inside
        j = i
    return inside


def _point_seg_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0, min(1, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def dist_to_boundary(x, y, poly_xy):
    n = len(poly_xy)
    dmin = float("inf")
    for i in range(n):
        ax, ay = poly_xy[i]
        bx, by = poly_xy[(i + 1) % n]
        d = _point_seg_dist(x, y, ax, ay, bx, by)
        dmin = min(dmin, d)
    return dmin


def bbox(poly_xy):
    xs = [p[0] for p in poly_xy]
    ys = [p[1] for p in poly_xy]
    return min(xs), max(xs), min(ys), max(ys)


def clip_polygon_halfplane(poly_xy, line_a, line_b, keep_point):
    """Recorta poly_xy contra la recta line_a-line_b, quedandose con el lado
    donde esta keep_point (un punto conocido del lado a conservar, p. ej. el
    centroide del predio). Usado para el recorte por retiro de rio cuando la
    linea SI cruza por dentro del poligono."""

    def side(px, py):
        return (line_b[0] - line_a[0]) * (py - line_a[1]) - (line_b[1] - line_a[1]) * (px - line_a[0])

    s_keep = side(*keep_point)

    def keep(x, y):
        return side(x, y) * s_keep >= 0

    def intersect(p1, p2):
        s1, s2 = side(*p1), side(*p2)
        t = s1 / (s1 - s2)
        return (p1[0] + t * (p2[0] - p1[0]), p1[1] + t * (p2[1] - p1[1]))

    out = []
    n = len(poly_xy)
    for i in range(n):
        curr, prev = poly_xy[i], poly_xy[i - 1]
        curr_in, prev_in = keep(*curr), keep(*prev)
        if curr_in:
            if not prev_in:
                out.append(intersect(prev, curr))
            out.append(curr)
        elif prev_in:
            out.append(intersect(prev, curr))
    return out


# --------------------------------------------------------------------------
# Pitch a partir de la pendiente (cuando Jonatan no da el numero directo)
# --------------------------------------------------------------------------

def worst_case_sun_elevation(lat_deg):
    """Elevacion solar minima al mediodia (peor caso entre los 2 solsticios),
    para un arreglo orientado al sur en el hemisferio norte."""
    elev_dec = 90 - abs(lat_deg - (-23.45))
    elev_jun = 90 - abs(lat_deg - 23.45)
    return min(elev_dec, elev_jun)


def pitch_from_slope(lat_deg, slope_pct_ns, tilt_deg=9.0, walkway=2.8,
                      pitch_min=2.8, pitch_max=3.5):
    """Calcula el pasillo teorico ajustado por pendiente norte-sur (slope_pct_ns,
    positivo si el terreno SUBE hacia el norte -- caso adverso para sombra) y
    lo acota entre pitch_min y pitch_max, que son los limites que Jonatan
    definio como realistas para SOLCOR (por defecto 2.8 m en terreno plano)."""
    beta = worst_case_sun_elevation(lat_deg)
    l_slope = PANEL_L * N_ROWS  # profundidad total de la mesa (2 paneles verticales apilados)
    h = l_slope * math.sin(math.radians(tilt_deg))
    w_proj = l_slope * math.cos(math.radians(tilt_deg))
    phi_deg = -abs(slope_pct_ns)  # tratamos la pendiente como adversa (conservador)
    tb = math.tan(math.radians(beta))
    tf = math.tan(math.radians(math.degrees(math.atan(phi_deg / 100.0))))
    theoretical_min = (h + w_proj * tb) / (tb + tf)
    pitch = theoretical_min + walkway
    return max(pitch_min, min(pitch_max, round(pitch, 3))), w_proj


# --------------------------------------------------------------------------
# Generacion de la grilla de mesas
#
# Configuracion de mesa (definida por Jonatan, ver dibujo de referencia):
#   - Paneles en VERTICAL (portrait), lado largo (2384 mm) orientado
#     norte-sur; el lado corto (1303 mm) queda horizontal (este-oeste).
#   - Cada mesa = 2 filas (norte-sur, un panel sobre otro) x 10 columnas
#     (este-oeste) = 20 paneles.
#   - Separacion entre columnas (paneles lado a lado en la misma fila): 0.20 m
#   - Separacion entre las 2 filas apiladas dentro de la misma mesa: 0.02 m
#     (holgura mecanica minima, no especificada por Jonatan -- ajustar si da
#     otro valor)
#   - Separacion entre MESAS contiguas en la misma banda este-oeste: 0.02 m
#     (idem, asuncion; el "pasillo" de 2.8-3.5 m es solo norte-sur)
# --------------------------------------------------------------------------

PANEL_L = 2.384   # lado largo del panel (m) -- ahora orientado norte-sur
PANEL_W = 1.303   # lado corto del panel (m) -- ahora orientado este-oeste
N_COLS = 10
N_ROWS = 2
COL_GAP = 0.20    # separacion horizontal entre paneles de la misma fila
ROW_GAP = 0.02    # separacion entre las 2 filas apiladas de una mesa (asuncion)
MESA_GAP_EW = 0.02  # separacion entre mesas contiguas este-oeste (asuncion)


def generate_layout(build_poly_xy, pitch_ns, setback=3.0,
                     tree_polys_xy=None, tree_margin=1.5,
                     trafo_point_xy=None, trafo_half=2.5, trafo_margin=1.0,
                     tilt_deg=9.0, target_panels=1880, panel_w=730,
                     n_cols=N_COLS, n_rows=N_ROWS, col_gap=COL_GAP, row_gap=ROW_GAP,
                     mesa_gap_ew=MESA_GAP_EW):
    """Genera la grilla de mesas de n_rows x n_cols paneles (por defecto 2x10
    = 20, en vertical norte-sur) dentro de build_poly_xy. pitch_ns es el
    pasillo libre entre el borde norte de una mesa y el borde sur de la
    siguiente (no incluye la profundidad de la mesa misma).
    Devuelve dict con: all_tables (todas las validas), selected (recortadas a
    target_panels si aplica), panels (cada mesa partida en sus paneles
    individuales), trafo_corners, summary.
    """
    tree_polys_xy = tree_polys_xy or []
    panel_depth = PANEL_L * math.cos(math.radians(tilt_deg))  # profundidad N-S de 1 panel, proyectada
    mesa_depth = n_rows * panel_depth + (n_rows - 1) * row_gap
    mesa_width = n_cols * PANEL_W + (n_cols - 1) * col_gap
    step_ew = mesa_width + mesa_gap_ew
    step_ns = mesa_depth + pitch_ns

    trafo_corners = None
    if trafo_point_xy is not None:
        tx, ty = trafo_point_xy
        cxp = sum(p[0] for p in build_poly_xy) / len(build_poly_xy)
        cyp = sum(p[1] for p in build_poly_xy) / len(build_poly_xy)
        dirx, diry = cxp - tx, cyp - ty
        norm = math.hypot(dirx, diry) or 1.0
        dirx, diry = dirx / norm, diry / norm

        def pad_ok(cx, cy):
            corners = [(cx - trafo_half, cy - trafo_half), (cx + trafo_half, cy - trafo_half),
                       (cx + trafo_half, cy + trafo_half), (cx - trafo_half, cy + trafo_half)]
            for x, y in corners:
                if not point_in_polygon(x, y, build_poly_xy):
                    return False
                if dist_to_boundary(x, y, build_poly_xy) < trafo_margin:
                    return False
            return True

        shift, step, cx, cy = 0.0, 0.3, tx, ty
        while shift < 60 and not pad_ok(cx, cy):
            shift += step
            cx, cy = tx + dirx * shift, ty + diry * shift
        trafo_corners = [(cx - trafo_half, cy - trafo_half), (cx + trafo_half, cy - trafo_half),
                          (cx + trafo_half, cy + trafo_half), (cx - trafo_half, cy + trafo_half)]

    def in_trafo(x, y):
        if trafo_corners is None:
            return False
        txs = [c[0] for c in trafo_corners]
        tys = [c[1] for c in trafo_corners]
        return (min(txs) - trafo_margin <= x <= max(txs) + trafo_margin and
                min(tys) - trafo_margin <= y <= max(tys) + trafo_margin)

    def near_tree(x, y):
        for tp in tree_polys_xy:
            if point_in_polygon(x, y, tp) or dist_to_boundary(x, y, tp) < tree_margin:
                return True
        return False

    minx, maxx, miny, maxy = bbox(build_poly_xy)
    all_tables = []
    y = miny
    while y + mesa_depth <= maxy + 1e-6:
        x = minx
        while x + mesa_width <= maxx + 1e-6:
            corners = [(x, y), (x + mesa_width, y), (x + mesa_width, y + mesa_depth), (x, y + mesa_depth)]
            ok = True
            for cx, cy in corners:
                if not point_in_polygon(cx, cy, build_poly_xy):
                    ok = False; break
                if dist_to_boundary(cx, cy, build_poly_xy) < setback:
                    ok = False; break
                if in_trafo(cx, cy):
                    ok = False; break
                if near_tree(cx, cy):
                    ok = False; break
            if ok:
                all_tables.append(corners)
            x += step_ew
        y += step_ns

    def sort_key(t):
        cx = sum(c[0] for c in t) / 4
        cy = sum(c[1] for c in t) / 4
        return (round(cy, 1), cx)

    all_tables.sort(key=sort_key)
    panels_per_mesa = n_rows * n_cols
    target_tables = round(target_panels / panels_per_mesa) if target_panels else None
    selected = all_tables[:target_tables] if target_tables else all_tables

    n_modules = len(selected) * panels_per_mesa
    dc_kwp = n_modules * panel_w / 1000
    summary = dict(
        available_tables=len(all_tables),
        n_tables=len(selected),
        n_modules=n_modules,
        dc_kwp=dc_kwp,
        panels_requested=target_panels,
        panels_per_mesa=panels_per_mesa,
        panels_short_by=max(0, (target_panels or 0) - n_modules),
        mesa_width=mesa_width, mesa_depth=mesa_depth,
    )
    panels = split_tables_into_panels(selected, n_cols=n_cols, n_rows=n_rows,
                                       col_gap=col_gap, row_gap=row_gap)
    return dict(all_tables=all_tables, selected=selected, panels=panels,
                trafo_corners=trafo_corners, summary=summary)


def split_tables_into_panels(tables, n_cols=N_COLS, n_rows=N_ROWS, col_gap=COL_GAP, row_gap=ROW_GAP):
    """Parte cada mesa (bloque de n_rows x n_cols paneles verticales) en sus
    paneles individuales, para que el KML muestre un cuadrito por panel real,
    como el ejemplo de Sutatausa, en vez de un rectangulo por mesa completa."""
    panels = []
    for table in tables:
        x0, y0 = table[0]           # esquina inferior-izquierda de la mesa
        x1, _ = table[1]            # esquina inferior-derecha
        _, y2 = table[2]            # esquina superior-derecha (da el y de arriba)
        mesa_width = x1 - x0
        mesa_depth = y2 - y0
        col_w = (mesa_width - (n_cols - 1) * col_gap) / n_cols
        row_h = (mesa_depth - (n_rows - 1) * row_gap) / n_rows
        for r in range(n_rows):
            py0 = y0 + r * (row_h + row_gap)
            for c in range(n_cols):
                px0 = x0 + c * (col_w + col_gap)
                panels.append([(px0, py0), (px0 + col_w, py0), (px0 + col_w, py0 + row_h), (px0, py0 + row_h)])
    return panels


# --------------------------------------------------------------------------
# Escritura de KML
# --------------------------------------------------------------------------

_STYLES = """
\t<Style id="origStyle"><LineStyle><color>ffffffff</color><width>2</width></LineStyle><PolyStyle><color>1affffff</color></PolyStyle></Style>
\t<Style id="treeStyle"><LineStyle><color>ff00A550</color><width>2</width></LineStyle><PolyStyle><color>8000A550</color></PolyStyle></Style>
\t<Style id="trafoStyle"><LineStyle><color>ff0000FF</color><width>2</width></LineStyle><PolyStyle><color>cc0000FF</color></PolyStyle></Style>
\t<Style id="connStyle"><IconStyle><color>ff00DCFF</color><scale>1.3</scale><Icon><href>http://maps.google.com/mapfiles/kml/shapes/electronics.png</href></Icon></IconStyle></Style>
\t<Style id="mesaStyle1"><LineStyle><color>ff1E90FF</color><width>1</width></LineStyle><PolyStyle><color>801E90FF</color></PolyStyle></Style>
\t<Style id="mesaStyle2"><LineStyle><color>ffFF8C00</color><width>1</width></LineStyle><PolyStyle><color>80FF8C00</color></PolyStyle></Style>
\t<Style id="mesaStyle3"><LineStyle><color>ff32CD32</color><width>1</width></LineStyle><PolyStyle><color>8032CD32</color></PolyStyle></Style>
""".strip("\n")

_MESA_STYLES = ["mesaStyle1", "mesaStyle2", "mesaStyle3"]


def _coords_str(corners_xy, proj, close=True):
    pts = [proj.to_lonlat(x, y) for x, y in corners_xy]
    if close:
        pts.append(pts[0])
    return " ".join(f"{lon:.8f},{lat:.8f},0" for lon, lat in pts)


def _poly_placemark(name, poly_xy, style, proj):
    cs = _coords_str(poly_xy, proj)
    return (f"\t<Placemark>\n\t\t<name>{name}</name>\n\t\t<styleUrl>#{style}</styleUrl>\n"
            f"\t\t<Polygon><tessellate>1</tessellate><outerBoundaryIs><LinearRing>\n"
            f"\t\t<coordinates>{cs}</coordinates>\n\t\t</LinearRing></outerBoundaryIs></Polygon>\n\t</Placemark>")


def write_kml(areas, proj, out_path, doc_name="Layout_Preliminar", extra_description=""):
    """areas: lista de dicts, uno por area/poligono construible:
        {name, build_poly_xy, tree_polys_xy, trafo_corners, conn_xy, layout}
       layout es el dict que devuelve generate_layout().
    """
    orig_pms, tree_pms, conn_pms, trafo_pms, folders = [], [], [], [], []
    total_modules = total_dc = 0

    for i, area in enumerate(areas):
        style = _MESA_STYLES[i % len(_MESA_STYLES)]
        orig_pms.append(_poly_placemark(f"{area['name']}_poligono_original", area["build_poly_xy"], "origStyle", proj))
        for j, tp in enumerate(area.get("tree_polys_xy", []) or []):
            tree_pms.append(_poly_placemark(f"{area['name']}_arbol_{j+1}", tp, "treeStyle", proj))
        if area.get("conn_xy") is not None:
            lon, lat = proj.to_lonlat(*area["conn_xy"])
            conn_pms.append(
                f"\t<Placemark>\n\t\t<name>{area['name']}_Punto_Conexion</name>\n\t\t<styleUrl>#connStyle</styleUrl>\n"
                f"\t\t<Point><coordinates>{lon:.8f},{lat:.8f},0</coordinates></Point>\n\t</Placemark>")
        layout = area["layout"]
        if layout.get("trafo_corners"):
            trafo_pms.append(_poly_placemark(f"{area['name']}_Centro_transformacion_5x5", layout["trafo_corners"], "trafoStyle", proj))

        mesa_pms = []
        for k, corners in enumerate(layout["panels"]):
            cs = _coords_str(corners, proj)
            mesa_pms.append(
                f"\t\t<Placemark>\n\t\t\t<name>{area['name']}_panel_{k+1:04d}</name>\n\t\t\t<styleUrl>#{style}</styleUrl>\n"
                f"\t\t\t<Polygon><tessellate>1</tessellate><outerBoundaryIs><LinearRing>\n"
                f"\t\t\t<coordinates>{cs}</coordinates>\n\t\t\t</LinearRing></outerBoundaryIs></Polygon>\n\t\t</Placemark>")

        s = layout["summary"]
        total_modules += s["n_modules"]
        total_dc += s["dc_kwp"]
        desc = (f"Mesas: {s['n_tables']} (2 modulos c/u) = {s['n_modules']} paneles | "
                f"DC: {s['dc_kwp']:.1f} kWp | Disponibles: {s['available_tables']} mesas | "
                f"Objetivo: {s['panels_requested']} paneles")
        folders.append(
            f"\t<Folder>\n\t\t<name>{area['name']} ({s['n_tables']} mesas / {s['n_modules']} paneles / {s['dc_kwp']:.1f} kWp)</name>\n"
            f"\t\t<description>{desc}</description>\n" + "\n".join(mesa_pms) + "\n\t</Folder>")

    ac_kw_990 = min(990.0, total_dc / 1.2)  # tope del inversor estandar 3x330kW
    summary_desc = (f"TOTAL: {total_modules} paneles, {total_dc:.1f} kWp DC, ~{ac_kw_990:.1f} kW AC "
                     f"(tope inversor 3x Huawei SUN2000-330KTL-H1 = 990 kW). {extra_description}")

    kml = (f'<?xml version="1.0" encoding="UTF-8"?>\n'
           f'<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:gx="http://www.google.com/kml/ext/2.2" '
           f'xmlns:kml="http://www.opengis.net/kml/2.2" xmlns:atom="http://www.w3.org/2005/Atom">\n<Document>\n'
           f"\t<name>{doc_name}</name>\n\t<description>{summary_desc}</description>\n{_STYLES}\n"
           + "\n".join(orig_pms) + "\n" + "\n".join(tree_pms) + "\n" + "\n".join(conn_pms) + "\n"
           + "\n".join(trafo_pms) + "\n" + "\n".join(folders) + "\n</Document>\n</kml>")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(kml)
    return dict(total_modules=total_modules, total_dc=total_dc, ac_kw_990=ac_kw_990, path=out_path)
