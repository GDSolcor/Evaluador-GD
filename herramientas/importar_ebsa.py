"""
Importa los circuitos MT de EBSA recolectados en el visor GD-Connect (gdc.ebsa.com.co) a la red
complementaria del evaluador.

El archivo ebsa_circuitos.json lo genera el recolector que corre dentro del visor (la sesión exige
aceptar la política de datos y un reCAPTCHA, por eso no se descarga desde Python). Estructura:
  {fuente, fecha, zonas, circuitos: {codigo: {mun, semilla, tramos: [[lon1, lat1, lon2, lat2, codigo_tramo], ...]}}}

    python herramientas/importar_ebsa.py "%USERPROFILE%\\Downloads\\ebsa_circuitos.json"
    python motor/red_complementaria.py
"""
import json
import math
import os
import sqlite3
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR = os.path.join(RAIZ, "datos", "red_or", "ebsa")
MUN = {1: "Tunja", 204: "Cómbita", 224: "Cucaita", 238: "Duitama", 407: "Villa de Leyva", 516: "Paipa", 837: "Tuta"}


# otros OR con el mismo visor SPARD GD-Connect: carpeta, capa, prefijo DANE del departamento, nombre del visor
OPERADORES = {"ebsa": ("EBSA_Circuitos_GDC", "15", "Visor EBSA GD-Connect"),
              "enerca": ("ENERCA_Circuitos_GDC", "85", "Visor ENERCA GD-Connect"),
              "emsa": ("EMSA_Circuitos_GDC", "50", "Visor EMSA GD-Connect")}


def nombre_mun(mun, depto="15"):
    """mun: código DANE sin el prefijo del departamento (int) o completo (str)."""
    if depto == "15" and mun in MUN:
        return MUN[mun]
    try:
        sys.path.insert(0, os.path.join(RAIZ, "motor"))
        import catastro
        cod = f"{depto}{int(mun):03d}" if int(mun) < 1000 else str(mun)
        return (catastro._nombre_municipio(cod) or cod).split(",")[0]
    except Exception:
        return str(mun)


def main(ruta, op="ebsa"):
    capa, depto, visor = OPERADORES[op]
    DIR = os.path.join(RAIZ, "datos", "red_or", op)
    os.makedirs(DIR, exist_ok=True)
    destino = os.path.join(DIR, f"{op}_circuitos.json")
    if os.path.abspath(ruta) != os.path.abspath(destino):
        # se acumula: circuitos de descargas anteriores (otras zonas) se conservan
        previo = json.load(open(destino, encoding="utf-8")) if os.path.isfile(destino) else None
        nuevo = json.load(open(ruta, encoding="utf-8"))
        if previo:
            previo["circuitos"].update(nuevo["circuitos"])
            previo["zonas"] = ", ".join(sorted(set((previo.get("zonas", "") + ", " + nuevo.get("zonas", "")).split(", ")) - {""}))
            previo["fecha"] = nuevo.get("fecha")
            nuevo = previo
        json.dump(nuevo, open(destino, "w", encoding="utf-8"), ensure_ascii=False)
    d = json.load(open(destino, encoding="utf-8"))
    ya = set()
    try:
        db = sqlite3.connect(f"file:{os.path.join(RAIZ, 'datos', 'gis', 'GIS_SOLCOR', 'data', 'coexistencia.db')}?mode=ro", uri=True)
        for (layer,) in db.execute("select distinct layer from features where dataset='RED_ELECTRICA' and layer like 'CTO_%'"):
            if op == "ebsa":  # las capas CTO_ del GIS Solcor son circuitos de EBSA
                ya.add(layer.split(".")[0].replace("CTO_", "").split("_")[0])
    except sqlite3.Error:
        pass
    filas = []
    for code, c in sorted(d["circuitos"].items()):
        paths, largo = [], 0.0
        for lon1, lat1, lon2, lat2 in (s[:4] for s in c["tramos"]):
            if (lon1, lat1) == (lon2, lat2) or not all(-80 < v < -66 for v in (lon1, lon2)) or not all(-5 < v < 14 for v in (lat1, lat2)):
                continue
            paths.append([[lon1, lat1], [lon2, lat2]])
            largo += math.hypot((lon2 - lon1) * 111320 * math.cos(math.radians(lat1)), (lat2 - lat1) * 111320)
        if not paths:
            continue
        xs = [p[0] for path in paths for p in path]
        ys = [p[1] for path in paths for p in path]
        attrs = {"Circuito": code, "Tramos MT": len(paths), "Longitud MT": f"{largo / 1000:.1f} km",
                 "Zona de consulta": nombre_mun(c.get("mun"), depto) if c.get("mun") else "barrido por código de circuito",
                 "También en GIS Solcor": "sí (versión anterior, capa CTO_" + code + ")" if code in ya else "no",
                 "Fuente": f"{visor} ({d.get('fecha', '')})"}
        filas.append(["RED_ELECTRICA", capa, code, json.dumps(attrs, ensure_ascii=False), "LineString",
                      json.dumps(paths), min(xs), min(ys), max(xs), max(ys)])
    json.dump(filas, open(os.path.join(DIR, "filas.json"), "w", encoding="utf-8"), ensure_ascii=False)
    nuevos = sum(1 for f in filas if f[2] not in ya)
    print(f"{len(filas)} circuitos {op.upper()} ({nuevos} nuevos, {len(filas) - nuevos} ya estaban en el GIS Solcor con versión anterior)")


if __name__ == "__main__":
    # python herramientas/importar_ebsa.py <archivo> [ebsa|enerca|emsa]
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.expanduser("~"), "Downloads", "ebsa_circuitos.json"),
         sys.argv[2] if len(sys.argv) > 2 else "ebsa")
