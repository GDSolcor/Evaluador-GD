"""
Capas de red que repiten otra más completa o más reciente (se ocultan en el mapa, en "red cercana"
y en el análisis del predio, sin borrar nada de las bases).

  - EBSA: un circuito antiguo del GIS Solcor (capa CTO_xxxxx) cuyo trazado corre en su mayoría (>= 50 %)
    a menos de 3 m de un circuito descargado del visor GD-Connect -> se usa la versión del geoportal.
  - ENEL: los tramos de "Redes_ENEL_MT" (sin nombre de circuito) que ya están en una capa con nombre
    de circuito (Redes_<zona>) -> se ocultan esos tramos concretos (elemento por elemento).

El resultado se guarda en la tabla `duplicados` de datos/red_or/red_complementaria.db y red.py lo lee.

    python motor/red_duplicados.py
"""
import collections
import json
import math
import os
import sqlite3

import gis

UMBRAL_M = 3.0
CEL = 20.0


def _segmentos(filas):
    """filas: (clave, geom_json) -> lista (clave, x1, y1, x2, y2) en metros (proyección local por latitud)."""
    out = []
    for k, gj in filas:
        for path in json.loads(gj):
            for a, b in zip(path, path[1:]):
                kx = 111320 * math.cos(math.radians(a[1]))
                x1, y1, x2, y2 = a[0] * kx, a[1] * 111320, b[0] * kx, b[1] * 111320
                if math.hypot(x2 - x1, y2 - y1) > 0.5:
                    out.append((k, x1, y1, x2, y2))
    return out


def _indice(segs):
    idx = collections.defaultdict(list)
    for i, s in enumerate(segs):
        for t in (0.0, 0.5, 1.0):
            idx[(int((s[1] + t * (s[3] - s[1])) // CEL), int((s[2] + t * (s[4] - s[2])) // CEL))].append(i)
    return idx


def _cerca(px, py, segs, idx):
    for a in (-1, 0, 1):
        for b in (-1, 0, 1):
            for j in idx.get((int(px // CEL) + a, int(py // CEL) + b), ()):
                _, x1, y1, x2, y2 = segs[j]
                dx, dy = x2 - x1, y2 - y1
                L = dx * dx + dy * dy
                t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / L)) if L else 0.0
                if math.hypot(px - x1 - t * dx, py - y1 - t * dy) < UMBRAL_M:
                    return True
    return False


def calcular():
    db = gis._db()
    dup = []  # (tipo, layer, id_elemento_o_null, motivo)
    # --- EBSA: circuitos antiguos reemplazados por los del visor GD-Connect
    nuevos = _segmentos(db.execute("SELECT name, geom_json FROM rc.features WHERE layer='EBSA_Circuitos_GDC'"))
    if nuevos:
        idx = _indice(nuevos)
        largo, cubierto, por = collections.Counter(), collections.Counter(), collections.defaultdict(collections.Counter)
        for s in _segmentos(db.execute("SELECT layer, geom_json FROM main.features WHERE dataset='RED_ELECTRICA' AND layer LIKE 'CTO_1%'")):
            L = math.hypot(s[3] - s[1], s[4] - s[2])
            largo[s[0]] += L
            if _cerca((s[1] + s[3]) / 2, (s[2] + s[4]) / 2, nuevos, idx):
                cubierto[s[0]] += L
        reemplazadas = set()
        for layer, L in largo.items():
            if L and cubierto[layer] / L >= 0.5:
                reemplazadas.add(layer)
                dup.append(("capa", layer, None, f"reemplazada por el visor EBSA GD-Connect ({100 * cubierto[layer] / L:.0f} % del trazado)"))
        # tramos sueltos de circuitos antiguos que ya están en la versión nueva (reconfiguraciones parciales)
        for fid, layer, gj in db.execute("SELECT id, layer, geom_json FROM main.features WHERE dataset='RED_ELECTRICA' AND layer LIKE 'CTO_1%'"):
            if layer in reemplazadas:
                continue
            segs = _segmentos([(fid, gj)])
            L = sum(math.hypot(s[3] - s[1], s[4] - s[2]) for s in segs)
            c = sum(math.hypot(s[3] - s[1], s[4] - s[2]) for s in segs if _cerca((s[1] + s[3]) / 2, (s[2] + s[4]) / 2, nuevos, idx))
            if L and c / L >= 0.8:
                dup.append(("elemento", layer, fid, "tramo ya incluido en un circuito del visor EBSA GD-Connect"))
    # --- ENEL: tramos sin nombre repetidos en una capa con nombre de circuito
    con_nombre = _segmentos(db.execute("SELECT layer, geom_json FROM main.features WHERE dataset='RED_ELECTRICA' "
                                       "AND layer LIKE 'Redes_%' AND layer NOT IN ('Redes_ENEL_MT', 'Redes_34,5')"))
    idx = _indice(con_nombre)
    for fid, gj in db.execute("SELECT id, geom_json FROM main.features WHERE layer='Redes_ENEL_MT'"):
        segs = _segmentos([(fid, gj)])
        L = sum(math.hypot(s[3] - s[1], s[4] - s[2]) for s in segs)
        c = sum(math.hypot(s[3] - s[1], s[4] - s[2]) for s in segs if _cerca((s[1] + s[3]) / 2, (s[2] + s[4]) / 2, con_nombre, idx))
        if L and c / L >= 0.8:
            dup.append(("elemento", "Redes_ENEL_MT", fid, "repetido en un circuito ENEL con nombre"))
    return dup


def guardar(dup, ruta=None):
    ruta = ruta or gis.COMPLEMENTO
    c = sqlite3.connect(ruta, timeout=30)
    with c:
        c.execute("CREATE TABLE IF NOT EXISTS duplicados(tipo TEXT, layer TEXT, fid INTEGER, motivo TEXT)")
        c.execute("DELETE FROM duplicados")
        c.executemany("INSERT INTO duplicados VALUES (?,?,?,?)", dup)
    c.close()


if __name__ == "__main__":
    d = calcular()
    guardar(d)
    capas = [x for x in d if x[0] == "capa"]
    print(f"{len(capas)} capas antiguas EBSA reemplazadas:")
    for x in capas:
        print("  ", x[1], "-", x[3])
    print(f"{sum(1 for x in d if x[0] == 'elemento' and x[1] == 'Redes_ENEL_MT')} tramos ENEL 'Red MT sin nombre' repetidos en circuitos con nombre")
    print(f"{sum(1 for x in d if x[0] == 'elemento' and x[1] != 'Redes_ENEL_MT')} tramos EBSA antiguos ya incluidos en circuitos del visor")
