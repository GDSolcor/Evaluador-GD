"""
Descarga cuidadosa de la red de ESSA desde su geoportal público CREG 174
(https://geoportal.api-essa-electrosoftware.xyz, servidor pg_tileserv de teselas vectoriales).

ESSA no publica las líneas de media tensión: publica transformadores de distribución con su
CIRCUITO, subestación y disponibilidad CREG 174 (capa autogenerador174_p1), subestaciones
(polígonos), apoyos, tramos BT y clientes. Aquí se bajan solo transformadores y subestaciones.

Para no provocar un bloqueo del portal:
  - una petición a la vez, con pausa mínima entre peticiones (--pausa, 1,2 s por defecto);
  - caché en disco: una tesela ya bajada no se vuelve a pedir (se puede interrumpir y reanudar);
  - ante 429/403/5xx o falta de respuesta, espera creciente (30 s, 60 s, 120 s) y se detiene
    tras 3 fallos seguidos;
  - si una tesela viene llena (posible recorte del servidor) se subdivide al zoom siguiente.

    python herramientas/descargar_red_essa.py            -> descarga (reanudable)
    python herramientas/descargar_red_essa.py --estimar  -> solo cuenta cuántas teselas pediría
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mvt  # noqa: E402

URL = "https://geoportal.api-essa-electrosoftware.xyz/tiles/public.{capa}/{z}/{x}/{y}.pbf"
CAPAS = ["subestaciones", "autogenerador174_p1"]
BOUNDS = (-74.50, 5.69, -72.36, 8.17)  # de /tiles/public.<capa>.json
ZOOM = 11
LLENA = 9000  # pg_tileserv corta por defecto en 10.000 elementos por tesela
BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "datos", "red_or", "essa")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) EvaluadorGD-Solcor/2.0 (consulta CREG 174, descarga lenta)"


class Cortes(Exception):
    pass


class Cliente:
    """Una sola conexión HTTPS persistente (keep-alive), como el navegador: el servidor ve una
    conexión abierta con peticiones espaciadas, no una ráfaga de conexiones nuevas."""

    def __init__(self, pausa):
        self.pausa, self.ultima, self.fallos, self.pedidas = pausa, 0.0, 0, 0
        self.conn = None

    def _get(self, url):
        import http.client
        from urllib.parse import urlparse
        u = urlparse(url)
        if self.conn is None:
            self.conn = http.client.HTTPSConnection(u.netloc, timeout=25)
        try:
            self.conn.request("GET", u.path, headers={"User-Agent": UA, "Accept-Encoding": "identity", "Connection": "keep-alive"})
            r = self.conn.getresponse()
            datos = r.read()
        except Exception:
            self.conn.close()
            self.conn = None
            raise
        if r.status >= 400:
            raise urllib.error.HTTPError(url, r.status, r.reason, r.headers, None)
        return datos, r.headers

    def bajar(self, url, destino):
        if os.path.isfile(destino):
            with open(destino, "rb") as f:
                return f.read()
        espera = 30
        while True:
            dt = time.time() - self.ultima
            if dt < self.pausa:
                time.sleep(self.pausa - dt)
            self.ultima = time.time()
            self.pedidas += 1
            try:
                datos, hdr = self._get(url)
                if hdr.get("Content-Encoding") == "gzip":
                    import gzip
                    datos = gzip.decompress(datos)
                self.fallos = 0
                os.makedirs(os.path.dirname(destino), exist_ok=True)
                with open(destino + ".tmp", "wb") as f:
                    f.write(datos)
                os.replace(destino + ".tmp", destino)
                return datos
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return b""
                motivo = f"HTTP {e.code}"
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                motivo = str(e)[:80]
            self.fallos += 1
            if self.fallos >= 3:
                raise Cortes(f"3 fallos seguidos ({motivo}); se detiene para no forzar el portal. Reintentar más tarde.")
            print(f"    aviso: {motivo}; esperando {espera} s", flush=True)
            time.sleep(espera)
            espera *= 2


def teselas(z):
    x0, y1 = mvt.tesela(BOUNDS[0], BOUNDS[1], z)
    x1, y0 = mvt.tesela(BOUNDS[2], BOUNDS[3], z)
    return [(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]


def descargar_capa(cli, capa, z=ZOOM):
    feats = {}
    pendientes = [(z, x, y) for x, y in teselas(z)]
    hechas = 0
    while pendientes:
        tz, tx, ty = pendientes.pop(0)
        datos = cli.bajar(URL.format(capa=capa, z=tz, x=tx, y=ty), os.path.join(BASE, "teselas", capa, str(tz), f"{tx}_{ty}.pbf"))
        hechas += 1
        if not datos:
            continue
        lista = next(iter(mvt.decodificar(datos, tz, tx, ty).values()), [])
        if len(lista) >= LLENA and tz < 16:
            pendientes += [(tz + 1, 2 * tx + i, 2 * ty + j) for i in (0, 1) for j in (0, 1)]
            continue
        for f in lista:
            clave = f["props"].get("ogc_fid", f["id"])
            if clave in feats and f["tipo"] == "Polygon":
                feats[clave]["coords"] += f["coords"]  # polígono partido entre teselas
            else:
                feats[clave] = f
        if hechas % 25 == 0:
            print(f"  {capa}: {hechas} teselas, {len(feats)} elementos, {cli.pedidas} peticiones al portal", flush=True)
    return list(feats.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pausa", type=float, default=1.2)
    ap.add_argument("--estimar", action="store_true")
    a = ap.parse_args()
    if a.estimar:
        n = len(teselas(ZOOM))
        print(f"{n} teselas por capa × {len(CAPAS)} capas = {n * len(CAPAS)} peticiones; ~{n * len(CAPAS) * a.pausa / 60:.0f} min")
        return
    cli = Cliente(a.pausa)
    os.makedirs(BASE, exist_ok=True)
    for capa in CAPAS:
        print(f"Capa {capa}…", flush=True)
        try:
            feats = descargar_capa(cli, capa)
        except Cortes as e:
            print("DETENIDO:", e)
            sys.exit(2)
        with open(os.path.join(BASE, f"{capa}.json"), "w", encoding="utf-8") as f:
            json.dump(dict(capa=capa, fuente=URL.split("/tiles")[0], fecha=time.strftime("%Y-%m-%d"), elementos=feats), f, ensure_ascii=False)
        print(f"  {capa}: {len(feats)} elementos guardados", flush=True)
    print(f"Listo. Peticiones al portal: {cli.pedidas}")


if __name__ == "__main__":
    main()
