"""Construye (una sola vez) los indices espaciales de las capas catastrales del IGAC.

    python herramientas/indexar_catastro.py
"""
import os
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "motor"))
import catastro  # noqa: E402

for nombre, capa in catastro.capas().items():
    t = time.time()
    if capa.progreso >= 1.0:
        print(f"{nombre}: indice ya existe", flush=True)
        continue
    print(f"{nombre}: indexando {capa.n:,} registros…", flush=True)
    capa.construir_indice()
    print(f"{nombre}: listo en {time.time() - t:,.0f} s", flush=True)
