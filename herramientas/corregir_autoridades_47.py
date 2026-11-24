"""
Correcciones de la base de autoridades ambientales ya descargada (#47), para no tener que volver a bajarla:
  - capas RICV de la CAR (riesgo por incendios de cobertura vegetal 2014) que quedaron en CAR_Riesgo -> CAR_Riesgo_Incendios
  - nombres «<Null>» -> el nombre de la capa
  - atributo «Capa» repetido (el nombre de la capa ya va en la columna layer)
Se puede correr aunque otra descarga esté escribiendo en la base (espera el bloqueo).

    python herramientas/corregir_autoridades_47.py
"""
import os
import sqlite3
import time

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(RAIZ, "datos", "autoridades_ambientales.db")

c = sqlite3.connect(DB, timeout=600)
t = time.time()
with c:
    n1 = c.execute("UPDATE features SET dataset='CAR_Riesgo_Incendios' WHERE dataset='CAR_Riesgo' AND layer LIKE 'RICV%'").rowcount
    c.execute("UPDATE capas SET dataset='CAR_Riesgo_Incendios' WHERE dataset='CAR_Riesgo' AND layer LIKE 'RICV%'")
print("RICV movidas:", n1, f"({time.time() - t:.0f} s)", flush=True)
with c:
    n2 = c.execute("UPDATE features SET name=layer WHERE name IN ('<Null>', '<null>', 'Null', '')").rowcount
print("nombres <Null>:", n2, f"({time.time() - t:.0f} s)", flush=True)
with c:
    n3 = c.execute("UPDATE features SET attrs_json=json_remove(attrs_json, '$.Capa') "
                   "WHERE dataset LIKE 'CORPOBOYACA_%' AND json_extract(attrs_json, '$.Capa') IS NOT NULL").rowcount
print("atributo Capa quitado:", n3, f"({time.time() - t:.0f} s)", flush=True)
c.close()
conteos = os.path.join(RAIZ, "datos", "gis", "_conteos.json")
if os.path.isfile(conteos):
    os.remove(conteos)
