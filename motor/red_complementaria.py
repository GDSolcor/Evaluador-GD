"""
Base complementaria de red eléctrica (datos/red_or/red_complementaria.db), armada con lo descargado
de los geoportales de los operadores (herramientas/descargar_red_*.py). Mismo esquema que la
tabla features del GIS Solcor; gis._db() la une con una vista temporal.

ESSA (geoportal CREG 174) no publica las líneas de media tensión. Con sus transformadores, que sí
traen el circuito y la subestación, se arma:
  - ESSA_Transformadores           punto por transformador (circuito, kVA, tensión, disponibilidad CREG 174)
  - ESSA_Circuitos_estimados       trazado ESTIMADO de cada circuito: árbol de mínima distancia que une la
                                   subestación y los transformadores del circuito (tramos > 2,5 km se omiten).
                                   Sirve para ubicar el circuito, no es la red real.
  - ESSA_Subestaciones_CREG174     subestaciones (centroide del polígono publicado)

EBSA (visor GD-Connect): EBSA_Circuitos_GDC, tramos MT reales por circuito (herramientas/importar_ebsa.py).

CENS (servicio ArcGIS de su visor de disponibilidad): red MT real por tramo con su circuito.
  - CENS_Circuitos_MT        un elemento por circuito (todos sus tramos MT)
  - CENS_Transformadores     punto por transformador con la disponibilidad AGPE publicada
  - CENS_Subestaciones

EPM (geoportal público del Grupo EPM, Antioquia): EPM_Circuitos_MT (red MT real por circuito),
EPM_Transformadores (sin disponibilidad AGPE publicada), EPM_Subestaciones, EPM_Lineas_transmision.

EDEQ (mapa de disponibilidad público, Quindío): EDEQ_Transformadores (disponibilidad AGPE),
EDEQ_Circuitos_estimados (mismo método que ESSA), EDEQ_Subestaciones.

    python motor/red_complementaria.py
"""
import json
import math
import os
import sqlite3
import time

BASE = os.path.dirname(os.path.abspath(__file__))
DIR = os.path.join(BASE, "..", "datos", "red_or")
DB = os.path.join(DIR, "red_complementaria.db")
MAX_TRAMO_M = 2500


def _f(v):
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _arbol(nodos):
    """Prim O(n²) en metros (proyección local). nodos: [(lon, lat)] -> [(i, j, dist_m)]."""
    n = len(nodos)
    if n < 2:
        return []
    lat0 = sum(p[1] for p in nodos) / n
    kx = 111320 * math.cos(math.radians(lat0))
    xy = [(p[0] * kx, p[1] * 111320) for p in nodos]
    dentro = [False] * n
    dist = [float("inf")] * n
    padre = [-1] * n
    dist[0] = 0
    aristas = []
    for _ in range(n):
        u = min((i for i in range(n) if not dentro[i]), key=lambda i: dist[i])
        dentro[u] = True
        if padre[u] >= 0:
            aristas.append((padre[u], u, dist[u]))
        ux, uy = xy[u]
        for v in range(n):
            if not dentro[v]:
                d = math.hypot(xy[v][0] - ux, xy[v][1] - uy)
                if d < dist[v]:
                    dist[v], padre[v] = d, u
    return aristas


def _fila(layer, name, attrs, gt, geom):
    if gt == "Point":
        xs, ys = [geom[0]], [geom[1]]
    else:
        xs = [p[0] for path in geom for p in path]
        ys = [p[1] for path in geom for p in path]
    return ("RED_ELECTRICA", layer, name, json.dumps(attrs, ensure_ascii=False), gt,
            json.dumps(geom), min(xs), min(ys), max(xs), max(ys))


def filas_essa():
    d = os.path.join(DIR, "essa")
    filas = []
    if not os.path.isdir(d):
        return filas
    fecha = ""
    ruta = os.path.join(d, "autogenerador174_p1.json")
    subs_pos = {}
    if os.path.isfile(os.path.join(d, "subestaciones.json")):
        sj = json.load(open(os.path.join(d, "subestaciones.json"), encoding="utf-8"))
        for e in sj["elementos"]:
            p = e["props"]
            pts = [pt for parte in e["coords"] for pt in parte]
            if not pts:
                continue
            c = [round(sum(x for x, _ in pts) / len(pts), 7), round(sum(y for _, y in pts) / len(pts), 7)]
            nombre = (p.get("nombresub") or p.get("codigosub") or "").strip()
            subs_pos[str(p.get("codigosub", "")).strip()] = c
            filas.append(_fila("ESSA_Subestaciones_CREG174", nombre,
                               {"Subestación": nombre, "Código": p.get("codigosub"), "Capacidad total": p.get("cap_total"),
                                "Nivel de tensión": p.get("nvl_tension"), "Fuente": "Geoportal ESSA CREG 174 (" + sj.get("fecha", "") + ")"},
                               "Point", c))
    if not os.path.isfile(ruta):
        return filas
    tj = json.load(open(ruta, encoding="utf-8"))
    fecha = tj.get("fecha", "")
    por_circ = {}
    for e in tj["elementos"]:
        p = e["props"]
        circ = str(p.get("circuito") or "").strip() or "(sin circuito)"
        lon, lat = e["coords"] if isinstance(e["coords"][0], (int, float)) else e["coords"][0]
        a = {"Circuito": circ, "Transformador": p.get("codigo_transformador"), "Apoyo": p.get("codigo_apoyo_transformador"),
             "Capacidad (kVA)": p.get("capacidad_nominal_trafo_kva"), "Tensión de conexión": p.get("tension_conexion"),
             "Relación": p.get("relacion"), "Subestación": p.get("nombresubestacion"),
             "Potencia disponible AGPE (kW)": p.get("potencia_disponible_agpe"), "Capacidad limitante (kW)": p.get("capacidad_limitante_kw"),
             "Semáforo potencia instalada": p.get("semaforo_potenciainstalada"),
             "Actualización ESSA": p.get("fecha_actualizacion"), "Fuente": f"Geoportal ESSA CREG 174 ({fecha})"}
        filas.append(_fila("ESSA_Transformadores", circ, {k: v for k, v in a.items() if v not in (None, "")}, "Point", [lon, lat]))
        c = por_circ.setdefault(circ, dict(pts=[], sub=None, sub_nombre=p.get("nombresubestacion")))
        c["pts"].append((lon, lat))
        ls, las = _f(p.get("longitud_subestacion")), _f(p.get("latitud_subestacion"))
        if ls and las and -80 < ls < -66 and -5 < las < 14:
            c["sub"] = (ls, las)
        elif not c["sub"] and str(p.get("codigosubestacion", "")).strip() in subs_pos:
            c["sub"] = tuple(subs_pos[str(p.get("codigosubestacion")).strip()])
    for circ, c in por_circ.items():
        nodos = ([c["sub"]] if c["sub"] else []) + c["pts"]
        paths, largo, omitidos = [], 0.0, 0
        for i, j, dm in _arbol(nodos):
            if dm > MAX_TRAMO_M:
                omitidos += 1
                continue
            paths.append([list(nodos[i]), list(nodos[j])])
            largo += dm
        if not paths:
            continue
        filas.append(_fila("ESSA_Circuitos_estimados", circ,
                           {"Circuito": circ, "Subestación": c["sub_nombre"], "Transformadores": len(c["pts"]),
                            "Longitud estimada": f"{largo / 1000:.1f} km", "Tramos > 2,5 km omitidos": omitidos,
                            "Método": "Trazado ESTIMADO: árbol de mínima distancia entre la subestación y los transformadores del circuito (no es la red real)",
                            "Fuente": f"Geoportal ESSA CREG 174 ({fecha})"}, "LineString", paths))
    return filas


def filas_cens():
    """CENS (Grupo EPM): servicio ArcGIS del visor "Disponibilidad de la red Nivel 1"
    (herramientas/descargar_red_arcgis.py cens). Trae la red MT real por tramo con su circuito."""
    d = os.path.join(DIR, "cens")
    filas = []
    if not os.path.isfile(os.path.join(d, "tramos_mt.json")):
        return filas

    def cargar(n):
        r = os.path.join(d, n + ".json")
        return json.load(open(r, encoding="utf-8")) if os.path.isfile(r) else dict(elementos=[], fecha="")

    ok = lambda x, y: x is not None and y is not None and -80 < x < -66 and -5 < y < 14
    fuente = lambda j: f"Visor de disponibilidad CENS ({j.get('fecha', '')})"
    sj = cargar("subestaciones")
    for e in sj["elementos"]:
        a, g = e["attributes"], e.get("geometry") or {}
        if not ok(g.get("x"), g.get("y")):
            continue
        filas.append(_fila("CENS_Subestaciones", a.get("CODE") or "",
                           {k: v for k, v in {"Subestación": a.get("CODE"), "Dirección": a.get("ADDRESS"), "Relación": a.get("USER_"),
                                              "Tensión (kV)": a.get("MAIN_KV"), "Capacidad instalada (MVA)": a.get("MVA_INSTAL"),
                                              "Demanda pico (MW)": a.get("MW_PEAK"), "Fuente": fuente(sj)}.items() if v not in (None, "")},
                           "Point", [g["x"], g["y"]]))
    tj = cargar("transformadores")
    sub_de, disp = {}, {}
    for e in tj["elementos"]:
        a, g = e["attributes"], e.get("geometry") or {}
        circ = str(a.get("CIRCUITO_ALIMENTADOR") or "").strip() or "(sin circuito)"
        sub_de.setdefault(circ, a.get("SUBESTACION_CIRCUITO"))
        kw = _f(a.get("CAP_DISPON_AGPE_KW"))
        if kw is not None:
            disp[circ] = disp.get(circ, 0) + kw
        if not ok(g.get("x"), g.get("y")):
            continue
        at = {"Circuito": circ, "Transformador": a.get("CODIGO_TRANSFORMADOR"), "Subestación": a.get("SUBESTACION_CIRCUITO"),
              "Capacidad (kVA)": a.get("CAPACIDAD_INSTALADA_TRAFO"), "Tensión BT": a.get("TENSION_PT"),
              "Fases": a.get("FASES_DEL_TRANSFORMADOR"), "Tensión MT (kV)": a.get("TENSION_NOMINAL_SUB"),
              "Potencia disponible AGPE (kW)": a.get("CAP_DISPON_AGPE_KW"), "Disponible AGPE (%)": a.get("CAP_DISPON_AGPE_PORC"),
              "AGPE instalada (kW)": a.get("CAP_INSTALADA_AGPE"), "Límite inyección (kW)": a.get("LIM_POTENCIA_INYEC_KW"),
              "Disponibilidad potencia": a.get("DISPONIBILIDAD_POTENCIA"), "Disponibilidad energía": a.get("DISPONIBILIDAD_ENERGIA"),
              "Estado del circuito": a.get("ESTADO_CIRCUITO"), "Municipio": a.get("MUN_TRANSFOR"), "Fuente": fuente(tj)}
        filas.append(_fila("CENS_Transformadores", circ, {k: v for k, v in at.items() if v not in (None, "")}, "Point", [g["x"], g["y"]]))
    mj = cargar("tramos_mt")
    por_circ = {}
    for e in mj["elementos"]:
        a, g = e["attributes"], e.get("geometry") or {}
        paths = [[[round(p[0], 6), round(p[1], 6)] for p in path] for path in g.get("paths") or []]
        paths = [p for p in paths if len(p) >= 2 and all(ok(x, y) for x, y in p)]
        if not paths:
            continue
        circ = str(a.get("FPARENT") or "").strip() or "(sin circuito)"
        c = por_circ.setdefault(circ, dict(paths=[], largo=0.0, kv={}, pobl=set()))
        c["paths"] += paths
        c["largo"] += _f(a.get("LENGTH")) or 0
        kv = _f(a.get("KVNOM"))
        if kv:
            c["kv"][kv] = c["kv"].get(kv, 0) + 1
        if a.get("POBLACION"):
            c["pobl"].add(str(a["POBLACION"]).strip())
    for circ, c in por_circ.items():
        kv = max(c["kv"], key=c["kv"].get) if c["kv"] else None
        at = {"Circuito": circ, "Subestación": sub_de.get(circ), "Tensión (kV)": kv, "Tramos MT": len(c["paths"]),
              "Longitud MT": f"{c['largo'] / 1000:.1f} km",
              "Disponible AGPE en sus transformadores (kW)": round(disp[circ]) if circ in disp else None,
              "Zona": ", ".join({"R": "rural", "U": "urbana"}.get(p, p) for p in sorted(c["pobl"])[:6]) or None,
              "Fuente": fuente(mj)}
        filas.append(_fila("CENS_Circuitos_MT", circ, {k: v for k, v in at.items() if v not in (None, "")}, "LineString", c["paths"]))
    return filas


def filas_epm():
    """EPM (Antioquia): geoportal público del Grupo EPM (herramientas/descargar_red_arcgis.py epm).
    Red MT real por tramo con circuito y subestación; transformadores sin disponibilidad AGPE publicada."""
    d = os.path.join(DIR, "epm")
    filas = []
    if not os.path.isfile(os.path.join(d, "tramos_mt.json")):
        return filas

    def cargar(n):
        r = os.path.join(d, n + ".json")
        return json.load(open(r, encoding="utf-8")) if os.path.isfile(r) else dict(elementos=[], fecha="")

    ok = lambda x, y: x is not None and y is not None and -80 < x < -66 and -5 < y < 14
    fuente = lambda j: f"Geoportal Grupo EPM ({j.get('fecha', '')})"
    limpio = lambda at: {k: v for k, v in at.items() if v not in (None, "", " ")}
    sj = cargar("subestaciones")
    for e in sj["elementos"]:
        a, g = e["attributes"], e.get("geometry") or {}
        pts = [p for r in g.get("rings") or [] for p in r]
        if not pts:
            continue
        c = [round(sum(p[0] for p in pts) / len(pts), 6), round(sum(p[1] for p in pts) / len(pts), 6)]
        if not ok(*c):
            continue
        filas.append(_fila("EPM_Subestaciones", a.get("NOMBRE_SUB") or a.get("CODIGO") or "",
                           limpio({"Subestación": a.get("NOMBRE_SUB"), "Código": a.get("CODIGO"),
                                   "Transformadores de potencia": a.get("CAN_TRANSFORMADORES"),
                                   "Mercado": a.get("MERCADO"), "Fuente": fuente(sj)}), "Point", c))

    def por_circuito(j, campo, capa, extra):
        por = {}
        for e in j["elementos"]:
            a, g = e["attributes"], e.get("geometry") or {}
            paths = [[[round(p[0], 6), round(p[1], 6)] for p in path] for path in g.get("paths") or []]
            paths = [p for p in paths if len(p) >= 2 and all(ok(x, y) for x, y in p)]
            if not paths:
                continue
            circ = str(a.get(campo) or "").strip() or "(sin circuito)"
            c = por.setdefault(circ, dict(paths=[], largo=0.0, sub={}, kv={}, mun=set(), zona=set()))
            c["paths"] += paths
            c["largo"] += _f(a.get("LONGITUD_CONN")) or 0
            for k, v in (("sub", a.get("SUBESTACION_CONN")), ("kv", a.get("TENSION_CONN"))):
                if v:
                    c[k][v] = c[k].get(v, 0) + 1
            if a.get("MUNICIPIO"):
                c["mun"].add(a["MUNICIPIO"])
            if a.get("CLASIFICACION_MERCADO"):
                c["zona"].add(a["CLASIFICACION_MERCADO"].lower())
        for circ, c in por.items():
            moda = lambda m: max(m, key=m.get) if m else None
            at = {"Circuito": circ, "Subestación": moda(c["sub"]), "Tensión (kV)": moda(c["kv"]),
                  "Tramos": len(c["paths"]), "Longitud": f"{c['largo'] / 1000:.1f} km",
                  "Municipios": ", ".join(sorted(c["mun"])[:6]) or None, "Zona": ", ".join(sorted(c["zona"])) or None}
            at.update(extra(circ))
            at["Fuente"] = fuente(j)
            filas.append(_fila(capa, circ, limpio(at), "LineString", c["paths"]))

    tj = cargar("transformadores")
    kva = {}
    for e in tj["elementos"]:
        a, g = e["attributes"], e.get("geometry") or {}
        circ = str(a.get("CIRCUITO_CONN") or "").strip() or "(sin circuito)"
        kva[circ] = kva.get(circ, 0) + (_f(a.get("CAPACIDAD_NOMINAL")) or 0)
        if not ok(g.get("x"), g.get("y")):
            continue
        filas.append(_fila("EPM_Transformadores", circ, limpio({
            "Circuito": circ, "Transformador": a.get("NRO_TRANSFORMADOR") or a.get("CODIGO"), "Subestación": a.get("SUBESTACION_CONN"),
            "Capacidad (kVA)": a.get("CAPACIDAD_NOMINAL"), "Tensión (kV)": a.get("TENSION_CONN"), "Fases": a.get("NUMERO_FASES"),
            "Cargabilidad (%)": a.get("PORCENTAJE_CARGA"), "Municipio": a.get("MUNICIPIO"),
            "Zona": (a.get("CLASIFICACION_MERCADO") or "").lower() or None, "Fuente": fuente(tj)}), "Point", [g["x"], g["y"]]))
    por_circuito(cargar("tramos_mt"), "CIRCUITO_CONN", "EPM_Circuitos_MT",
                 lambda circ: {"Transformadores instalados (kVA)": round(kva[circ]) if circ in kva else None})
    por_circuito(cargar("lineas_transmision"), "CIRCUITO_CONN", "EPM_Lineas_transmision", lambda circ: {})
    return filas


def filas_edeq():
    """EDEQ (Quindío): mapa de disponibilidad público (herramientas/descargar_red_edeq.py). No publica
    líneas: como en ESSA, el trazado del circuito se ESTIMA uniendo subestación y transformadores."""
    d = os.path.join(DIR, "edeq")
    filas = []
    if not os.path.isfile(os.path.join(d, "transformers.json")):
        return filas
    fecha = time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(os.path.join(d, "transformers.json"))))
    fuente = f"Mapa de disponibilidad EDEQ ({fecha})"
    ok = lambda x, y: x is not None and y is not None and -80 < x < -66 and -5 < y < 14
    subs = {}
    for s in json.load(open(os.path.join(d, "substations.json"), encoding="utf-8"))["userData"]:
        lon, lat = _f(s.get("LONGITUD")), _f(s.get("LATITUD"))
        if not ok(lon, lat):
            continue
        nombre = " ".join(str(s.get("NOMBRE_SUB") or s.get("CODIGO")).split())
        subs[nombre.upper()] = (lon, lat)
        filas.append(_fila("EDEQ_Subestaciones", nombre, {"Subestación": nombre, "Código": s.get("CODIGO"),
                                                         "Ubicación": s.get("UBICACION"), "Fuente": fuente}, "Point", [lon, lat]))
    por_circ = {}
    for t in json.load(open(os.path.join(d, "transformers.json"), encoding="utf-8"))["userData"]:
        cod = str(t.get("TRANSFORMADOR") or "").strip()
        lon, lat = _f(t.get("LONGITUD")), _f(t.get("LATITUD"))
        if not cod or not ok(lon, lat):
            continue
        r = os.path.join(d, "detalle", "".join(c for c in cod if c.isalnum() or c in "-_") + ".json")
        det = json.load(open(r, encoding="utf-8")) if os.path.isfile(r) else None
        if not det:
            continue  # sin detalle aún (descarga en curso): no se sabe el circuito
        ip, pot = det.get("identificacion_punto_conexion") or {}, det.get("datos_potencia") or {}
        e1, e2 = det.get("energia_tsfv") or {}, det.get("energia_ntsfv") or {}
        circ = " ".join(str(ip.get("circuito") or "").split()) or "(sin circuito)"
        sub = " ".join(str(ip.get("subestacion") or "").split())
        a = {"Circuito": circ, "Transformador": cod, "Subestación": sub, "Capacidad (kVA)": ip.get("capacidad_nominal_kva"),
             "Fases": ip.get("fases"), "Tensión BT (V)": ip.get("voltaje_nominal"),
             "Potencia máxima AGPE (kW)": pot.get("potencia_maxima"), "Semáforo potencia": pot.get("formula_color"),
             "Energía disponible con FV (kW)": e1.get("energia_maxima_tsfv"), "Semáforo energía FV": e1.get("formula_color_tsfv"),
             "Fuente": fuente}
        filas.append(_fila("EDEQ_Transformadores", circ, {k: v for k, v in a.items() if v not in (None, "")}, "Point", [lon, lat]))
        c = por_circ.setdefault(circ, dict(pts=[], sub=sub))
        c["pts"].append((lon, lat))
    for circ, c in por_circ.items():
        pos = subs.get(c["sub"].upper())
        nodos = ([pos] if pos else []) + c["pts"]
        paths, largo, omitidos = [], 0.0, 0
        for i, j, dm in _arbol(nodos):
            if dm > MAX_TRAMO_M:
                omitidos += 1
                continue
            paths.append([list(nodos[i]), list(nodos[j])])
            largo += dm
        if paths:
            filas.append(_fila("EDEQ_Circuitos_estimados", circ,
                               {"Circuito": circ, "Subestación": c["sub"], "Transformadores": len(c["pts"]),
                                "Longitud estimada": f"{largo / 1000:.1f} km", "Tramos > 2,5 km omitidos": omitidos,
                                "Método": "Trazado ESTIMADO: árbol de mínima distancia entre la subestación y los transformadores del circuito (no es la red real)",
                                "Fuente": fuente}, "LineString", paths))
    return filas


def filas_celsia():
    """Celsia (Tolima y Valle del Cauca): Excel público "Transformadores de distribución CELSIA"
    (celsia.com > Solicitud de conexión para autogeneradores; archivo de dic-2021). Trae ubicación,
    kVA, tensión primaria y autogeneración instalada, pero NO el circuito: solo puntos de red."""
    import re
    import zipfile
    import xml.etree.ElementTree as ET
    ruta = os.path.join(DIR, "celsia", "Transformadores-de-distribucion-CELSIA.xlsx")
    filas = []
    if not os.path.isfile(ruta):
        return filas
    M = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    z = zipfile.ZipFile(ruta)
    ss = ["".join(t.text or "" for t in si.iter(M + "t")) for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(M + "si")]
    hojas = [s.get("name") for s in ET.fromstring(z.read("xl/workbook.xml")).find(M + "sheets")]
    for k, hoja in enumerate(hojas, 1):
        region = "Tolima" if "tolima" in hoja.lower() else "Valle del Cauca"
        cab = None
        for _, el in ET.iterparse(z.open(f"xl/worksheets/sheet{k}.xml")):
            if not el.tag.endswith("}row"):
                continue
            vals = {}
            for c in el:
                v = c.find(M + "v")
                t = v.text if v is not None else ""
                col = re.sub(r"\d", "", c.get("r", ""))
                vals[col] = ss[int(t)] if c.get("t") == "s" and t else t
            el.clear()
            if cab is None:
                cab = {col: str(n).strip().upper() for col, n in vals.items()}
                continue
            r = {cab.get(col, col): v for col, v in vals.items()}
            lon, lat = _f(r.get("LONGITUD")), _f(r.get("LATITUD"))
            if lon is None or lat is None or not (-80 < lon < -66 and -5 < lat < 14):
                continue
            tension = " ".join(str(r.get("TENSIÓN PRIMARIA") or "").replace(",", ".").split()).upper() or "tensión sin dato"
            a = {"Zona": region, "Transformador": r.get("CODIGO_GIS"), "Matrícula": r.get("MATRICULA"),
                 "Capacidad (kVA)": _f(r.get("POTENCIA NOMINAL")), "Tensión primaria": tension,
                 "Tensión secundaria": r.get("TENSIÓN SECUNDARIA"),
                 "Autogeneración instalada (kW)": _f(r.get("CAPACIDAD INSTALADA DE AUTOGENERACIÓN")),
                 "Autogeneración instalada (%)": _f(r.get("CAPACIDAD INSTALADA (%)")),
                 "Fuente": "Excel público de transformadores Celsia (dic-2021; sin circuito)"}
            filas.append(_fila("CELSIA_Transformadores", f"{region} · {tension}", {k2: v for k2, v in a.items() if v not in (None, "")},
                               "Point", [round(lon, 6), round(lat, 6)]))
    return filas


def construir():
    os.makedirs(DIR, exist_ok=True)
    filas = filas_essa() + filas_cens() + filas_epm() + filas_edeq() + filas_celsia()
    # otros OR: datos/red_or/<or>/filas.json con filas ya armadas (importar_ebsa.py, importar_red_shp.py)
    for extra in sorted(d for d in os.listdir(DIR) if os.path.isfile(os.path.join(DIR, d, "filas.json"))):
        r = os.path.join(DIR, extra, "filas.json")
        if os.path.isfile(r):
            filas += [tuple(f) for f in json.load(open(r, encoding="utf-8"))]
    tmp = DB + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    c = sqlite3.connect(tmp)
    c.executescript("""CREATE TABLE features (id INTEGER PRIMARY KEY AUTOINCREMENT, dataset TEXT, layer TEXT, name TEXT,
        attrs_json TEXT, geom_type TEXT, geom_json TEXT, xmin REAL, ymin REAL, xmax REAL, ymax REAL);""")
    # ids altos para no chocar con los del GIS Solcor en la vista unida
    c.execute("INSERT INTO sqlite_sequence(name, seq) VALUES ('features', 100000000)")
    c.executemany("INSERT INTO features(dataset, layer, name, attrs_json, geom_type, geom_json, xmin, ymin, xmax, ymax) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?)", filas)
    c.execute("CREATE INDEX idx_bbox ON features(dataset, xmax, xmin, ymax, ymin)")
    c.execute("CREATE TABLE meta(clave TEXT PRIMARY KEY, valor TEXT)")
    c.execute("INSERT INTO meta VALUES ('construida', ?)", (time.strftime("%Y-%m-%d %H:%M"),))
    c.commit()
    resumen = list(c.execute("SELECT layer, count(*), count(distinct name) FROM features GROUP BY layer"))
    c.close()
    try:
        os.replace(tmp, DB)
    except PermissionError:
        # el evaluador está abierto y tiene la base en uso: se actualiza en su sitio en una transacción
        c = sqlite3.connect(DB, timeout=30)
        c.execute("ATTACH DATABASE ? AS nueva", (tmp,))
        with c:
            c.execute("DELETE FROM features")
            c.execute("INSERT INTO features SELECT * FROM nueva.features")
            c.execute("DELETE FROM meta")
            c.execute("INSERT INTO meta SELECT * FROM nueva.meta")
        c.execute("DETACH DATABASE nueva")
        c.close()
        os.remove(tmp)
    conteos = os.path.join(BASE, "..", "datos", "gis", "_conteos.json")
    if os.path.isfile(conteos):
        os.remove(conteos)  # se recalcula con la red nueva
    return resumen


if __name__ == "__main__":
    for capa, n, nombres in construir():
        print(f"{n:7} elementos  {nombres:5} nombres  {capa}")
    import red_duplicados  # capas/tramos repetidos entre la base original y la complementaria
    d = red_duplicados.calcular()
    red_duplicados.guardar(d)
    print(f"duplicados ocultos: {sum(1 for x in d if x[0] == 'capa')} capas, {sum(1 for x in d if x[0] == 'elemento')} tramos")
