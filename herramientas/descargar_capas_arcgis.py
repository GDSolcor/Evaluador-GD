"""
Capas de restricción complementarias desde geoportales ArcGIS públicos de entidades del Estado:

  ICANH  Geovisor ICANH (geo.icanh.gov.co): áreas arqueológicas protegidas, parques arqueológicos,
         sitios arqueológicos, programas de arqueología preventiva (PAP), hallazgos positivos de PAP
         y zonificación de los planes de manejo arqueológico municipales.
  ANH    GeoVisor ANH "Tierras" (geovisor.anh.gov.co/tierras): áreas con contrato o en proceso de
         hidrocarburos (mapa de tierras vigente), pozos y líneas sísmicas 2D.

Se guardan en datos/capas_complementarias.db con el mismo esquema que la tabla features del GIS
Solcor; motor/gis.py la une a la vista de capas. No se bajan campos de personas (titulares,
usuarios de edición).

Descarga cuidadosa: una petición a la vez con pausa, páginas por OBJECTID, caché en disco
(reanudable) y parada tras 3 fallos seguidos (descargar_red_arcgis.Cliente).

    python herramientas/descargar_capas_arcgis.py            -> todas las fuentes
    python herramientas/descargar_capas_arcgis.py icanh
"""
import json
import os
import sqlite3
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from descargar_red_arcgis import Cliente, Cortes, descargar_capa  # noqa: E402

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(RAIZ, "datos", "capas_complementarias.db")
CACHE = os.path.join(RAIZ, "datos", "capas_complementarias")

ICANH = "https://geovector.icanh.gov.co/server/rest/services"
ANH = "https://geovisor.anh.gov.co/server/rest/services/GEOVISOR_v32"
MADS = "https://geo.minambiente.gov.co/server/rest/services"
ANLA = "https://portalsig.anla.gov.co/publico/rest/services/PROYECTOS_ANLA"


def _fecha(ms):
    """Fecha ArcGIS (milisegundos desde 1970) -> AAAA-MM-DD."""
    import datetime as dt
    if not isinstance(ms, (int, float)):
        return ms
    # con timedelta: en Windows time.gmtime no acepta fechas anteriores a 1970
    return (dt.datetime(1970, 1, 1) + dt.timedelta(milliseconds=ms)).strftime("%Y-%m-%d")


def _redondo(v):
    try:
        return round(float(v))
    except (TypeError, ValueError):
        return v


def _u(url):
    p = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((p.scheme, p.netloc, urllib.parse.quote(p.path), "", ""))


# (fuente, clave, url servicio, id capa, campos, dataset, capa (layer), nombre(attrs), atributos legibles(attrs))
CAPAS = [
    ("icanh", "aap", f"{ICANH}/Atlas/ÁREAS_ARQUEOLOGICAS_PREVENTIVAS/FeatureServer", 0,
     "nombrearea,subarea,tipoarea,dpto,mpio,actoadmin,enlaceacto,enlacepma,areaha", "ICANH_Protegida", "Área arqueológica protegida",
     lambda a: a.get("nombrearea"),
     lambda a: {"Área": a.get("nombrearea"), "Subárea": a.get("subarea"), "Tipo": a.get("tipoarea"), "Municipio": a.get("mpio"),
                "Departamento": a.get("dpto"), "Acto administrativo": a.get("actoadmin"), "Plan de manejo": a.get("enlacepma"),
                "Área (ha)": a.get("areaha")}),
    ("icanh", "parques", f"{ICANH}/Atlas/PARQUES/MapServer", 1,
     "nombrearea,subarea,tipoarea,dpto,mpio,actoadmin,enlacepma,patmundial,areaha", "ICANH_Protegida", "Parque arqueológico",
     lambda a: a.get("nombrearea"),
     lambda a: {"Parque": a.get("nombrearea"), "Subárea": a.get("subarea"), "Tipo": a.get("tipoarea"), "Municipio": a.get("mpio"),
                "Departamento": a.get("dpto"), "Acto administrativo": a.get("actoadmin"), "Patrimonio mundial": a.get("patmundial"),
                "Área (ha)": a.get("areaha")}),
    ("icanh", "zonificacion_pma", f"{ICANH}/Atlas/PMA_MUNICIPAÑ/FeatureServer", 13,
     "tipo_area,escala,nombre,enlace_pma", "ICANH_PMA", "Zonificación arqueológica (PMA municipal)",
     lambda a: a.get("nombre") or a.get("tipo_area"),
     lambda a: {"Zona": a.get("tipo_area"), "Nombre": a.get("nombre"), "Escala": a.get("escala"), "Plan de manejo": a.get("enlace_pma")}),
    ("icanh", "restriccion_pma", f"{ICANH}/Atlas/PMA_MUNICIPAÑ/FeatureServer", 9,
     "tipo_area,escala,nombre,enlace_pma", "ICANH_PMA", "Área de restricción (PMA municipal)",
     lambda a: a.get("nombre") or a.get("tipo_area"),
     lambda a: {"Zona": a.get("tipo_area"), "Nombre": a.get("nombre"), "Escala": a.get("escala"), "Plan de manejo": a.get("enlace_pma")}),
    ("icanh", "conservacion_pma", f"{ICANH}/Atlas/PMA_MUNICIPAÑ/FeatureServer", 10,
     "nombre,estado_con,escala,enlace_pma", "ICANH_PMA", "Área de conservación (PMA municipal)",
     lambda a: a.get("nombre"),
     lambda a: {"Nombre": a.get("nombre"), "Estado de conservación": a.get("estado_con"), "Escala": a.get("escala"),
                "Plan de manejo": a.get("enlace_pma")}),
    ("icanh", "sitios", f"{ICANH}/Atlas/SITIOS/MapServer", 0,
     "id,nombre_arqueologico,nombre_periodo_regional,siglo_ocupacion_inicial,siglo_ocupacion_final,departamento,municipio",
     "ICANH_Sitio", "Sitio arqueológico",
     lambda a: a.get("nombre_arqueologico") or f"Sitio {a.get('id')}",
     lambda a: {"Sitio": a.get("nombre_arqueologico"), "Periodo regional": a.get("nombre_periodo_regional"),
                "Ocupación (siglos)": " a ".join(str(x) for x in (a.get("siglo_ocupacion_inicial"), a.get("siglo_ocupacion_final")) if x),
                "Municipio": a.get("municipio"), "Departamento": a.get("departamento")}),
    ("icanh", "pap_positivos", f"{ICANH}/Atlas/ARQUEOLOGIA_PREVENTIVA/MapServer", 0,
     "id_act_arq,tipo_act,fecha_ini,ano,municipio,departamento", "ICANH_Hallazgo", "Actividad arqueológica con hallazgo positivo (PAP)",
     lambda a: f"{a.get('tipo_act') or 'Actividad'} positiva",
     lambda a: {"Actividad": a.get("tipo_act"), "Año": a.get("ano"), "Municipio": a.get("municipio"), "Departamento": a.get("departamento")}),
    ("icanh", "pap_generales", f"{ICANH}/Atlas/ARQUEOLOGIA_PREVENTIVA/MapServer", 1,
     "regis_auto,nombre_pro,resolucion,año,municipio,departamento", "ICANH_PAP", "Programa de arqueología preventiva (PAP)",
     lambda a: a.get("nombre_pro") or a.get("regis_auto"),
     lambda a: {"Proyecto": a.get("nombre_pro"), "Registro / autorización": a.get("regis_auto"), "Resolución": a.get("resolucion"),
                "Año": a.get("año"), "Municipio": a.get("municipio"), "Departamento": a.get("departamento")}),
    ("anh", "tierras", f"{ANH}/ANH_TIERRAS_EGDB_ATTACH/MapServer", 0,
     "CONTRATO_N,AREA_NOMBR,FECHA_FIRM,CLASIFICAC,TIPO_CONTR,ESTAD_AREA,SUBTIPO,OPERADOR,AREA_HA,CUENCA_SED,YACIMIENTO,PROCESO,LEYENDA",
     "ANH_Tierras", "Mapa de tierras ANH",
     lambda a: a.get("AREA_NOMBR") if a.get("AREA_NOMBR") not in (None, "", "NO APLICA") else (a.get("LEYENDA") or "").capitalize(),
     lambda a: {"Área": a.get("AREA_NOMBR"), "Contrato": a.get("CONTRATO_N"), "Estado": a.get("ESTAD_AREA"), "Clasificación": a.get("CLASIFICAC"),
                "Tipo de contrato": a.get("TIPO_CONTR"), "Operador": a.get("OPERADOR"), "Yacimiento": a.get("YACIMIENTO"),
                "Cuenca": a.get("CUENCA_SED"), "Leyenda": a.get("LEYENDA"), "Área (ha)": a.get("AREA_HA")}),
    ("anh", "pozos", f"{ANH}/ANH_InsGDB/MapServer", 1,
     "UWI,WELL_NAME,GEOLOGIC_P", "ANH_Pozo", "Pozo de hidrocarburos",
     lambda a: a.get("WELL_NAME") or a.get("UWI"),
     lambda a: {"Pozo": a.get("WELL_NAME"), "UWI": a.get("UWI"), "Provincia geológica": a.get("GEOLOGIC_P")}),
    ("anh", "sismica_2d", f"{ANH}/ANH_InsGDB/MapServer", 2,
     "SURVEY_NAM,COMPANY_NA,ANO_ADQ,CUENCA,PROGRAMA_L", "ANH_Sismica", "Línea sísmica 2D",
     lambda a: a.get("PROGRAMA_L") or a.get("SURVEY_NAM"),
     lambda a: {"Programa": a.get("SURVEY_NAM"), "Línea": a.get("PROGRAMA_L"), "Compañía": a.get("COMPANY_NA"),
                "Año de adquisición": a.get("ANO_ADQ"), "Cuenca": a.get("CUENCA")}),
    # Reservas forestales de Ley 2ª de 1959 (MinAmbiente, SIAC). La zonificación (A, B, C) es la que condiciona el uso;
    # las sustracciones definitivas son áreas que ya salieron de la reserva.
    ("minambiente", "ley2_zonificacion", f"{MADS}/BBSE_BRFN/BRFN_Reserva_Lec/FeatureServer", 6,
     "nom_reserva,tipo_zoni,acto_admin,area_ha,fecha_actuali", "MINAMBIENTE_Ley2", "Reserva forestal Ley 2ª (zonificación)",
     lambda a: f"{a.get('nom_reserva') or 'Reserva forestal Ley 2ª'} · zona {a.get('tipo_zoni') or 'sin tipo'}",
     lambda a: {"Reserva": a.get("nom_reserva"), "Zona": a.get("tipo_zoni"), "Acto administrativo": a.get("acto_admin"),
                "Área (ha)": a.get("area_ha")}),
    ("minambiente", "ley2_reservas", f"{MADS}/BBSE_BRFN/BRFN_Reserva_Lec/FeatureServer", 8,
     "nom_ley2,area_ha,res_zoni", "MINAMBIENTE_Ley2", "Reserva forestal Ley 2ª (límite)",
     lambda a: f"{a.get('nom_ley2') or 'Reserva forestal Ley 2ª'} · dentro del límite de la reserva",
     lambda a: {"Reserva": a.get("nom_ley2"), "Resolución de zonificación": a.get("res_zoni"), "Área (ha)": a.get("area_ha")}),
    ("minambiente", "ley2_sustraccion", f"{MADS}/BBSE_BRFN/BRFN_Reserva_Lec/FeatureServer", 5,
     "nom_reserva,sustrajo,acto_admin,sector,area_ha,municipio,departamento", "MINAMBIENTE_Ley2_Sustraccion", "Sustracción definitiva de reserva Ley 2ª",
     lambda a: f"Sustracción · {a.get('nom_reserva') or ''}".strip(" ·"),
     lambda a: {"Reserva": a.get("nom_reserva"), "Entidad que sustrajo": a.get("sustrajo"), "Acto administrativo": a.get("acto_admin"),
                "Sector": a.get("sector"), "Municipio": a.get("municipio"), "Departamento": a.get("departamento"), "Área (ha)": a.get("area_ha")}),
    # ANLA (geovisor público, servicios del portal SIG): líneas de proyectos licenciados (seguimiento) y en evaluación
    ("anla", "linea_licenciado", f"{ANLA}/ProyectosANLA_Seguimiento/MapServer", 5,
     "expediente,sector,operador,proyecto,num_act_ad,fec_act_ad,descrip,nomenclat,longitud_m", "ANLA_Linea", "Línea proyecto licenciado",
     lambda a: " - ".join(str(x) for x in (a.get("expediente"), a.get("proyecto")) if x),
     lambda a: {"Expediente": a.get("expediente"), "Sector": a.get("sector"), "Operador": a.get("operador"), "Proyecto": a.get("proyecto"),
                "Acto administrativo": a.get("num_act_ad"), "Fecha del acto": _fecha(a.get("fec_act_ad")), "Descripción": a.get("descrip"),
                "Nomenclatura": a.get("nomenclat"), "Longitud (m)": _redondo(a.get("longitud_m")), "Estado": "Licenciado (seguimiento)"}),
    ("anla", "linea_evaluacion", f"{ANLA}/ProyectosANLA_Evaluacion/MapServer", 1,
     "expediente,sector,operador,proyecto,descrip,nomenclat,longitud_m,estado,radicado,fecha_radicado", "ANLA_Linea_Eval",
     "Línea proyecto en evaluación",
     lambda a: " - ".join(str(x) for x in (a.get("expediente"), a.get("proyecto")) if x),
     lambda a: {"Expediente": a.get("expediente"), "Sector": a.get("sector"), "Operador": a.get("operador"), "Proyecto": a.get("proyecto"),
                "Estado": a.get("estado") or "En evaluación", "Radicado": a.get("radicado"), "Fecha de radicado": _fecha(a.get("fecha_radicado")),
                "Descripción": a.get("descrip"), "Nomenclatura": a.get("nomenclat"), "Longitud (m)": _redondo(a.get("longitud_m"))}),
]
FUENTE = {"icanh": "Geovisor ICANH", "anh": "GeoVisor ANH (Tierras)", "minambiente": "MinAmbiente · Reservas forestales (SIAC)",
          "anla": "Geovisor ANLA (portalsig.anla.gov.co)"}
# capas de polígonos grandes: páginas pequeñas (con 2.000 el servidor responde 500)
PAGINA = {"pap_generales": 100, "tierras": 100, "sismica_2d": 500, "zonificacion_pma": 200, "restriccion_pma": 200, "conservacion_pma": 200,
          "ley2_zonificacion": 1, "ley2_reservas": 1, "ley2_sustraccion": 50, "linea_licenciado": 100, "linea_evaluacion": 100}
# polígonos de escala nacional: el servidor los entrega generalizados a ~22 m (su escala de origen es 1:100.000).
# Líneas ANLA: vienen con un vértice cada pocos centímetros (200 líneas = 27 MB); a ~2 m sobra para medir distancia al predio
SIMPLIFICAR = {"ley2_zonificacion": 0.0002, "ley2_reservas": 0.0002, "linea_licenciado": 0.00002, "linea_evaluacion": 0.00002}


def _area(r):
    return sum(r[i][0] * r[i + 1][1] - r[i + 1][0] * r[i][1] for i in range(len(r) - 1)) / 2


def _poligonos(rings):
    """Anillos ArcGIS (exteriores en sentido horario, huecos antihorario) -> [[exterior, huecos...]]."""
    polys = []
    for r in rings:
        if len(r) < 4:
            continue
        if _area(r) < 0 or not polys:
            polys.append([r])
        else:
            polys[-1].append(r)
    return polys


def filas_de(feats, ds, layer, nombre, legible, fuente, fecha):
    ok = lambda x, y: isinstance(x, (int, float)) and isinstance(y, (int, float)) and -82 < x < -66 and -5 < y < 14
    out = []
    for e in feats:
        a, g = e.get("attributes") or {}, e.get("geometry") or {}
        at = {k: v for k, v in legible(a).items() if v not in (None, "", " ")}
        at["Fuente"] = f"{fuente} ({fecha})"
        at["_name"] = str(nombre(a) or layer)
        aj = json.dumps(at, ensure_ascii=False)
        if "x" in g:
            if not ok(g["x"], g.get("y")):
                continue
            out.append((ds, layer, at["_name"], aj, "Point", json.dumps([round(g["x"], 6), round(g["y"], 6)]), g["x"], g["y"], g["x"], g["y"]))
        elif "paths" in g:
            paths = [[[round(p[0], 6), round(p[1], 6)] for p in path if ok(p[0], p[1])] for path in g["paths"]]
            paths = [p for p in paths if len(p) >= 2]
            pts = [p for path in paths for p in path]
            if not pts or not all(ok(*p) for p in pts[::max(1, len(pts) // 20)]):
                continue
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            out.append((ds, layer, at["_name"], aj, "LineString", json.dumps(paths), min(xs), min(ys), max(xs), max(ys)))
        elif "rings" in g:
            for poly in _poligonos([[[round(p[0], 6), round(p[1], 6)] for p in r if ok(p[0], p[1])] for r in g["rings"]]):
                xs, ys = [p[0] for p in poly[0]], [p[1] for p in poly[0]]
                if not ok(xs[0], ys[0]):
                    continue
                out.append((ds, layer, at["_name"], aj, "Polygon", json.dumps(poly), min(xs), min(ys), max(xs), max(ys)))
    return out


def main(fuentes):
    cli = Cliente(1.5)
    todas = []
    for fuente, clave, url, idc, campos, ds, layer, nombre, legible in CAPAS:
        if fuente not in fuentes:
            continue
        print(f"{fuente}/{clave}…", flush=True)
        carpeta = os.path.join(CACHE, fuente)
        try:
            feats = descargar_capa(cli, _u(url), clave, idc, campos, carpeta, tam_max=PAGINA.get(clave, 2000),
                                   simplificar=SIMPLIFICAR.get(clave))
        except Cortes as e:
            print("DETENIDO:", e)
            sys.exit(2)
        fecha = time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(os.path.join(carpeta, "cache", f"{clave}_meta.json"))))
        filas = filas_de(feats, ds, layer, nombre, legible, FUENTE[fuente], fecha)
        todas.append((fuente, filas))
        print(f"  {len(feats)} elementos -> {len(filas)} geometrías", flush=True)
    c = sqlite3.connect(DB)
    c.executescript("""CREATE TABLE IF NOT EXISTS features (id INTEGER PRIMARY KEY AUTOINCREMENT, dataset TEXT, layer TEXT, name TEXT,
        attrs_json TEXT, geom_type TEXT, geom_json TEXT, xmin REAL, ymin REAL, xmax REAL, ymax REAL);
        CREATE INDEX IF NOT EXISTS idx_bbox ON features(dataset, xmax, xmin, ymax, ymin);""")
    if not c.execute("SELECT 1 FROM sqlite_sequence WHERE name='features'").fetchone():
        c.execute("INSERT INTO sqlite_sequence(name, seq) VALUES ('features', 200000000)")  # ids sin choque con las otras bases
    with c:
        for fuente in {f for f, _ in todas}:
            c.execute("DELETE FROM features WHERE dataset LIKE ?", (fuente.upper() + "_%",))
        for fuente, filas in todas:
            c.executemany("INSERT INTO features(dataset, layer, name, attrs_json, geom_type, geom_json, xmin, ymin, xmax, ymax) "
                          "VALUES (?,?,?,?,?,?,?,?,?,?)", filas)
    for ds, n in c.execute("SELECT dataset, count(*) FROM features GROUP BY dataset"):
        print(f"{n:8}  {ds}")
    c.close()
    conteos = os.path.join(RAIZ, "datos", "gis", "_conteos.json")
    if os.path.isfile(conteos):
        os.remove(conteos)


if __name__ == "__main__":
    main(sys.argv[1:] or list(FUENTE))
