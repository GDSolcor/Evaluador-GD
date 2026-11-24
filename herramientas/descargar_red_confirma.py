"""
Descarga los tramos de red de los visores CREG 174 de Afinia que publica Confirma (sin ingreso ni captcha):
  - afinia : https://visor.confirma.com.co         (API api-visor.confirma.com.co)
  - atenea : https://visor-athenea.confirma.com.co (API api-visor-athenea.confirma.com.co; también red de Afinia, zona Cesar)

El API entrega hasta 5.000 tramos por recuadro (/api/red/?bounds=…&lite=true). Se recorre la región en celdas
y la celda que llega llena se parte en cuatro. Para no cargar el servicio: una petición a la vez, pausa entre
peticiones (--pausa), caché en disco por celda (se puede interrumpir y reanudar) y se detiene tras 3 fallos seguidos.
Al final arma datos/red_or/afinia/filas.json (un elemento por circuito) para motor/red_complementaria.py.

    python herramientas/descargar_red_confirma.py afinia atenea
    python motor/red_complementaria.py
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) EvaluadorGD-Solcor/2.2 (consulta disponibilidad CREG 174, descarga lenta)"
FUENTES = {
    "afinia": dict(api="https://api-visor.confirma.com.co/api/red/", visor="https://visor.confirma.com.co"),
    "atenea": dict(api="https://api-visor-athenea.confirma.com.co/api/red/", visor="https://visor-athenea.confirma.com.co"),
}
REGION = (-76.6, 7.0, -72.6, 11.4)   # Caribe: Córdoba, Sucre, Bolívar, Cesar, Magdalena, La Guajira
CELDA = 0.25
TOPE = 5000
CARPETA = os.path.join(BASE, "datos", "red_or", "afinia")


class Detenido(Exception):
    pass


def pedir(url, pausa, estado):
    espera = 30
    while True:
        dt = time.time() - estado["ultima"]
        if dt < pausa:
            time.sleep(pausa - dt)
        estado["ultima"] = time.time()
        estado["pedidas"] += 1
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"}), timeout=90) as r:
                d = json.load(r)
            estado["fallos"] = 0
            return d
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, ValueError) as e:
            estado["fallos"] += 1
            if estado["fallos"] >= 3:
                raise Detenido(f"3 fallos seguidos ({str(e)[:80]}); se detiene para no forzar el servicio. Reintentar más tarde.")
            print(f"    aviso: {str(e)[:80]}; esperando {espera} s", flush=True)
            time.sleep(espera)
            espera *= 2


def recorrer(fuente, pausa):
    F = FUENTES[fuente]
    cache = os.path.join(CARPETA, "cache", fuente)
    os.makedirs(cache, exist_ok=True)
    estado = dict(ultima=0.0, pedidas=0, fallos=0)
    pend = []
    x = REGION[0]
    while x < REGION[2] - 1e-9:
        y = REGION[1]
        while y < REGION[3] - 1e-9:
            pend.append((round(x, 6), round(y, 6), round(min(x + CELDA, REGION[2]), 6), round(min(y + CELDA, REGION[3]), 6)))
            y += CELDA
        x += CELDA
    tramos, hechas = {}, 0
    while pend:
        b = pend.pop()
        archivo = os.path.join(cache, "_".join(f"{v:.6f}" for v in b) + ".json")
        if os.path.isfile(archivo):
            d = json.load(open(archivo, encoding="utf-8"))
        else:
            d = pedir(f"{F['api']}?bounds={b[0]},{b[1]},{b[2]},{b[3]}&lite=true", pausa, estado)
            with open(archivo + ".tmp", "w", encoding="utf-8") as f:
                json.dump(d, f)
            os.replace(archivo + ".tmp", archivo)
        hechas += 1
        if len(d) >= TOPE and (b[2] - b[0]) > 0.002:
            mx, my = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2  # celda llena: se parte en cuatro
            pend += [(b[0], b[1], mx, my), (mx, b[1], b[2], my), (b[0], my, mx, b[3]), (mx, my, b[2], b[3])]
            pend[-4:] = [tuple(round(v, 6) for v in c) for c in pend[-4:]]
        else:
            for t in d:
                tramos[t.get("codigo_tramo") or json.dumps(t.get("geometry"))] = t
        if hechas % 25 == 0:
            print(f"  {fuente}: {hechas} celdas, {len(pend)} por revisar, {len(tramos)} tramos, {estado['pedidas']} peticiones", flush=True)
    print(f"{fuente}: {len(tramos)} tramos en {hechas} celdas ({estado['pedidas']} peticiones nuevas)", flush=True)
    return tramos


def armar_filas(por_fuente):
    circ = {}
    for fuente, tramos in por_fuente.items():
        for t in tramos.values():
            g = t.get("geometry") or {}
            cs = g.get("coordinates") or []
            paths = [cs] if g.get("type") == "LineString" else cs if g.get("type") == "MultiLineString" else []
            paths = [[[round(p[0], 5), round(p[1], 5)] for p in path] for path in paths if len(path) >= 2]
            if not paths:
                continue
            nombre = t.get("circuito") or "(sin circuito)"
            c = circ.setdefault(nombre, dict(paths=[], sub=t.get("subestacion"), prop={}, fuente=fuente))
            c["paths"] += paths
            pr = t.get("propiedad_tramo") or ""
            c["prop"][pr] = c["prop"].get(pr, 0) + 1
    hoy = time.strftime("%Y-%m-%d")
    filas = []
    for nombre, c in circ.items():
        cod, _, nom = nombre.partition(" - ")
        tot = sum(c["prop"].values())
        xs = [p[0] for path in c["paths"] for p in path]
        ys = [p[1] for path in c["paths"] for p in path]
        at = {"Circuito": nom or nombre, "Código del circuito": cod if nom else None, "Subestación": c["sub"], "Tramos": len(c["paths"]),
              "Tramos de particulares": f"{100 * sum(v for k, v in c['prop'].items() if k and k.upper() != 'AFINIA') / tot:.0f} %" if tot else None,
              "Fuente": f"Visor CREG 174 de Afinia ({FUENTES[c['fuente']]['visor']}, {hoy})"}
        filas.append(["RED_ELECTRICA", "AFINIA_Circuitos_MT", nom or nombre, json.dumps({k: v for k, v in at.items() if v not in (None, "")}, ensure_ascii=False),
                      "LineString", json.dumps(c["paths"]), min(xs), min(ys), max(xs), max(ys)])
    with open(os.path.join(CARPETA, "filas.json"), "w", encoding="utf-8") as f:
        json.dump(filas, f, ensure_ascii=False)
    print(f"{len(filas)} circuitos -> {os.path.join(CARPETA, 'filas.json')}")


def main():
    fuentes = [a for a in sys.argv[1:] if a in FUENTES] or list(FUENTES)
    pausa = float(sys.argv[sys.argv.index("--pausa") + 1]) if "--pausa" in sys.argv else 1.5
    por = {}
    try:
        for f in fuentes:
            por[f] = recorrer(f, pausa)
    except Detenido as e:
        print("DETENIDO:", e)
        sys.exit(2)
    armar_filas(por)


if __name__ == "__main__":
    main()
