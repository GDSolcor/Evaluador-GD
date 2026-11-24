"""
Catalogo de modulos fotovoltaicos del evaluador.

Base: los dos modulos de los presets (LONGi 650 de Mavalle y Jinko 730 estandar Solcor).
Se amplia cargando la ficha tecnica (PDF) del fabricante: se extraen potencia(s), dimensiones,
peso, eficiencia y celdas; el usuario confirma los valores y el modulo queda en datos/modulos.json
(compartido por todos los usuarios de este PC) y en la lista desplegable.
"""
import json
import os
import re
import threading
import time

import pdf_texto

BASE = os.path.dirname(os.path.abspath(__file__))
COMPARTIDO = os.path.join(BASE, "..", "datos", "modulos.json")  # catálogo que viaja con la herramienta (solo lectura)
ARCHIVO = os.environ.get("EVALUADOR_MODULOS", COMPARTIDO)       # módulos cargados en este PC
FICHAS_DIR = os.path.join(os.path.dirname(ARCHIVO), "fichas_modulos")
_lock = threading.Lock()

BASE_MODULOS = [
    dict(id="longi_lr8_66hyd_650", fabricante="LONGi", modelo="LR8-66HYD-650M", wp=650, largo=2.382, ancho=1.134,
         espesor=0.030, peso_kg=33.5, eficiencia=24.1, celdas=132, bifacial=True, fuente="Preset Mavalle", base=True),
    dict(id="jinko_jkm730n_66hl5", fabricante="Jinko", modelo="JKM730N-66HL5-BDV", wp=730, largo=2.384, ancho=1.303,
         espesor=0.033, peso_kg=37.5, eficiencia=23.5, celdas=132, bifacial=True, fuente="Estándar Solcor", base=True),
]

FABRICANTES = ["LONGi", "Jinko", "JinkoSolar", "Trina", "JA Solar", "Canadian Solar", "Risen", "Astronergy", "Tongwei",
               "Huasun", "DAS Solar", "GCL", "Seraphim", "Znshine", "Aiko", "First Solar", "SunPower", "Qcells", "REC", "Yingli"]


def _leer(ruta=None):
    try:
        with open(ruta or ARCHIVO, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def catalogo():
    """Base + catálogo compartido de la carpeta de la herramienta + módulos cargados en este PC."""
    vistos, out = set(), []
    compartido = _leer(COMPARTIDO) if os.path.abspath(COMPARTIDO) != os.path.abspath(ARCHIVO) else []
    for m in list(reversed(_leer())) + compartido + BASE_MODULOS:  # el local más reciente gana
        if m["id"] not in vistos:
            vistos.add(m["id"])
            out.append(m)
    orden = {m["id"]: i for i, m in enumerate(BASE_MODULOS)}
    return sorted(out, key=lambda m: (orden.get(m["id"], 99), m.get("fabricante", ""), m.get("wp", 0)))


def obtener(mid):
    return next((m for m in catalogo() if m["id"] == mid), None)


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:60]


def guardar(m, usuario="", pdf=None, nombre_pdf=""):
    """Agrega o reemplaza un modulo (validado) en el catalogo del PC."""
    req = dict(fabricante=str(m.get("fabricante") or "").strip(), modelo=str(m.get("modelo") or "").strip())
    if not req["modelo"]:
        raise ValueError("Falta el modelo del módulo.")
    try:
        wp = float(m["wp"])
        largo = float(m["largo"])
        ancho = float(m["ancho"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("Potencia (Wp), largo y ancho son obligatorios.")
    if largo > 10:  # vino en mm
        largo, ancho = largo / 1000, ancho / 1000
    if not (100 <= wp <= 1000 and 0.8 <= largo <= 3.5 and 0.5 <= ancho <= 2.0):
        raise ValueError("Valores fuera de rango: revisa potencia (100–1000 Wp) y dimensiones (largo 0,8–3,5 m, ancho 0,5–2 m).")
    if ancho > largo:
        largo, ancho = ancho, largo
    mod = dict(id=_slug(f"{req['fabricante']}_{req['modelo']}_{int(wp)}"), fabricante=req["fabricante"], modelo=req["modelo"],
               wp=wp, largo=round(largo, 4), ancho=round(ancho, 4),
               espesor=_num(m.get("espesor")), peso_kg=_num(m.get("peso_kg")), eficiencia=_num(m.get("eficiencia")),
               celdas=int(_num(m.get("celdas")) or 0) or None, bifacial=bool(m.get("bifacial")),
               voc=_num(m.get("voc")), isc=_num(m.get("isc")), vmp=_num(m.get("vmp")), imp=_num(m.get("imp")),
               fuente=nombre_pdf or m.get("fuente") or "Ficha técnica", cargado_por=usuario, fecha=time.strftime("%Y-%m-%d"))
    if pdf:
        os.makedirs(FICHAS_DIR, exist_ok=True)
        with open(os.path.join(FICHAS_DIR, mod["id"] + ".pdf"), "wb") as f:
            f.write(pdf)
    with _lock:
        lista = [x for x in _leer() if x["id"] != mod["id"]]
        lista.append(mod)
        os.makedirs(os.path.dirname(ARCHIVO), exist_ok=True)
        tmp = ARCHIVO + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(lista, f, ensure_ascii=False, indent=1)
        os.replace(tmp, ARCHIVO)
    return mod


def borrar(mid, usuario=None):
    """Con usuario (el de la sesión) solo borra el administrador o quien cargó el módulo."""
    with _lock:
        if usuario is not None and usuario.get("rol") != "admin":
            m = next((x for x in _leer() if x["id"] == mid), None)
            if m and m.get("cargado_por") != usuario.get("correo"):
                raise PermissionError("Solo el administrador o quien cargó el módulo puede borrarlo.")
        lista = [x for x in _leer() if x["id"] != mid]
        with open(ARCHIVO, "w", encoding="utf-8") as f:
            json.dump(lista, f, ensure_ascii=False, indent=1)


def _num(v):
    try:
        return float(str(v).replace(",", ".")) if v not in (None, "") else None
    except ValueError:
        return None


def extraer_ficha(datos, nombre_archivo=""):
    """Lee la ficha tecnica PDF y propone los parametros. Devuelve dict con los campos encontrados
    y 'potencias' (bins del rango); el usuario confirma antes de guardar."""
    ex = pdf_texto.extraer(datos)
    t = ex["texto"]
    tu = t.replace("（", "(").replace("）", ")").replace("～", "~")
    out = dict(ok=True, archivo=nombre_archivo, escaneado=ex["escaneado"], avisos=[])
    if ex["escaneado"]:
        out["avisos"].append("La ficha parece escaneada (sin texto): completa los datos a mano.")
    # fabricante
    fab = next((f for f in FABRICANTES if re.search(re.escape(f).replace("\\ ", r"\s*"), tu, re.I)), "")
    if not fab:
        if re.search(r"\bJKM\d", tu):
            fab = "Jinko"
        elif re.search(r"\bLR\d", tu):
            fab = "LONGi"
        elif re.search(r"\bJAM\d", tu):
            fab = "JA Solar"
        elif re.search(r"\bTSM-", tu):
            fab = "Trina"
    out["fabricante"] = "Jinko" if fab == "JinkoSolar" else fab
    # modelo y rango de potencia
    modelo = ""
    for pat in (r"\b(JKM\s?\d{3}(?:-\d{3})?[A-Z]?-\d{2}[A-Z0-9]*(?:-[A-Z0-9]+)*)", r"\b(LR\d-\d{2}[A-Z]{2,4})\b", r"\b(JAM\d{2}[A-Z]\d{2}(?:-\d{3})?(?:/[A-Z]+)?)",
                r"\b(TSM-[A-Z0-9]+(?:\.\d+)?[A-Z]*)", r"\b(CS\d[A-Z]-?\d{3}[A-Z]*)", r"\b(RSM\d{3}-\d-\d{3}[A-Z]*)"):
        m = re.search(pat, tu)
        if m:
            modelo = m.group(1).replace(" ", "")
            break
    out["modelo"] = modelo or os.path.splitext(nombre_archivo)[0][:40]
    rango = re.search(r"\b([3-9]\d{2})\s*(?:W|Wp)?\s*[-~–]\s*([3-9]\d{2})\s*(?:W|Wp|Watt|M\b)", tu)
    pot = []
    if rango:
        a, b = int(rango.group(1)), int(rango.group(2))
        if a < b and b - a <= 120:
            pot = list(range(a, b + 1, 5))
    # potencias listadas en la tabla electrica (numeros de 3 cifras multiplos de 5 dentro del rango)
    if pot:
        vistos = sorted({int(x) for x in re.findall(r"(?<![\d.])([3-9]\d{2})(?![\d.])", tu) if pot[0] <= int(x) <= pot[-1] and int(x) % 5 == 0})
        if len(vistos) >= 3:
            pot = vistos
    out["potencias"] = pot
    out["wp"] = pot[len(pot) // 2] if pot else None
    if not pot:
        m = re.search(r"(?:Pmax|Maximum Power)[^\n]{0,40}?\b([3-9]\d{2})\b", tu, re.I)
        out["wp"] = int(m.group(1)) if m else None
    # dimensiones  2384×1303×33 mm  /  2382 x 1134 x 30mm
    m = re.search(r"\b(\d{4})\s*[×xX*]\s*(\d{3,4})\s*[×xX*]\s*(\d{2})\s*mm", tu)
    if m:
        out["largo"], out["ancho"], out["espesor"] = int(m.group(1)) / 1000, int(m.group(2)) / 1000, int(m.group(3)) / 1000
    else:
        out["avisos"].append("No se encontraron las dimensiones (largo × ancho × espesor): complétalas.")
    m = re.search(r"\b(\d{2}(?:[.,]\d)?)\s*kg\b", tu, re.I)
    out["peso_kg"] = _num(m.group(1)) if m else None
    efs = [float(x.replace(",", ".")) for x in re.findall(r"\b(2\d[.,]\d{1,2})\s*%", tu) if 19 <= float(x.replace(",", ".")) <= 26]
    out["eficiencia"] = max(efs) if efs else None
    m = re.search(r"\b(1[0-9]{2}|[5-9]\d|2[0-9]{2})\s*\(\s*\d+\s*[×xX]\s*\d+\s*\)", tu)
    out["celdas"] = int(m.group(1)) if m else None
    out["bifacial"] = bool(re.search(r"bifacial|dual[- ]glass|dual-sided|bifaciality", tu, re.I))
    if not out.get("wp"):
        out["avisos"].append("No se encontró la potencia: indícala.")
    return out
