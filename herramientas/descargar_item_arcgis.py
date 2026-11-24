"""Descarga el archivo de un item publico de ArcGIS Online, con reanudacion.

    python descargar_item_arcgis.py <item_id> <carpeta_destino>
"""
import json
import os
import sys
import time
import urllib.request

UA = {"User-Agent": "EvaluadorGD/1.0"}


def descargar(item_id, destino):
    meta = json.load(urllib.request.urlopen(urllib.request.Request(
        f"https://www.arcgis.com/sharing/rest/content/items/{item_id}?f=json", headers=UA), timeout=60))
    nombre, total = meta["name"], meta.get("size", -1)
    os.makedirs(destino, exist_ok=True)
    ruta = os.path.join(destino, nombre)
    url = f"https://www.arcgis.com/sharing/rest/content/items/{item_id}/data"
    for intento in range(1, 21):
        hecho = os.path.getsize(ruta) if os.path.exists(ruta) else 0
        if total > 0 and hecho >= total:
            break
        req = urllib.request.Request(url, headers=dict(UA, Range=f"bytes={hecho}-") if hecho else UA)
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                modo = "ab" if hecho and r.status == 206 else "wb"
                with open(ruta, modo) as f:
                    t0, ult = time.time(), 0
                    while True:
                        bloque = r.read(1 << 20)
                        if not bloque:
                            break
                        f.write(bloque)
                        if time.time() - ult > 30:
                            ult = time.time()
                            print(f"{nombre}: {f.tell() / 1e6:,.0f} / {total / 1e6:,.0f} MB", flush=True)
        except Exception as e:  # corte de red: se reanuda desde lo descargado
            print(f"intento {intento}: {type(e).__name__}: {e}; reintentando…", flush=True)
            time.sleep(min(60, 5 * intento))
    final = os.path.getsize(ruta)
    estado = "COMPLETO" if total <= 0 or final == total else "INCOMPLETO"
    print(f"{estado}: {ruta} ({final:,} de {total:,} bytes)", flush=True)


if __name__ == "__main__":
    descargar(sys.argv[1], sys.argv[2])
