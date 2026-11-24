"""
Descarga cuidadosa de la red de EDEQ (Quindío, Grupo EPM) desde su "Mapa disponibilidad" público
(https://aplicaciones.edeq.com.co/portal/mapadisponibilidad, enlazado desde edeq.com.co >
Autogeneradores > Consultar disponibilidad de la red).

El mapa carga de una vez los transformadores (solo código y coordenadas) y las subestaciones; el
circuito, la subestación, los kVA y la disponibilidad AGPE de cada transformador salen de una
consulta por transformador (lo mismo que hace el mapa al hacer clic en él). Por eso:
  - una consulta a la vez con pausa (--pausa, 2 s por defecto): ~8.700 consultas ≈ 5 h;
  - caché en disco por transformador (datos/red_or/edeq/detalle/): se puede interrumpir y reanudar;
  - ante 429/403/5xx o falta de respuesta, espera creciente y se detiene tras 3 fallos seguidos;
  - si el PC se queda sin internet (no resuelve nombres) espera a que vuelva, sin contarlo como fallo.

    python herramientas/descargar_red_edeq.py
"""
import argparse
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request

API = "https://aplicaciones.edeq.com.co/portal/mapadisponibilidad/back/api"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) EvaluadorGD-Solcor/2.0 (consulta disponibilidad AGPE, descarga lenta)"
BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "datos", "red_or", "edeq")


class Cortes(Exception):
    pass


class Cliente:
    def __init__(self, pausa):
        self.pausa, self.ultima, self.fallos, self.pedidas, self.sin_red = pausa, 0.0, 0, 0, 0

    def pedir(self, ruta, cuerpo=None):
        espera = 30
        while True:
            dt = time.time() - self.ultima
            if dt < self.pausa:
                time.sleep(self.pausa - dt)
            self.ultima = time.time()
            self.pedidas += 1
            try:
                req = urllib.request.Request(API + ruta, data=json.dumps(cuerpo).encode() if cuerpo is not None else None,
                                             headers={"User-Agent": UA, "Content-Type": "application/json",
                                                      "Referer": "https://aplicaciones.edeq.com.co/portal/mapadisponibilidad/"})
                with urllib.request.urlopen(req, timeout=60) as r:
                    d = json.load(r)
                if self.sin_red:
                    print("    conexión recuperada", flush=True)
                self.fallos = self.sin_red = 0
                return d
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return None
                motivo = f"HTTP {e.code}"
            except urllib.error.URLError as e:
                if isinstance(e.reason, socket.gaierror):
                    # sin internet en este PC (no resuelve nombres): no es presión sobre el portal;
                    # se espera a que vuelva la conexión (hasta 2 h) sin contar el fallo
                    self.sin_red += 1
                    if self.sin_red > 120:
                        raise Cortes("sin conexión a internet durante 2 horas")
                    if self.sin_red == 1:
                        print("    sin conexión a internet; esperando a que vuelva…", flush=True)
                    time.sleep(60)
                    continue
                motivo = str(e)[:80]
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, ValueError) as e:
                motivo = str(e)[:80]
            self.fallos += 1
            if self.fallos >= 3:
                raise Cortes(f"3 fallos seguidos ({motivo}); se detiene para no forzar el portal. Reintentar más tarde.")
            print(f"    aviso: {motivo}; esperando {espera} s", flush=True)
            time.sleep(espera)
            espera *= 2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pausa", type=float, default=2.0)
    ap.add_argument("--limite", type=int, default=0, help="solo las primeras N consultas de detalle (prueba)")
    a = ap.parse_args()
    cli = Cliente(a.pausa)
    os.makedirs(os.path.join(BASE, "detalle"), exist_ok=True)
    listas = {}
    for n in ("transformers", "substations"):
        r = os.path.join(BASE, f"{n}.json")
        if not os.path.isfile(r):
            d = cli.pedir(f"/initial/{n}")
            json.dump(d, open(r, "w", encoding="utf-8"), ensure_ascii=False)
        listas[n] = json.load(open(r, encoding="utf-8"))["userData"]
    trafos = listas["transformers"]
    print(f"{len(trafos)} transformadores, {len(listas['substations'])} subestaciones", flush=True)
    hechas, saltados, seguidos = 0, [], 0
    try:
        for i, t in enumerate(trafos):
            cod = str(t.get("TRANSFORMADOR") or "").strip()
            if not cod:
                continue
            ruta = os.path.join(BASE, "detalle", "".join(c for c in cod if c.isalnum() or c in "-_") + ".json")
            if os.path.isfile(ruta):
                continue
            if a.limite and hechas >= a.limite:
                break
            try:
                d = cli.pedir("/initial/data_transformer", {"transformador": cod})
            except Cortes:
                # el portal falla con algunos transformadores puntuales (HTTP 500 / corte SSL) y responde
                # bien con los demás: se salta ese y se sigue; si fallan 5 distintos seguidos, se detiene
                saltados.append(cod)
                seguidos += 1
                cli.fallos = 0
                print(f"    saltado {cod} (el portal no lo entrega)", flush=True)
                if seguidos >= 5:
                    raise Cortes("5 transformadores seguidos sin respuesta: el portal parece caído")
                continue
            seguidos = 0
            with open(ruta + ".tmp", "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False)
            os.replace(ruta + ".tmp", ruta)
            hechas += 1
            if hechas % 100 == 0:
                print(f"  {i + 1}/{len(trafos)} transformadores ({cli.pedidas} consultas)", flush=True)
    except Cortes as e:
        print("DETENIDO:", e)
        sys.exit(2)
    if saltados:
        print(f"Saltados ({len(saltados)}): {', '.join(saltados[:30])}{' …' if len(saltados) > 30 else ''}")
    print(f"Listo. Consultas: {cli.pedidas}")


if __name__ == "__main__":
    main()
