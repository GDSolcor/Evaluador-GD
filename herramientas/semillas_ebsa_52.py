"""
Ticket #52: puntos y códigos para recorrer el visor de EBSA con herramientas/recolector_gdc.js.

  1) Duitama (GD San Antonio, 5.84781, -73.01917): puntos cada 300 m en 2,5 km a la redonda, del centro hacia afuera.
     Se consultan todos (zona densa: varios circuitos corren juntos).
  2) Barrido por código: los códigos de circuito de EBSA van de ~13500 a ~15400; se piden directamente con get_line
     todos los de 13000 a 15999 que no estén ya cargados (en GD-Connect o en el GIS Solcor). Así se encuentran los
     circuitos que el recorrido por puntos no alcanzó, en todo Boyacá.

    python herramientas/semillas_ebsa_52.py   -> datos/red_or/ebsa/semillas_duitama.json y codigos_barrido.json
"""
import json
import math
import os
import re
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "motor"))
os.chdir(RAIZ)
import gis  # noqa: E402

LON, LAT, RADIO_M, PASO_M = -73.01917, 5.84781, 2500, 300
db = gis._db()
gdc = {int(n) for (n,) in db.execute("select distinct name from features where layer='EBSA_Circuitos_GDC'") if str(n).isdigit()}
cto = {int(m.group(1)) for (l,) in db.execute("select distinct layer from features where dataset='RED_ELECTRICA' and layer like 'CTO_%'")
       for m in [re.match(r"CTO_(\d+)", l)] if m}
previos = sorted(gdc | cto)

dlat = PASO_M / 110540
dlon = PASO_M / (111320 * math.cos(math.radians(LAT)))
n = int(RADIO_M / PASO_M) + 1
pts = []
for i in range(-n, n + 1):
    for j in range(-n, n + 1):
        d = math.hypot(i, j) * PASO_M
        if d <= RADIO_M:
            pts.append((d, [round(LON + i * dlon, 6), round(LAT + j * dlat, 6), "Duitama"]))
pts = [p for _, p in sorted(pts, key=lambda t: t[0])]
codigos = [c for c in range(13000, 16000) if c not in gdc and c not in cto]
carpeta = os.path.join(RAIZ, "datos", "red_or", "ebsa")
os.makedirs(carpeta, exist_ok=True)
json.dump({"semillas": pts, "previos": previos}, open(os.path.join(carpeta, "semillas_duitama.json"), "w"))
json.dump(codigos, open(os.path.join(carpeta, "codigos_barrido.json"), "w"))
print(len(pts), "puntos ·", len(codigos), "códigos ·", len(previos), "previos")
