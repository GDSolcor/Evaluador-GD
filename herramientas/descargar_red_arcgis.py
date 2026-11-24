"""
Descarga cuidadosa de la red de un operador (OR) publicada como servicio ArcGIS REST público
(FeatureServer / MapServer con consulta abierta). Hoy: CENS (Grupo EPM, Norte de Santander,
sur del Cesar y sur de Bolívar), servicio "Disponibilidad_2026" que alimenta su visor
"Disponibilidad de la red Nivel 1" (www.cens.com.co > Autogeneradores).

Solo se bajan capas de red: tramos de media tensión, transformadores con disponibilidad AGPE y
subestaciones. NO se bajan las capas de clientes/medidores (traen nombres y direcciones).

Para no provocar un bloqueo:
  - una petición a la vez, pausa mínima entre peticiones (--pausa, 1,5 s por defecto);
  - paginación por OBJECTID (páginas del tamaño máximo del servicio) con caché en disco:
    se puede interrumpir y reanudar sin volver a pedir lo ya bajado;
  - ante 429/403/5xx o falta de respuesta, espera creciente y se detiene tras 3 fallos seguidos.

    python herramientas/descargar_red_arcgis.py cens
    python herramientas/descargar_red_arcgis.py epm --estimar
"""
import argparse
import http.client
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) EvaluadorGD-Solcor/2.0 (consulta disponibilidad AGPE, descarga lenta)"

FUENTES = {
    "cens": dict(
        nombre="CENS (Grupo EPM)",
        visor="https://www.cens.com.co/Portals/_default/Skins/cens/html/Disponibilidad_de_la_red_Nivel1.html",
        url="https://services8.arcgis.com/imvHVlQ4h5Mf8wtx/ArcGIS/rest/services/Disponibilidad_2026/FeatureServer",
        capas={
            "subestaciones": (2, "CODE,DESCRIPTIO,ADDRESS,USER_,MAIN_KV,MW_PEAK,MVA_INSTAL"),
            "transformadores": (0, "OBJECTID,CODIGO_TRANSFORMADOR,CIRCUITO_ALIMENTADOR,SUBESTACION_CIRCUITO,"
                                   "CAPACIDAD_INSTALADA_TRAFO,TENSION_PT,FASES_DEL_TRANSFORMADOR,TENSION_NOMINAL_SUB,"
                                   "CAPACIDAD_SUBESTACION,CAP_DISPON_AGPE_KW,CAP_DISPON_AGPE_PORC,CAP_INSTALADA_AGPE,"
                                   "DISPONIBILIDAD_POTENCIA,DISPONIBILIDAD_ENERGIA,LIM_POTENCIA_INYEC_KW,"
                                   "LIM_ENERGIA_CON_TFV_KW,ESTADO_CIRCUITO,ESTADO_TRANSFORMADOR,MUN_TRANSFOR,"
                                   "LATITUD_SUB,LONGITUD_SUB"),
            "tramos_mt": (6, "OBJECTID,CODE,FPARENT,KVNOM,LENGTH,PHASES,CONDUCTOR,POBLACION"),
        },
    ),
    # Geoportal público del Grupo EPM (experience.arcgis.com "Visor Redes Energía", enlazado desde
    # epm.com.co > Autogeneradores > Modelo gráfico). Solo trae EPM (Antioquia).
    "epm": dict(
        nombre="EPM (Geoportal Grupo EPM)",
        visor="https://experience.arcgis.com/experience/742f0b4891f346b68e5512f807b80387",
        url="https://services1.arcgis.com/lHSB5M3vxFT82S7P/ArcGIS/rest/services/P_Redes_Energia_GE3/FeatureServer",
        capas={
            "subestaciones": (14, "CODIGO,NOMBRE_SUB,CAN_TRANSFORMADORES,MERCADO,CLASIFICACION_MERCADO"),
            "lineas_transmision": (17, "SUBESTACION_CONN,CIRCUITO_CONN,TENSION_CONN,LONGITUD_CONN,CODIGO_LINEA", "ESTADO='OPERACION'"),
            "transformadores": (4, "CODIGO,NRO_TRANSFORMADOR,CIRCUITO_CONN,SUBESTACION_CONN,CAPACIDAD_NOMINAL,TENSION_CONN,"
                                   "NUMERO_FASES,MUNICIPIO,CLASIFICACION_MERCADO,PORCENTAJE_CARGA", "ESTADO='OPERACION'"),
            "tramos_mt": (13, "CODIGO,CIRCUITO_CONN,SUBESTACION_CONN,TENSION_CONN,LONGITUD_CONN,NUMERO_FASES,"
                             "CLASIFICACION_MERCADO,MUNICIPIO", "ESTADO='OPERACION'"),
        },
    ),
}


class Cortes(Exception):
    pass


class Cliente:
    def __init__(self, pausa):
        self.pausa, self.ultima, self.fallos, self.pedidas = pausa, 0.0, 0, 0

    def json(self, url, params, destino=None):
        if destino and os.path.isfile(destino):
            with open(destino, encoding="utf-8") as f:
                return json.load(f)
        espera = 30
        while True:
            dt = time.time() - self.ultima
            if dt < self.pausa:
                time.sleep(self.pausa - dt)
            self.ultima = time.time()
            self.pedidas += 1
            try:
                req = urllib.request.Request(url, data=urllib.parse.urlencode(params).encode(),
                                             headers={"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded"})
                with urllib.request.urlopen(req, timeout=90) as r:
                    d = json.load(r)
                if "error" in d:
                    raise urllib.error.HTTPError(url, int(d["error"].get("code") or 500), str(d["error"].get("message")), None, None)
                self.fallos = 0
                if destino:
                    os.makedirs(os.path.dirname(destino), exist_ok=True)
                    with open(destino + ".tmp", "w", encoding="utf-8") as f:
                        json.dump(d, f)
                    os.replace(destino + ".tmp", destino)
                return d
            except urllib.error.HTTPError as e:
                motivo = f"HTTP {e.code}"
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, ValueError, http.client.HTTPException) as e:
                # HTTPException: p. ej. IncompleteRead cuando el servidor corta una respuesta grande (#47)
                motivo = str(e)[:80]
            self.fallos += 1
            if self.fallos >= 3:
                raise Cortes(f"3 fallos seguidos ({motivo}); se detiene para no forzar el servicio. Reintentar más tarde.")
            print(f"    aviso: {motivo}; esperando {espera} s", flush=True)
            time.sleep(espera)
            espera *= 2


def descargar_capa(cli, base, capa, idc, campos, carpeta, filtro=None, tam_max=2000, simplificar=None):
    """simplificar: tolerancia en grados (maxAllowableOffset) para que el servidor entregue los polígonos
    muy grandes ya generalizados (0,0002° ≈ 22 m)."""
    meta = cli.json(f"{base}/{idc}", {"f": "json"}, os.path.join(carpeta, "cache", f"{capa}_meta.json"))
    oid = meta.get("objectIdField") or next((f["name"] for f in meta.get("fields", []) if f.get("type") == "esriFieldTypeOID"), "OBJECTID")
    tam = min(int(meta.get("maxRecordCount") or 1000), tam_max)
    existentes = {f["name"] for f in meta.get("fields", [])}
    campos = ",".join([oid] + [c for c in campos.split(",") if c in existentes and c != oid])
    feats, ultimo, pagina = [], -1, 0
    while True:
        d = cli.json(f"{base}/{idc}/query",
                     {"where": f"{oid}>{ultimo}" + (f" AND {filtro}" if filtro else ""), "outFields": campos, "orderByFields": oid, "outSR": 4326,
                      "geometryPrecision": 6, "resultRecordCount": tam, "returnGeometry": "true", "f": "json",
                      **({"maxAllowableOffset": simplificar} if simplificar else {})},
                     os.path.join(carpeta, "cache", capa, f"{pagina:05d}_{ultimo}.json"))
        lote = d.get("features", [])
        if not lote:
            break
        feats += lote
        ultimo = max(f["attributes"][oid] for f in lote)
        pagina += 1
        if pagina % 10 == 0:
            print(f"  {capa}: {len(feats)} elementos, {cli.pedidas} peticiones", flush=True)
        if len(lote) < tam and not d.get("exceededTransferLimit"):
            break
    return feats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("fuente", choices=sorted(FUENTES))
    ap.add_argument("--pausa", type=float, default=1.5)
    ap.add_argument("--estimar", action="store_true")
    a = ap.parse_args()
    F = FUENTES[a.fuente]
    carpeta = os.path.join(RAIZ, "datos", "red_or", a.fuente)
    cli = Cliente(a.pausa)
    if a.estimar:
        total = 0
        for capa, (idc, _, *filtro) in F["capas"].items():
            n = cli.json(f"{F['url']}/{idc}/query", {"where": filtro[0] if filtro else "1=1", "returnCountOnly": "true", "f": "json"})["count"]
            total += n // 1000 + 1
            print(f"{capa}: {n} elementos")
        print(f"~{total} peticiones, ~{total * a.pausa / 60:.0f} min de pausas")
        return
    for capa, (idc, campos, *filtro) in F["capas"].items():
        print(f"Capa {capa}…", flush=True)
        try:
            feats = descargar_capa(cli, F["url"], capa, idc, campos, carpeta, filtro[0] if filtro else None)
        except Cortes as e:
            print("DETENIDO:", e)
            sys.exit(2)
        with open(os.path.join(carpeta, f"{capa}.json"), "w", encoding="utf-8") as f:
            json.dump(dict(capa=capa, fuente=F["nombre"], servicio=F["url"], visor=F["visor"],
                           fecha=time.strftime("%Y-%m-%d"), elementos=feats), f, ensure_ascii=False)
        print(f"  {capa}: {len(feats)} elementos guardados", flush=True)
    print(f"Listo. Peticiones al servicio: {cli.pedidas}")


if __name__ == "__main__":
    main()
