"""
Arma una copia limpia del Evaluador GD para publicarla en SharePoint (Herramientas/Evaluador GD).

Copia el código y SOLO los datos que la herramienta lee en ejecución; deja fuera los ZIP
originales, la GDB/GPKG incompletos, temporales, bases de usuarios/evaluaciones de este PC y las
capas del catastro que solo se usan para reconstruir índices (el índice ya va construido).

    python herramientas/preparar_paquete.py                      -> solo muestra qué copiaría y cuánto pesa
    python herramientas/preparar_paquete.py --destino "C:\\...\\Herramientas\\Evaluador GD" --copiar

La copia es incremental: si el archivo ya existe en el destino con el mismo tamaño y fecha, no se
vuelve a copiar (útil para actualizar solo el código).
"""
import argparse
import os
import shutil
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CODIGO = ["servidor.py", "Abrir Evaluador GD.bat", "LEEME.md", "ARQUITECTURA.md", "motor", "web", "herramientas", "ejemplos"]
CAT = "datos/igac/catastro/CatastroPubliconAgosto2026"
DATOS = [
    "datos/config_correo.ejemplo.json",
    "datos/gis/GIS_SOLCOR",                       # coexistencia.db + rásteres de pendiente y PVOUT
    "datos/igac/hidrografia_gdb/Hidrografia_completa.gpkg",
    "datos/igac/hidrografia_100k",
] + [f"{CAT}/{c}.{e}" for c in ("R_TERRENO", "U_TERRENO", "R_VEREDA", "U_MANZANA")
     for e in ("shp", "shx", "dbf", "cpg", "prj", "rtree.sqlite")] + [f"{CAT}/catastro_codigos.sqlite"]
EXCLUIR_EXT = (".tmp", ".pyc", ".zip", ".lock")
EXCLUIR_NOMBRES = {"__pycache__", "usuarios.db", "evaluaciones.db", "config_correo.json", "correo_secreto.bin", "correo_verificacion.json", ".claude"}


def archivos():
    for rel in CODIGO + DATOS:
        p = os.path.join(RAIZ, rel)
        if os.path.isfile(p):
            yield rel.replace("\\", "/"), p
        elif os.path.isdir(p):
            for d, subdirs, fs in os.walk(p):
                subdirs[:] = [s for s in subdirs if s not in EXCLUIR_NOMBRES]
                for f in fs:
                    if f in EXCLUIR_NOMBRES or f.lower().endswith(EXCLUIR_EXT) or f.startswith("_prueba"):
                        continue
                    full = os.path.join(d, f)
                    yield os.path.relpath(full, RAIZ).replace("\\", "/"), full
        else:
            print(f"  (no existe, se omite) {rel}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--destino")
    ap.add_argument("--copiar", action="store_true")
    a = ap.parse_args()
    lista = list(archivos())
    total = sum(os.path.getsize(p) for _, p in lista)
    grupos = {}
    for rel, p in lista:
        k = "/".join(rel.split("/")[:3]) if rel.startswith("datos/") else rel.split("/")[0]
        grupos[k] = grupos.get(k, 0) + os.path.getsize(p)
    for k, v in sorted(grupos.items(), key=lambda kv: -kv[1]):
        print(f"{v / 2**20:10,.0f} MB  {k}")
    print(f"{total / 2**30:10,.2f} GB  TOTAL ({len(lista)} archivos)")
    if not (a.copiar and a.destino):
        print("\nVista previa: agrega --destino <carpeta> --copiar para copiar.")
        return
    copiados = 0
    for rel, p in lista:
        dst = os.path.join(a.destino, rel)
        if os.path.isfile(dst) and os.path.getsize(dst) == os.path.getsize(p) and os.path.getmtime(dst) >= os.path.getmtime(p):
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(p, dst)
        copiados += 1
        print(f"  copiado {rel}")
    print(f"\n{copiados} archivo(s) copiado(s) a {a.destino}")


if __name__ == "__main__":
    main()
