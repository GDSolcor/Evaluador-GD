"""
Importa la red de CHEC (Caldas, Risaralda y municipios vecinos) recorrida desde su visor público de la Resolución
CREG 174 (https://portal.almeraim.com/sgichec/PAUTOG/external/r174). El visor pide un token tras un reCAPTCHA, así
que la descarga se hace DENTRO de esa página en Chrome (recolector en la consola, una petición a la vez con pausa),
que deja en Descargas:
    chec_subestaciones.json   RED_TOPOLOGICA_RES_174/FeatureServer/5 (polígonos)
    chec_trafos.json          TRAFOS_AUTOGENERADORES/FeatureServer/0 (puntos, capacidad y circuito)
    chec_red_primaria.json    RED_TOPOLOGICA_RES_174/FeatureServer/4 (tramos MT con circuito)

Este script los copia a datos/red_or/chec/ y arma datos/red_or/chec/filas.json (subestaciones, transformadores y un
elemento por circuito MT) para motor/red_complementaria.py:

    python herramientas/importar_chec.py [carpeta con los .json; por defecto Descargas]
    python motor/red_complementaria.py
"""
import json
import os
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DESTINO = os.path.join(BASE, "datos", "red_or", "chec")
ARCHIVOS = ("chec_subestaciones.json", "chec_trafos.json", "chec_red_primaria.json")


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _ok(x, y):
    return x is not None and y is not None and -80 < x < -66 and -5 < y < 14


def _fila(capa, nombre, attrs, gt, geom):
    if gt == "Point":
        xs, ys = [geom[0]], [geom[1]]
    else:
        xs = [p[0] for path in geom for p in path]
        ys = [p[1] for path in geom for p in path]
    return ["RED_ELECTRICA", capa, nombre, json.dumps({k: v for k, v in attrs.items() if v not in (None, "")}, ensure_ascii=False),
            gt, json.dumps(geom), min(xs), min(ys), max(xs), max(ys)]


def main():
    origen = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.expanduser("~"), "Downloads")
    os.makedirs(DESTINO, exist_ok=True)
    for a in ARCHIVOS:
        src = os.path.join(origen, a)
        if os.path.isfile(src) and os.path.abspath(src) != os.path.abspath(os.path.join(DESTINO, a)):
            shutil.copy2(src, os.path.join(DESTINO, a))
    faltan = [a for a in ARCHIVOS if not os.path.isfile(os.path.join(DESTINO, a))]
    if faltan:
        raise SystemExit(f"Faltan {', '.join(faltan)} en {origen} ni en {DESTINO}")
    cargar = lambda a: json.load(open(os.path.join(DESTINO, a), encoding="utf-8"))  # noqa: E731
    filas = []

    sj = cargar("chec_subestaciones.json")
    fuente = f"Visor CREG 174 de CHEC ({sj.get('fecha', '')})"
    for e in sj["features"]:
        a, g = e["attributes"], e.get("geometry") or {}
        pts = [p for r in g.get("rings") or [] for p in r]
        if not pts:
            continue
        c = [round(sum(p[0] for p in pts) / len(pts), 6), round(sum(p[1] for p in pts) / len(pts), 6)]
        if not _ok(*c):
            continue
        nombre = (a.get("NOMBRE_SUB") or a.get("CODIGO") or "").strip()
        filas.append(_fila("CHEC_Subestaciones", nombre, {"Subestación": nombre, "Código": a.get("CODIGO"), "Estado": a.get("ESTADO"),
                                                         "Fuente": fuente}, "Point", c))

    tj = cargar("chec_trafos.json")
    fuente = f"Visor CREG 174 de CHEC ({tj.get('fecha', '')})"
    cap_circ, mun_circ = {}, {}
    for e in tj["features"]:
        a, g = e["attributes"], e.get("geometry") or {}
        circ = str(a.get("CIRCUITO") or "").strip() or "(sin circuito)"
        cap_circ[circ] = cap_circ.get(circ, 0) + (_f(a.get("CAPACIDAD_NOMINAL_TRAFO")) or 0)
        if a.get("MUNICIPIO"):
            mun_circ.setdefault(circ, {}).setdefault(a["MUNICIPIO"].title(), 0)
            mun_circ[circ][a["MUNICIPIO"].title()] += 1
        if not _ok(g.get("x"), g.get("y")):
            continue
        at = {"Circuito": circ, "Transformador": a.get("CODIGO_TRANSFORMADOR"), "Capacidad (kVA)": a.get("CAPACIDAD_NOMINAL_TRAFO"),
              "Tipo": a.get("TIPO_TRANSFORMADOR"), "Tensión MT (kV)": a.get("TENSION_NOMINAL_SUB"),
              "Tensión BT (kV)": a.get("TENSION_PUNTO_CONEXION"), "AGPE instalada (kW)": a.get("CAPACIDAD_NOMINAL_INSTAL") or None,
              "Energía entregada FV": a.get("CAP_ENE_ENT_SIS_FOT") or None, "Energía entregada no FV": a.get("CAP_ENE_ENT_SIS_NO_FOT") or None,
              "Potencia reservada (kW)": a.get("POTENCIA_RESERVADA") or None, "Estado": a.get("ESTADO_TRANSFORMADOR"),
              "Municipio": (a.get("MUNICIPIO") or "").title(), "Departamento": (a.get("DEPARTAMENTO") or "").title(), "Fuente": fuente}
        filas.append(_fila("CHEC_Transformadores", circ, at, "Point", [round(g["x"], 6), round(g["y"], 6)]))

    mj = cargar("chec_red_primaria.json")
    fuente = f"Visor CREG 174 de CHEC ({mj.get('fecha', '')})"
    por_circ = {}
    for e in mj["features"]:
        a, g = e["attributes"], e.get("geometry") or {}
        paths = [[[round(p[0], 6), round(p[1], 6)] for p in path] for path in g.get("paths") or []]
        paths = [p for p in paths if len(p) >= 2 and all(_ok(x, y) for x, y in p)]
        if not paths:
            continue
        circ = str(a.get("CIRCUITO") or "").strip() or "(sin circuito)"
        c = por_circ.setdefault(circ, dict(paths=[], largo=0.0, kv={}, mun={}, loc=set()))
        c["paths"] += paths
        c["largo"] += _f(a.get("LONGITUD")) or _f(a.get("Shape__Length")) or 0
        kv = a.get("TENSION")
        if kv not in (None, ""):
            c["kv"][kv] = c["kv"].get(kv, 0) + 1
        if a.get("MUNICIPIO"):
            m = str(a["MUNICIPIO"]).title()
            c["mun"][m] = c["mun"].get(m, 0) + 1
        if a.get("LOCALIZACION"):
            c["loc"].add(str(a["LOCALIZACION"]).strip().lower())
    for circ, c in por_circ.items():
        kv = max(c["kv"], key=c["kv"].get) if c["kv"] else None
        mun = c["mun"] or mun_circ.get(circ, {})
        at = {"Circuito": circ, "Operador": "CHEC", "Tensión (kV)": kv, "Tramos MT": len(c["paths"]),
              "Longitud MT": f"{c['largo'] / 1000:.1f} km" if c["largo"] else None,
              "Capacidad de transformadores (kVA)": round(cap_circ[circ]) if circ in cap_circ else None,
              "Municipios": ", ".join(sorted(mun, key=mun.get, reverse=True)[:5]) or None,
              "Zona": ", ".join(sorted(c["loc"])[:4]) or None, "Fuente": fuente}
        # la «red primaria» del visor incluye las líneas de 115 kV y más: van en su propia capa de alta tensión
        filas.append(_fila("CHEC_Lineas_AT" if (_f(kv) or 0) >= 57.5 else "CHEC_Circuitos_MT", circ, at, "LineString", c["paths"]))

    with open(os.path.join(DESTINO, "filas.json"), "w", encoding="utf-8") as f:
        json.dump(filas, f, ensure_ascii=False)
    n = {}
    for fl in filas:
        n[fl[1]] = n.get(fl[1], 0) + 1
    print(", ".join(f"{k}: {v}" for k, v in n.items()), "->", os.path.join(DESTINO, "filas.json"))


if __name__ == "__main__":
    main()
