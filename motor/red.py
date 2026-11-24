"""
Red electrica del GIS Solcor organizada por operador de red (OR) -> tipo de red -> circuito -> tramo.

La base solo trae el atributo _name por elemento, asi que la jerarquia se deduce de la capa de
origen (layer) y del nombre:
  ENEL  Redes_<zona>: cada elemento es un circuito completo (CTO_NEUSA--TS12D)
        Redes_ENEL_MT: red MT sin nombre de circuito
  EBSA  CTO_14xxx(.kml): cada capa es un circuito; sus elementos son tramos (TRMVLS6874)
        Redes_34,5: circuitos 34,5 kV por nombre; LINEAS_34: tramos 34,5 kV
        LINEAS_115_kV / LINEAS_230kV: lineas de transmision por nombre (LN 312)
  ESSA  solo subestaciones y apoyos 34,5 kV
El operador sigue la misma regla que core/query_engine.py del paquete GIS Solcor.
"""
import json
import math
import re
import threading

import gis

_ZONAS = {"Ubate": "Ubaté", "Bogotá": "Bogotá", "Gualivá": "Gualivá", "Sabana_Centro": "Sabana Centro",
          "Sabana_Occidente": "Sabana Occidente", "Alto_Magdalena": "Alto Magdalena",
          "Bajo_Magdalena": "Bajo Magdalena", "Magdalena_Centro": "Magdalena Centro"}
COLORES = {"ENEL": "#f59e0b", "EBSA": "#ff5a1f", "ESSA": "#a3e635", "CENS": "#c084fc", "EPM": "#34d399", "EDEQ": "#f472b6", "CELSIA": "#fb7185", "ENERCA": "#facc15", "EMSA": "#e879f9", "AIR-E": "#8b5cf6", "AFINIA": "#2dd4bf", "CHEC": "#84cc16", "EEP": "#06b6d4", "sin identificar": "#cbd5e1"}
# capas de motor/red_complementaria.py (geoportales de los OR): atributos ya legibles
PREFIJOS_OR = ("ESSA_", "EBSA_", "CENS_", "EPM_", "EDEQ_", "CELSIA_", "ENERCA_", "EMSA_", "AIRE_", "AFINIA_", "CHEC_", "EEP_")
CAPAS_TRAFO = ("ESSA_Transformadores", "CENS_Transformadores", "EPM_Transformadores", "EDEQ_Transformadores",
               "CELSIA_Transformadores", "CHEC_Transformadores", "EEP_Transformadores")


def clasificar(layer, geom_type):
    """(operador, grupo, tension, circuito_por_nombre) para una capa de RED_ELECTRICA."""
    L = layer.upper()
    # capas completadas desde los geoportales CREG 174 de los OR (motor/red_complementaria.py)
    if L == "ESSA_TRANSFORMADORES":
        return "ESSA", "Transformadores por circuito (geoportal CREG 174)", "MT/BT", True
    if L == "ESSA_CIRCUITOS_ESTIMADOS":
        return "ESSA", "Circuitos MT · trazado estimado", "MT", True
    if L == "ESSA_SUBESTACIONES_CREG174":
        return "ESSA", "Subestaciones (geoportal CREG 174)", "", False
    if L.startswith("EBSA_CIRCUITOS_GDC"):
        return "EBSA", "Circuitos MT (geoportal GD-Connect)", "MT", True
    if L in ("ENERCA_CIRCUITOS_GDC", "EMSA_CIRCUITOS_GDC"):
        return L.split("_")[0], "Circuitos MT (geoportal GD-Connect)", "MT", True
    if L == "AIRE_CIRCUITOS_MT":
        return "AIR-E", "Circuitos MT (shapefile del operador)", "MT", True
    if L == "AFINIA_CIRCUITOS_MT":
        return "AFINIA", "Circuitos (visor CREG 174)", "MT", True
    if L == "CHEC_CIRCUITOS_MT":
        return "CHEC", "Circuitos MT (visor CREG 174)", "MT", True
    if L == "CHEC_TRANSFORMADORES":
        return "CHEC", "Transformadores por circuito (disponibilidad AGPE)", "MT/BT", True
    if L == "EEP_CIRCUITOS_MT":
        return "EEP", "Circuitos MT (portal de disponibilidad)", "MT", True
    if L == "EEP_TRANSFORMADORES":
        return "EEP", "Transformadores por circuito", "MT/BT", True
    if L == "EEP_SUBESTACIONES":
        return "EEP", "Subestaciones (portal de disponibilidad)", "", False
    if L == "CHEC_LINEAS_AT":
        return "CHEC", "Líneas de alta tensión (115 kV y más)", "AT", True
    if L == "CHEC_SUBESTACIONES":
        return "CHEC", "Subestaciones (visor CREG 174)", "", False
    if L == "CENS_CIRCUITOS_MT":
        return "CENS", "Circuitos MT (visor de disponibilidad)", "MT", True
    if L == "CENS_TRANSFORMADORES":
        return "CENS", "Transformadores por circuito (disponibilidad AGPE)", "MT/BT", True
    if L == "CENS_SUBESTACIONES":
        return "CENS", "Subestaciones (visor de disponibilidad)", "", False
    if L == "EPM_CIRCUITOS_MT":
        return "EPM", "Circuitos MT (geoportal Grupo EPM)", "MT", True
    if L == "EPM_TRANSFORMADORES":
        return "EPM", "Transformadores por circuito", "MT/BT", True
    if L == "EPM_SUBESTACIONES":
        return "EPM", "Subestaciones (geoportal Grupo EPM)", "", False
    if L == "EPM_LINEAS_TRANSMISION":
        return "EPM", "Líneas de transmisión", "AT", True
    if L == "EDEQ_TRANSFORMADORES":
        return "EDEQ", "Transformadores por circuito (disponibilidad AGPE)", "MT/BT", True
    if L == "EDEQ_CIRCUITOS_ESTIMADOS":
        return "EDEQ", "Circuitos MT · trazado estimado", "MT", True
    if L == "CELSIA_TRANSFORMADORES":
        return "CELSIA", "Transformadores por zona y tensión (Excel 2021, sin circuito)", "MT/BT", True
    if L == "EDEQ_SUBESTACIONES":
        return "EDEQ", "Subestaciones (mapa de disponibilidad)", "", False
    if geom_type == "Point":
        if "ESSA" in L and "APOYO" in L:
            return "ESSA", "Apoyos 34,5 kV", "34,5 kV", False
        if "ESSA" in L:
            return "ESSA", "Subestaciones", "", False
        if "ENEL" in L:
            return "ENEL", "Subestaciones", "", False
        if "115" in L:
            return "EBSA", "Subestaciones 115 kV", "115 kV", False
        if "34_5" in L or "13_2" in L:
            return "EBSA", "Subestaciones 34,5/13,2 kV", "34,5 / 13,2 kV", False
        return "sin identificar", "Subestaciones", "", False
    if L == "REDES_ENEL_MT":
        return "ENEL", "Red MT (sin nombre de circuito)", "MT", False
    if L == "COTA":
        return "ENEL", "Circuitos MT · Cota", "MT", True
    if L.startswith("REDES_34"):
        return "EBSA", "Circuitos 34,5 kV", "34,5 kV", True
    if L.startswith("REDES_"):
        zona = layer[6:]
        return "ENEL", f"Circuitos MT · {_ZONAS.get(zona, zona.replace('_', ' '))}", "MT", True
    # LINEAS_* del respaldo GIS Solcor son de Santander (red ESSA), no de Boyacá: >90 % de sus tramos caen en
    # Santander y el resto en municipios vecinos donde ESSA llega (ticket #44)
    if L == "LINEAS_34":
        return "ESSA", "Líneas 34,5 kV (tramos)", "34,5 kV", False
    if L.startswith("LINEAS_115"):
        return "ESSA", "Líneas 115 kV", "115 kV", True
    if L.startswith("LINEAS_230"):
        return "ESSA", "Líneas 230 kV (STN)", "230 kV", True
    if L.startswith("CTO_1"):
        return "EBSA", "Circuitos MT (por tramos)", "MT", False
    return "sin identificar", layer, "", False


def _nombre_circuito_capa(layer):
    return re.sub(r"\.(kml|kmz)$", "", layer, flags=re.I)


def clave(layer, name, geom_type):
    _, _, _, por_nombre = clasificar(layer, geom_type)
    return f"{layer}|{name}" if por_nombre else f"{layer}|*"


_arbol = None
_lock = threading.Lock()


def arbol():
    """Operador -> grupo -> circuitos (clave, nombre, elementos, bbox). Se calcula una vez."""
    global _arbol
    with _lock:
        if _arbol is not None:
            return _arbol
        if not gis.disponible():
            _arbol = dict(disponible=False, operadores=[])
            return _arbol
        circ = {}
        # el encuadre ignora elementos fuera de Colombia (la base trae algunos tramos con coordenadas erradas,
        # p. ej. un tramo de CTO_14522 en -83,05 / -4,39) para que "acercar al circuito" no muestre medio continente
        for layer, name, gt, x0, y0, x1, y1 in gis._db().execute(
                "SELECT layer, name, geom_type, min(xmin), min(ymin), max(xmax), max(ymax) FROM features "
                "WHERE dataset='RED_ELECTRICA' AND xmin > -79.5 AND xmax < -66.5 AND ymin > -4.3 AND ymax < 13.6 "
                "GROUP BY layer, CASE WHEN geom_type='Point' AND layer NOT IN "
                f"({','.join(repr(c) for c in CAPAS_TRAFO)}) THEN '' ELSE name END, geom_type"):
            op, grupo, tension, por_nombre = clasificar(layer, gt)
            k = clave(layer, name, gt)
            nombre = (name or "(sin nombre)") if por_nombre else _nombre_circuito_capa(layer)
            if gt == "Point" and not por_nombre:
                nombre = {"Subestaciones": "Subestaciones", "Apoyos 34,5 kV": "Apoyos"}.get(grupo, grupo)
            c = circ.setdefault((op, grupo, k), dict(clave=k, nombre=nombre, tension=tension, tipo=gt,
                                                    bbox=[x0, y0, x1, y1], elementos=0,
                                                    fuente="geoportal OR" if layer.startswith(PREFIJOS_OR) else "GIS Solcor"))
            c["bbox"] = [min(c["bbox"][0], x0), min(c["bbox"][1], y0), max(c["bbox"][2], x1), max(c["bbox"][3], y1)]
        for (layer, name, gt, n) in gis._db().execute(
                "SELECT layer, name, geom_type, count(*) FROM features WHERE dataset='RED_ELECTRICA' GROUP BY layer, name, geom_type"):
            op, grupo, tension, por_nombre = clasificar(layer, gt)
            k = clave(layer, name, gt)
            if (op, grupo, k) not in circ:  # todos sus elementos estan fuera de Colombia
                circ[(op, grupo, k)] = dict(clave=k, nombre=(name or "(sin nombre)") if por_nombre else _nombre_circuito_capa(layer),
                                            tension=tension, tipo=gt, bbox=None, elementos=0, fuente="")
            circ[(op, grupo, k)]["elementos"] += n
        ops = {}
        for (op, grupo, _), c in circ.items():
            ops.setdefault(op, {}).setdefault(grupo, []).append(c)
        orden = ["ENEL", "EBSA", "ESSA", "CENS", "EPM", "EDEQ", "CELSIA", "ENERCA", "EMSA", "AIR-E", "AFINIA", "CHEC", "EEP", "sin identificar"]
        _arbol = dict(disponible=True, operadores=[
            dict(id=op, color=COLORES.get(op, "#cbd5e1"), grupos=[
                dict(id=f"{op}::{g}", nombre=g, circuitos=sorted(cs, key=lambda c: c["nombre"]),
                     elementos=sum(c["elementos"] for c in cs), subestaciones=cs[0]["tipo"] == "Point" and "Transformadores" not in g)
                for g, cs in sorted(ops[op].items(), key=lambda kv: (not kv[0].startswith("Sub"), kv[0]))])
            for op in orden if op in ops])
        return _arbol


def cercano(lon, lat, radio_m=5000, n=6):
    """Líneas y subestaciones de la red más cercanas a un punto (para ubicar el punto de conexión).
    Devuelve la distancia y el punto más cercano sobre cada línea (para 'ajustar a la red')."""
    if not gis.disponible():
        return []
    mx = 111320 * math.cos(math.radians(lat))
    d = radio_m / 111320
    out = []
    for layer, name, gt, gj in gis._db().execute(
            "SELECT layer, name, geom_type, geom_json FROM features WHERE dataset='RED_ELECTRICA' "
            "AND xmax>=? AND xmin<=? AND ymax>=? AND ymin<=?", (lon - d * 1.2, lon + d * 1.2, lat - d, lat + d)):
        g = json.loads(gj)
        op, grupo, tension, por_nombre = clasificar(layer, gt)
        if gt == "Point":
            best = (math.hypot((g[0] - lon) * mx, (g[1] - lat) * 111320), g)
        else:
            best = (float("inf"), None)
            for path in g:
                for i in range(len(path) - 1):
                    ax, ay = (path[i][0] - lon) * mx, (path[i][1] - lat) * 111320
                    bx, by = (path[i + 1][0] - lon) * mx, (path[i + 1][1] - lat) * 111320
                    dx, dy = bx - ax, by - ay
                    L = dx * dx + dy * dy
                    t = 0 if L == 0 else max(0, min(1, -(ax * dx + ay * dy) / L))
                    px, py = ax + t * dx, ay + t * dy
                    dist = math.hypot(px, py)
                    if dist < best[0]:
                        best = (dist, [lon + px / mx, lat + py / 111320])
        if best[0] <= radio_m:
            nombre = (name or "(sin nombre)") if (por_nombre or gt == "Point") else _nombre_circuito_capa(layer)
            out.append(dict(operador=op, tipo_red=grupo, tension=tension, nombre=nombre,
                            tramo=None if (por_nombre or gt == "Point") else name,
                            es_subestacion=gt == "Point" and "Transformadores" not in grupo,
                            es_transformador=gt == "Point" and "Transformadores" in grupo,
                            distancia_m=round(best[0], 1), punto=best[1]))
    out.sort(key=lambda r: r["distancia_m"])
    unicos, vistos = [], set()
    for r in out:  # la misma línea aparece en la capa del circuito y en la general del OR (misma distancia)
        k = (r["operador"], r["nombre"], r["es_subestacion"])
        k2 = (r["operador"], r["es_subestacion"], round(r["distancia_m"]))
        if k not in vistos and k2 not in vistos:
            vistos.update((k, k2))
            unicos.append(r)
    return unicos[:n]


def features(claves, bbox, limite=6000):
    """Elementos de los circuitos seleccionados (claves 'layer|name' o 'layer|*') en la vista."""
    sel = set(claves or [])
    if not sel or not gis.disponible():
        return dict(type="FeatureCollection", features=[], truncado=False)
    capas = sorted({k.split("|", 1)[0] for k in sel})
    x0, y0, x1, y1 = bbox
    q = ("SELECT layer, name, geom_type, geom_json, attrs_json FROM features WHERE dataset='RED_ELECTRICA' "
         f"AND layer IN ({','.join('?' * len(capas))}) AND xmax>=? AND xmin<=? AND ymax>=? AND ymin<=?")
    feats, trunc = [], False
    for layer, name, gt, gj, aj in gis._db().execute(q, (*capas, x0, x1, y0, y1)):
        k = clave(layer, name, gt)
        if k not in sel:
            continue
        if len(feats) >= limite:
            trunc = True
            break
        op, grupo, tension, por_nombre = clasificar(layer, gt)
        g = json.loads(gj)
        props = {"Operador (OR)": op, "Tipo de red": grupo}
        complemento = layer.startswith(PREFIJOS_OR)
        if complemento:  # capas de geoportal: atributos ya legibles, guardados por red_complementaria.py
            a = json.loads(aj or "{}")
            props.update({k: v for k, v in a.items() if not k.startswith("_") and v not in ("", None)})
            geom = dict(type="Point", coordinates=g) if gt == "Point" else dict(type="MultiLineString", coordinates=g)
            feats.append(dict(type="Feature", geometry=geom, properties=dict(props, _op=op, _sub="Subestaciones" in grupo,
                                                                            _trafo=gt == "Point" and "Transformadores" in grupo)))
            continue
        if gt == "Point":
            props["Subestación" if "Subest" in grupo else "Apoyo"] = name or "(sin nombre)"
            geom = dict(type="Point", coordinates=g)
        else:
            if por_nombre:
                props["Circuito / línea"] = name or "(sin nombre)"
            else:
                props["Circuito"] = _nombre_circuito_capa(layer)
                props["Tramo"] = name or "(sin nombre)"
            geom = dict(type="MultiLineString", coordinates=g)
        if tension:
            props["Tensión"] = tension
        props["Capa de origen"] = layer
        feats.append(dict(type="Feature", geometry=geom, properties=dict(props, _op=op, _sub=gt == "Point")))
    return dict(type="FeatureCollection", features=feats, truncado=trunc)
