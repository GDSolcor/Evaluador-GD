"""
Capas de las autoridades ambientales regionales, tal como las publican en sus geovisores (ticket #47):

  corpoboyaca  Visor público de Corpoboyacá (corpoboyaca-visorpublico.geoambiental.co). Las 82 capas del visor se
               sirven desde un GeoServer público (appgeoserver.api.sigmaingenieria.net, operado por Sigma Ingeniería)
               con WFS abierto: drenajes 1:10.000, 1:25.000 y 1:100.000, rondas hídricas acotadas, páramos,
               humedales, áreas protegidas, POMCA, gestión del riesgo, geología, hidrogeología y predial.
  car          Datos geográficos abiertos de la CAR Cundinamarca (datosgeograficos.car.gov.co, ArcGIS Hub): todos los
               servicios de entidades (Feature Service) del catálogo: rondas, páramos, áreas protegidas y su
               zonificación, POMCA, amenazas municipales, cambio climático, suelos…

Se guardan en datos/autoridades_ambientales.db con el esquema de la tabla features del GIS Solcor (motor/gis.py la
une a la vista de capas). Cada capa conserva su nombre del visor (columna layer) y sus atributos tal cual.

Descarga cuidadosa: una petición a la vez con pausa, páginas en caché en disco (reanudable) y parada tras 3 fallos
seguidos. Cada capa se guarda en la base al terminar, así que una descarga interrumpida conserva lo ya bajado.

    python herramientas/descargar_autoridades_ambientales.py                 -> ambas
    python herramientas/descargar_autoridades_ambientales.py corpoboyaca
    python herramientas/descargar_autoridades_ambientales.py car
    python herramientas/descargar_autoridades_ambientales.py corpoboyaca --sin-predial
"""
import json
import os
import re
import sqlite3
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from descargar_red_arcgis import Cliente, Cortes, descargar_capa  # noqa: E402

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.environ.get("EVALUADOR_AUTORIDADES_DB", os.path.join(RAIZ, "datos", "autoridades_ambientales.db"))
CACHE = os.path.join(RAIZ, "datos", "autoridades_ambientales")

GEOSERVER = "https://appgeoserver.api.sigmaingenieria.net/geoserver/ows"
CAR_HUB = "https://datosgeograficos.car.gov.co/api/search/v1/collections/all/items"
FUENTE = {"corpoboyaca": "Visor público Corpoboyacá", "car": "Datos geográficos abiertos CAR"}

# Corpoboyacá: (capa del GeoServer, nombre en el visor, dataset)
H, R, PH, AP, RC, PO, RI, GE, RF, PR = ("CORPOBOYACA_Hidrografia", "CORPOBOYACA_Rondas", "CORPOBOYACA_Paramos_Humedales",
                                         "CORPOBOYACA_Areas_Protegidas", "CORPOBOYACA_Recarga", "CORPOBOYACA_POMCA",
                                         "CORPOBOYACA_Riesgo", "CORPOBOYACA_Geologia", "CORPOBOYACA_Referencia", "CORPOBOYACA_Predial")
CORPOBOYACA = [
    # hidrografía (lo que más se pidió en el ticket)
    ("CartografiaBase:DrenajeDoble100k", "Drenaje Doble 100K", H),
    ("CartografiaBase:DrenajeSencillo100kCodif", "Drenaje Sencillo 100K", H),
    ("CartografiaBase:DrenajeDoble25k", "Drenaje Doble 25K", H),
    ("CartografiaBase:DrenajeSencillo25k", "Drenaje Sencillo 25K", H),
    ("CartografiaBase:DrenajeDoble10k", "Drenaje Doble 10K", H),
    ("CartografiaBase:DrenajeSencillo10k", "Drenaje Sencillo 10K", H),
    ("CartografiaBase:FuentesCreadasGeoam", "Otras Fuentes Hídricas", H),
    # rondas hídricas acotadas y cotas de inundación
    ("Rondas_hidricas:CPermanenteChicamocha", "Cauce Permanente Río Chicamocha", R),
    ("Rondas_hidricas:RondaHidricaRChi", "Ronda Hídrica Río Chicamocha", R),
    ("Rondas_hidricas:FajaParalelaChicamocha", "Faja Paralela Río Chicamocha", R),
    ("Rondas_hidricas:ARestauracionChicamocha", "Área De Restauración Río Chicamocha", R),
    ("Rondas_hidricas:AProteccChicamocha", "Área De Protección Río Chicamocha", R),
    ("Rondas_hidricas:CPermanenteJordan", "Cauce Permanente Río Jordán", R),
    ("Rondas_hidricas:RondaHidricaJordan", "Ronda Hídrica Río Jordán", R),
    ("Rondas_hidricas:FajaParalelaJordan", "Faja Paralela Río Jordán", R),
    ("Rondas_hidricas:CPermanenteVega", "Cauce Permanente Río La Vega", R),
    ("Rondas_hidricas:RondaHidricaVega", "Ronda Hídrica Río La Vega", R),
    ("Rondas_hidricas:FajaParalelaVega", "Faja Paralela Río La Vega", R),
    ("Rondas_hidricas:CaucePerm_RioPiedras", "Cauce Permanente Río Piedras", R),
    ("Rondas_hidricas:FajaParela_RioPiedras", "Faja Paralela Río Piedras", R),
    ("Rondas_hidricas:MedidasManejo_RioPiedras", "Medidas Manejo Río Piedras", R),
    ("Rondas_hidricas:AProteccion_RioPiedras", "Área De Protección Río Piedras", R),
    ("Rondas_hidricas:CPermanenteTuta", "Cauce Permanente Río Tuta", R),
    ("Rondas_hidricas:RondaHidricaTuta", "Ronda Hídrica Río Tuta", R),
    ("Rondas_hidricas:FajaParalelaTuta", "Faja Paralela Río Tuta", R),
    ("Rondas_hidricas:ARestauracionTuta", "Área De Restauración Río Tuta", R),
    ("Rondas_hidricas:AProteccTuta", "Áreas De Protección Río Tuta", R),
    ("Rondas_hidricas:CotaInundacionTOTA", "Cota Max. Inundación Lago De Tota", R),
    ("Rondas_hidricas:RondaProteccTOTA", "Ronda De Protección Lago De Tota", R),
    ("Rondas_hidricas:cpermanentenobsa", "Cauces Permanentes Nobsa", R),
    ("Rondas_hidricas:fajaparalelanobsa", "Fajas Paralelas Nobsa", R),
    ("Rondas_hidricas:zonasestrategicasnobsa", "Zonas Estratégicas Nobsa", R),
    ("Rondas_hidricas:rondahidricanobsa", "Rondas Hídricas Nobsa", R),
    ("Ecosistemas_estrategicos:CotaMaxInundPalagua", "Cota Max Inundación Palagua", R),
    # páramos y humedales
    ("Ecosistemas_estrategicos:paramos25k100kJurisd", "Paramos", PH),
    ("Ecosistemas_estrategicos:DelimitacionHumedales", "Delimitacion Humedales", PH),
    ("Ecosistemas_estrategicos:HumedalesZonificacion", "Humedales Zonificación", PH),
    # áreas protegidas
    ("Areas_protegidas:ZonificacionAreasProtegidas", "Zonificación Áreas Protegidas", AP),
    ("Areas_protegidas:areas_protegidas_2026", "Sirap", AP),
    ("Ecosistemas_estrategicos:ReservasLey2da", "Reservas Ley2da", AP),
    # recarga de acuíferos e interés hídrico
    ("Ecosistemas_estrategicos:ZonaRecargaNueva", "Área Recarga Acuífero Tunja", RC),
    ("Ecosistemas_estrategicos:Areas_Interes_Hidrico1", "Areas Interes Hidrico", RC),
    ("Hidrogeologia:zona_recarga_mediobajosuarez", "Zona De Recarga Medio Bajo Suarez", RC),
    ("Hidrogeologia:zona_recarga_cravosur", "Zona De Recarga Cravo Sur", RC),
    ("Hidrogeologia:zona_recarga_directmagdalena", "Zona De Recarga Directos Al Magdalena", RC),
    # POMCA y ordenación forestal
    ("Zonificacion_POMCAS_PGOF:Zon_Carare", "Pomca Río Carare Minero", PO),
    ("Zonificacion_POMCAS_PGOF:Zon_Cusiana", "Pomca Río Cusiana", PO),
    ("Zonificacion_POMCAS_PGOF:Zon_CravoSur", "Pomca Río Cravo Sur", PO),
    ("Zonificacion_POMCAS_PGOF:Zon_MBSuarez", "Pomca Río Medio Y Bajo Suarez", PO),
    ("Zonificacion_POMCAS_PGOF:Zon_Garagoa", "Pomca Río Garagoa", PO),
    ("Zonificacion_POMCAS_PGOF:Zon_AltoChicamocha", "Pomca Río Alto Chicamocha", PO),
    ("Zonificacion_POMCAS_PGOF:Zon_ASuarez", "Pomca Río Alto Suarez", PO),
    ("Zonificacion_POMCAS_PGOF:Zon_DirectosMagd", "Pomca Río Directos Al Magdalena Medio Entre Ríos Negro Y Carare", PO),
    ("Zonificacion_POMCAS_PGOF:PomcaPauto", "Pomca Río Pauto", PO),
    ("Zonificacion_POMCAS_PGOF:ZonRioNegro", "Pomca Río Negro", PO),
    ("Zonificacion_POMCAS_PGOF:PomcaMedioChicamocha", "Pomca Río Medio Chicamocha", PO),
    ("Zonificacion_POMCAS_PGOF:PGOF", "Plan Ordenación Forestal", PO),
    ("Zonificacion_POMCAS_PGOF:POF_Occidente", "Plan Ordenación Forestal Occidente", PO),
    ("Rondas_hidricas:Area_Aferente_La_Vega", "Area Aferente La Vega", PO),
    ("Rondas_hidricas:Area_Aferente_Jordan", "Area Aferente Jordán", PO),
    # gestión del riesgo
    ("gesti_riesgo_2026:incendios_forestales2024-2026", "Incendios Forestales 2024 A 2026", RI),
    ("gesti_riesgo_2026:movimientos_en_masa2024-2026", "Movimientos En Masa 2024 A 2026", RI),
    ("gesti_riesgo_2026:recorridos_diagnosticos2024-2026", "Recorridos Diagnosticos 2024 A 2026", RI),
    # geología e hidrogeología
    ("Geologia:mediobajosuarez", "Geologia Medio Bajo Suarez", GE),
    ("Geologia:directmagdalena", "Geologia Directos Al Magdalena", GE),
    ("Geologia:unidad_geo_altochicam", "Geologia Alto Chicamocha", GE),
    ("Geologia:cravosur", "Geologia Cravo Sur", GE),
    ("Hidrogeologia:unidad_hidrogeo_altochicam", "Hidrogeologia Alto Chicamocha", GE),
    ("Hidrogeologia:unidad_hidrogeo_mediobajosuarez", "Hidrogeologia Medio Bajo Suarez", GE),
    ("Hidrogeologia:unidad_hidrogeo_cravosur", "Hidrogeologia Cravo Sur", GE),
    # referencia
    ("CartografiaBase:Jurisdiccion", "Jurisdicción", RF),
    ("CartografiaBase:MunicipiosIgac2024", "Municipiosigac2024", RF),
    ("CartografiaBase:Veredas", "Veredas", RF),
    ("CartografiaBase:Cuencas_Magna_Geo", "Cuencas", RF),
    ("CartografiaBase:Subcuencas_Magna_Geo", "Subcuencas", RF),
    # predial (al final: son 720.000 predios)
    ("BasePredial:PredialUrbano", "Predial Urbano", PR),
    ("BasePredial:PredialRural", "Predial Rural", PR),
]

# CAR: dataset según el título del servicio (orden: la primera regla que coincide)
REGLAS_CAR = [
    (r"ronda|^rh_", "CAR_Rondas"),
    (r"cambio de uso", "CAR_Clima_Suelos"),
    (r"p[aá]ramo", "CAR_Paramos"),
    (r"pomca", "CAR_POMCA"),
    (r"area[s]? protegida|dmi|drmi|rfpp?_|rfpp? |tvdh|thomas van|r[eé]gimen de usos|zonificaci[oó]n ambiental", "CAR_Areas_Protegidas"),
    (r"^ricv_", "CAR_Riesgo_Incendios"),  # antes que la regla de amenazas municipales («…_incendios_…»)
    (r"_avenida_torrencial|_incendio|_inundacion|_remocion_en_masa|registro de eventos", "CAR_Riesgo"),
    (r"aguas subterr|eep_car|bama|restaurar", "CAR_Ecosistemas"),
    (r"jurisdicci[oó]n|cuencas y subcuencas", "CAR_Referencia"),
    (r".", "CAR_Clima_Suelos"),
]


def _ok(x, y):
    return isinstance(x, (int, float)) and isinstance(y, (int, float)) and -82 < x < -66 and -5 < y < 14


def _limpio(v):
    if isinstance(v, float) and v.is_integer() and abs(v) < 1e15:
        return int(v)
    return v


def _attrs(props, fuente, fecha, capa):
    at = {}
    for k, v in props.items():
        if v in (None, "", " ", "<Null>", "<null>", "Null") or re.fullmatch(r"(?i)shape[_.]?(area|len(gth)?|le_\d)|st_(area|length).*|objectid(_\d+)?|fid|gid", k):
            continue
        at[k] = _limpio(v)
    at["Fuente"] = f"{fuente} ({fecha})"  # el nombre de la capa va en la columna layer (el mapa lo muestra como «capa»)
    return at


def _nombre(at, capa):
    for k, v in at.items():
        if re.fullmatch(r"(?i)nombre.*|nom_.*|name|.*_nom|nombre_geo|nomb.*|zona|zonificac.*|categoria|unidad|uso.*", k) and isinstance(v, str) and v.strip():
            return v.strip()[:120]
    return capa


def _area(r):
    return sum(r[i][0] * r[i + 1][1] - r[i + 1][0] * r[i][1] for i in range(len(r) - 1)) / 2


def _filas_geojson(feats, ds, capa, fuente, fecha):
    out = []
    for f in feats:
        g = f.get("geometry") or {}
        at = _attrs(f.get("properties") or {}, fuente, fecha, capa)
        nombre = _nombre(at, capa)
        aj = json.dumps(at, ensure_ascii=False)
        t, c = g.get("type"), g.get("coordinates")
        if not c:
            continue
        if t in ("Point", "MultiPoint"):
            for p in ([c] if t == "Point" else c):
                if _ok(p[0], p[1]):
                    x, y = round(p[0], 6), round(p[1], 6)
                    out.append((ds, capa, nombre, aj, "Point", json.dumps([x, y]), x, y, x, y))
        elif t in ("LineString", "MultiLineString"):
            paths = [[[round(p[0], 6), round(p[1], 6)] for p in path if _ok(p[0], p[1])] for path in ([c] if t == "LineString" else c)]
            paths = [p for p in paths if len(p) >= 2]
            pts = [p for path in paths for p in path]
            if pts:
                xs, ys = [p[0] for p in pts], [p[1] for p in pts]
                out.append((ds, capa, nombre, aj, "LineString", json.dumps(paths), min(xs), min(ys), max(xs), max(ys)))
        elif t in ("Polygon", "MultiPolygon"):
            for poly in ([c] if t == "Polygon" else c):
                anillos = [[[round(p[0], 6), round(p[1], 6)] for p in r if _ok(p[0], p[1])] for r in poly]
                anillos = [r for r in anillos if len(r) >= 4]
                if not anillos:
                    continue
                xs, ys = [p[0] for p in anillos[0]], [p[1] for p in anillos[0]]
                out.append((ds, capa, nombre, aj, "Polygon", json.dumps(anillos), min(xs), min(ys), max(xs), max(ys)))
    return out


def _filas_arcgis(feats, ds, capa, fuente, fecha):
    """Elementos ArcGIS (JSON Esri, outSR 4326) -> filas."""
    gj = []
    for e in feats:
        g = e.get("geometry") or {}
        if "x" in g:
            geom = {"type": "Point", "coordinates": [g["x"], g.get("y")]}
        elif "points" in g:
            geom = {"type": "MultiPoint", "coordinates": g["points"]}
        elif "paths" in g:
            geom = {"type": "MultiLineString", "coordinates": g["paths"]}
        elif "rings" in g:
            polys = []
            for r in g["rings"]:
                if len(r) < 4:
                    continue
                if _area(r) < 0 or not polys:  # exterior en sentido horario
                    polys.append([r])
                else:
                    polys[-1].append(r)
            geom = {"type": "MultiPolygon", "coordinates": polys}
        else:
            continue
        gj.append({"geometry": geom, "properties": e.get("attributes") or {}})
    return _filas_geojson(gj, ds, capa, fuente, fecha)


def _base():
    c = sqlite3.connect(DB, timeout=120)  # Corpoboyacá y la CAR pueden bajarse a la vez en dos procesos
    c.executescript("""CREATE TABLE IF NOT EXISTS features (id INTEGER PRIMARY KEY AUTOINCREMENT, dataset TEXT, layer TEXT, name TEXT,
        attrs_json TEXT, geom_type TEXT, geom_json TEXT, xmin REAL, ymin REAL, xmax REAL, ymax REAL);
        CREATE INDEX IF NOT EXISTS idx_bbox ON features(dataset, xmax, xmin, ymax, ymin);
        CREATE TABLE IF NOT EXISTS capas (fuente TEXT, clave TEXT, dataset TEXT, layer TEXT, elementos INTEGER, fecha TEXT,
        PRIMARY KEY (fuente, clave));""")
    if not c.execute("SELECT 1 FROM sqlite_sequence WHERE name='features'").fetchone():
        c.execute("INSERT INTO sqlite_sequence(name, seq) VALUES ('features', 300000000)")  # ids sin choque con las otras bases
    return c


def _guardar(fuente, clave, ds, capa, filas, fecha):
    c = _base()
    with c:
        viejo = c.execute("SELECT dataset, layer FROM capas WHERE fuente=? AND clave=?", (fuente, clave)).fetchone()
        if viejo:
            c.execute("DELETE FROM features WHERE dataset=? AND layer=?", viejo)
        c.executemany("INSERT INTO features(dataset, layer, name, attrs_json, geom_type, geom_json, xmin, ymin, xmax, ymax) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?)", filas)
        c.execute("INSERT OR REPLACE INTO capas VALUES (?,?,?,?,?,?)", (fuente, clave, ds, capa, len(filas), fecha))
    c.close()


def _listo(fuente, clave):
    if not os.path.isfile(DB):
        return False
    c = sqlite3.connect(DB)
    try:
        return bool(c.execute("SELECT 1 FROM capas WHERE fuente=? AND clave=?", (fuente, clave)).fetchone())
    except sqlite3.OperationalError:
        return False
    finally:
        c.close()


# ------------------------------------------------------------------------------------------- Corpoboyacá (WFS)
def _wfs(cli, capa, carpeta, pagina=2000):
    """Todas las entidades de una capa WFS en lon/lat, por páginas (startIndex) con caché."""
    clave = re.sub(r"[^A-Za-z0-9_-]", "_", capa)
    feats, i = [], 0
    while True:
        destino = os.path.join(carpeta, clave, f"{i:07d}.json")
        d = cli.json(GEOSERVER, {"service": "WFS", "version": "1.0.0", "request": "GetFeature", "typeName": capa,
                                 "outputFormat": "application/json", "srsName": "EPSG:4326", "maxFeatures": pagina,
                                 "startIndex": i}, destino)
        lote = d.get("features") or []
        feats += lote
        i += len(lote)
        if len(lote) < pagina:
            return feats
        if (i // pagina) % 10 == 0:
            print(f"    {capa}: {i} elementos", flush=True)


def corpoboyaca(sin_predial=False, rehacer=False):
    cli = Cliente(1.0)
    carpeta = os.path.join(CACHE, "corpoboyaca")
    for capa, titulo, ds in CORPOBOYACA:
        if sin_predial and ds == PR:
            continue
        if not rehacer and _listo("corpoboyaca", capa):
            continue
        print(f"corpoboyaca · {titulo} ({capa})…", flush=True)
        feats = _wfs(cli, capa, carpeta)
        fecha = time.strftime("%Y-%m-%d")
        filas = _filas_geojson(feats, ds, titulo, FUENTE["corpoboyaca"], fecha)
        _guardar("corpoboyaca", capa, ds, titulo, filas, fecha)
        print(f"  {len(feats)} elementos -> {len(filas)} geometrías", flush=True)


# ------------------------------------------------------------------------------------------- CAR (ArcGIS Hub)
def _get_json(url, destino):
    """GET con caché (la API del Hub no acepta POST)."""
    import urllib.request
    if os.path.isfile(destino):
        with open(destino, encoding="utf-8") as f:
            return json.load(f)
    time.sleep(1)
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Evaluador GD)"}), timeout=90) as r:
        d = json.load(r)
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    with open(destino, "w", encoding="utf-8") as f:
        json.dump(d, f)
    return d


def _servicios_car(cli):
    items, url, n = [], CAR_HUB + "?limit=100", 0
    while url:
        d = _get_json(url, os.path.join(CACHE, "car", "catalogo", f"{n:03d}.json"))
        items += d.get("features", [])
        nxt = [lk["href"] for lk in d.get("links", []) if lk.get("rel") == "next"]
        url, n = (nxt[0], n + 1) if nxt else (None, n)
    vistos, out = set(), []
    for it in items:
        p = it.get("properties") or {}
        u = (p.get("url") or "").rstrip("/")
        if p.get("type") != "Feature Service" or not u or u in vistos:
            continue
        vistos.add(u)
        out.append((p.get("title") or u.rsplit("/", 2)[-2], u))
    return out


def _dataset_car(titulo):
    t = titulo.lower()
    return next(ds for patron, ds in REGLAS_CAR if re.search(patron, t))


def _get(cli, url, destino):
    # el cliente envía POST; los servicios ArcGIS aceptan ambos
    return cli.json(url, {"f": "json"}, destino)


def car(rehacer=False):
    cli = Cliente(1.0)
    carpeta = os.path.join(CACHE, "car")
    servicios = _servicios_car(cli)
    print(f"CAR: {len(servicios)} servicios", flush=True)
    for titulo, url in servicios:
        u = urllib.parse.urlsplit(url)
        url = urllib.parse.urlunsplit((u.scheme, u.netloc, urllib.parse.quote(urllib.parse.unquote(u.path)), "", ""))
        nombre_srv = re.sub(r"[^A-Za-z0-9_-]", "_", urllib.parse.unquote(u.path).split("/services/")[-1])[:80]
        try:
            info = _get(cli, url, os.path.join(carpeta, "servicios", nombre_srv + ".json"))
        except Cortes as e:
            print("  servicio sin respuesta:", titulo, e, flush=True)
            cli.fallos = 0
            continue
        capas = [lyr for lyr in info.get("layers", []) if lyr.get("type", "Feature Layer") == "Feature Layer"]
        ds = _dataset_car(titulo)
        for lyr in capas:
            clave = f"{nombre_srv}_{lyr['id']}"
            if not rehacer and _listo("car", clave):
                continue
            nombre = titulo if len(capas) == 1 else f"{titulo} · {lyr.get('name')}"
            print(f"car · {nombre}…", flush=True)
            meta = cli.json(f"{url}/{lyr['id']}", {"f": "json"}, os.path.join(carpeta, "cache", f"{clave}_meta.json"))
            campos = ",".join(f["name"] for f in meta.get("fields", []) if f.get("type") not in ("esriFieldTypeGeometry", "esriFieldTypeBlob"))
            try:
                feats = descargar_capa(cli, url, clave, lyr["id"], campos, carpeta, tam_max=1000)
            except Cortes as e:
                print("  capa sin respuesta, sigue con la próxima:", e, flush=True)
                cli.fallos = 0
                continue
            fecha = time.strftime("%Y-%m-%d")
            filas = _filas_arcgis(feats, ds, nombre, FUENTE["car"], fecha)
            _guardar("car", clave, ds, nombre, filas, fecha)
            print(f"  {len(feats)} elementos -> {len(filas)} geometrías", flush=True)


def resumen():
    c = _base()
    for ds, n, capas in c.execute("SELECT dataset, count(*), count(DISTINCT layer) FROM features GROUP BY dataset ORDER BY dataset"):
        print(f"{n:9}  {capas:4} capas  {ds}")
    c.close()
    conteos = os.path.join(RAIZ, "datos", "gis", "_conteos.json")
    if os.path.isfile(conteos):
        os.remove(conteos)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    fuentes = args or ["corpoboyaca", "car"]
    try:
        if "corpoboyaca" in fuentes:
            corpoboyaca(sin_predial="--sin-predial" in sys.argv, rehacer="--rehacer" in sys.argv)
        if "car" in fuentes:
            car(rehacer="--rehacer" in sys.argv)
    except Cortes as e:
        print("DETENIDO:", e)
        resumen()
        sys.exit(2)
    resumen()
