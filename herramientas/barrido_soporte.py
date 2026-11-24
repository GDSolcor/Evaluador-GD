"""
Barrido diario de soporte: lista los tickets abiertos y registra la respuesta de cada uno.

    python herramientas/barrido_soporte.py                      -> escribe datos/soporte_barrido.md con los abiertos
    python herramientas/barrido_soporte.py --todos              -> incluye resueltos y descartados
    python herramientas/barrido_soporte.py responder 12 resuelto "Texto de la respuesta"
    python herramientas/barrido_soporte.py --db <ruta soporte.db>   (base alternativa, para pruebas)

Estados: nuevo, en_revision, planeado, resuelto, descartado. El usuario ve la respuesta en
"Soporte → Mis tickets" del evaluador. Las imágenes quedan en datos/soporte_adjuntos/.
"""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
args = sys.argv[1:]
if "--db" in args:
    i = args.index("--db")
    os.environ["EVALUADOR_SOPORTE"] = args[i + 1]
    del args[i:i + 2]
else:
    # misma regla que servidor.py: datos/ salvo que la carpeta esté en SharePoint/OneDrive
    local = os.path.join(os.environ.get("LOCALAPPDATA", ""), "EvaluadorGD", "soporte.db")
    propia = os.path.join(BASE, "datos", "soporte.db")
    os.environ["EVALUADOR_SOPORTE"] = propia if os.path.isfile(propia) or not os.path.isfile(local) else local
sys.path.insert(0, os.path.join(BASE, "motor"))
import soporte  # noqa: E402

ADMIN = dict(id=0, rol="admin")


def barrido(todos=False):
    ts = soporte.listar(ADMIN, todos=True, estado=None if todos else "abiertos")
    ts.sort(key=lambda t: (t["tipo"] != "error", t["creado"]))
    lineas = [f"# Barrido de soporte · {soporte._ahora()}", "",
              f"{len(ts)} ticket(s) {'en total' if todos else 'abiertos'}. Base: {soporte.DB_PATH}", ""]
    for t in ts:
        lineas += [f"## #{t['id']} · {t['tipo_txt']} · {t['titulo']}",
                   f"- Estado: {t['estado_txt']} · {t['creado']} · {t['nombre']} <{t['correo']}>", "",
                   t["descripcion"], ""]
        if t["contexto"]:
            lineas += ["Contexto:"] + [f"- {k}: {v}" for k, v in t["contexto"].items() if v not in ("", None)] + [""]
        for r in soporte.rutas_adjuntos(t["id"]):
            lineas.append(f"- Adjunto: {r}")
        if t["mensajes"]:
            lineas += ["", "Conversación:"] + [f"- [{m['creado']}] {m['nombre']} ({m['rol']}): {m['texto']}" for m in t["mensajes"]]
            if t["mensajes"][-1]["rol"] == "usuario":
                lineas.append("- >>> El último mensaje es del usuario: espera respuesta.")
        lineas.append("")
    salida = os.path.join(os.path.dirname(os.path.abspath(soporte.DB_PATH)), "soporte_barrido.md")
    with open(salida, "w", encoding="utf-8") as f:
        f.write("\n".join(lineas))
    print(f"{len(ts)} ticket(s) -> {salida}")
    for t in ts:
        print(f"  #{t['id']:<4} {t['tipo_txt']:<7} {t['estado_txt']:<12} {t['creado'][:16]}  {t['titulo']}")


if __name__ == "__main__":
    if args[:1] == ["responder"]:
        soporte.responder(int(args[1]), args[2], args[3] if len(args) > 3 else "")
        print(f"Ticket #{args[1]} -> {args[2]}")
    else:
        barrido("--todos" in args)
