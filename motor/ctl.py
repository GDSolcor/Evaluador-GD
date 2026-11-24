"""
Certificado de Tradicion y Libertad (SNR) -> datos estructurados + analisis preliminar de titulos.

Lee el texto del PDF (pdf_texto) y arma: encabezado del folio, cabida y linderos, complementacion,
anotaciones (documento, acto, cancelaciones, personas DE/A con marca X/I, cedulas y cuotas),
titulares actuales con su cuota, y una matriz de riesgos con documentos pendientes, siguiendo el
formato del "Estudio de Titulos Preliminar" de Solcor (Monsalve 083-4434).

El resultado es un borrador para revision juridica: las reglas marcan lo que un abogado revisa
primero (medidas vigentes, falsa tradicion, sucesiones recientes, proindiviso), no reemplazan su concepto.
"""
import datetime as dt
import re
import unicodedata
from fractions import Fraction

import pdf_texto

MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
         "octubre", "noviembre", "diciembre"]

_RUIDO = re.compile(
    r"^(SUPERINTENDENCIA DE NOTARIADO Y REGISTRO LA GUARDA.*|La validez de este documento.*|"
    r"Certificado generado con el Pin.*|Pagina \d+ TURNO.*|Impreso el .*|\"?ESTE CERTIFICADO REFLEJA.*|"
    r"HASTA LA FECHA Y HORA DE SU EXPEDICION\"?|No tiene validez sin la firma.*|"
    r"OFICINA DE REGISTRO DE INSTRUMENTOS PUBLICOS DE .*|CERTIFICADO DE TRADICION|MATRICULA INMOBILIARIA|"
    r"(\* )*\*|(= )+=?)\s*$")

# actos que transfieren dominio (aunque el folio los clasifique como "limitacion al dominio", p. ej. 0301 adjudicacion sucesion)
_TRANSFIERE = re.compile(r"COMPRA[ -]?VENTA|ADJUDICACI[OÓ]N|DONACI[OÓ]N|PERMUTA|APORTE|DACI[OÓ]N EN PAGO|REMATE|"
                         r"PRESCRIPCI[OÓ]N|TITULACI[OÓ]N|ADJUDICACION|CESI[OÓ]N DE DERECHOS|RESTITUCI[OÓ]N|"
                         r"TRANSFERENCIA|FIDUCIA|LIQUIDACI[OÓ]N (DE LA )?SOCIEDAD|DECLARACI[OÓ]N (JUDICIAL )?DE PERTENENCIA")


def _sin_tildes(s):
    return unicodedata.normalize("NFD", s or "").encode("ascii", "ignore").decode()


def _fecha(s):
    try:
        d, m, a = [int(x) for x in re.split(r"[-/]", s)]
        return dt.date(a, m, d)
    except (ValueError, TypeError):
        return None


def fecha_larga(d):
    return f"{d.day} de {MESES[d.month - 1]} de {d.year}" if d else ""


def _nombre_propio(s):
    """RUBIANO AMADOR OMAR -> Rubiano Amador Omar (conserva siglas como S.A.S.)."""
    out = []
    for w in s.split():
        out.append(w if (("." in w and len(w) <= 6) or w in ("S.A.", "SAS", "S.A.S.", "LTDA", "E.S.P.")) else w.capitalize())
    return " ".join(out).replace(" De ", " de ").replace(" Del ", " del ").replace(" La ", " la ").replace(" Y ", " y ")


def _num_id(s):
    return "{:,}".format(int(s)).replace(",", ".") if s and s.isdigit() else (s or "")


def limpiar(texto):
    lineas = []
    for l in texto.splitlines():
        l = l.strip()
        if not l or _RUIDO.match(l):
            continue
        # la marca de agua a veces queda pegada al final de una linea
        l = re.sub(r"\s*SUPERINTENDENCIA DE NOTARIADO Y REGISTRO LA GUARDA DE LA FE PUBLICA.*$", "", l)
        if l:
            lineas.append(l)
    return lineas


def _persona(linea):
    """'A: RUBIANO GARZON CRISTHIAN CAMILO CC# 1013670372 25% X' -> dict."""
    rol = "DE" if linea.startswith("DE:") else "A"
    s = linea.split(":", 1)[1].strip()
    marca = ""
    # folios antiguos: la marca va antes de la cédula y esta con puntos ('HORTENSIA X C.C 41.401.392')
    m = re.search(r"\s(X|I)\s+(C\.?\s?C\.?|NIT|TI|CE)\s*#?\s*([\d.\-]{4,})\s*$", s)
    if m:
        tipo = "NIT" if m.group(2) == "NIT" else re.sub(r"[.\s]", "", m.group(2))
        marca, s = m.group(1), s[:m.start()].strip() + f" {tipo}# " + re.sub(r"[.\-]", "", m.group(3))
    m = None if marca else (re.search(r"\s(X|I)\s*$", s) or re.search(r"(?<=\d)(X|I)\s*$", s))  # 'NIT# 8907040216X'
    if m:
        marca, s = m.group(1), s[:m.start()].strip()
    m = re.search(r"\(\s*C\.?\s*C\.?\s*([\d.]+)\s*\)\s*$", s)  # 'BARRIGA LOPEZ PEDRO (C.C. 79.402.992)'
    if m:
        s = s[:m.start()].strip() + " CC# " + m.group(1).replace(".", "")
    pct = None
    m = re.search(r"\s(\d{1,3}(?:[.,]\d+)?)\s*%\s*$", s)
    if m:
        pct, s = float(m.group(1).replace(",", ".")), s[:m.start()].strip()
    if not marca:
        m = re.search(r"\s(X|I)\s*$", s)
        if m:
            marca, s = m.group(1), s[:m.start()].strip()
    tipo_id, num = "", ""
    m = re.search(r"\s(CC|NIT|C\.\s?C\.?|TI|CE|PA|NUIP|SE)\s*#?\s*([\d.\-]+)\s*$", s)
    if m:
        tipo_id, num, s = re.sub(r"[.\s]", "", m.group(1)), re.sub(r"[.\-]", "", m.group(2)), s[:m.start()].strip()
        if tipo_id == "NIT" and len(num) > 9:
            num = num[:9] + "-" + num[9:]
    return dict(rol=rol, nombre=s, nombre_propio=_nombre_propio(s), tipo_id=tipo_id, id=num, marca=marca, pct=pct)


def parsear(texto):
    # algunos folios traen la Ñ como barra invertida ('SE\OR', 'MONTA\EZ')
    texto = re.sub(r"(?<=[A-ZÁÉÍÓÚ])\\(?=[A-ZÁÉÍÓÚ])", "Ñ", texto)
    raw = texto
    L = limpiar(texto)
    T = "\n".join(L)
    g = lambda pat, s=raw, fl=0: (re.search(pat, s, fl).group(1).strip() if re.search(pat, s, fl) else "")
    r = dict(
        pin=g(r"Pin No:\s*(\d+)"),
        matricula=g(r"Nro Matr[ií]cula:\s*([\d-]+)"),
        impreso=g(r"Impreso el (.+?)(?: a las|\n)"),
        circulo=g(r"CIRCULO REGISTRAL:\s*(.+?)\s+DEPTO:"),
        depto=g(r"DEPTO:\s*(.+?)\s+MUNICIPIO:"),
        municipio=g(r"MUNICIPIO:\s*(.+?)\s+VEREDA:"),
        vereda=g(r"VEREDA:\s*(.*)"),
        fecha_apertura=g(r"FECHA APERTURA:\s*([\d-]+)"),
        radicacion=g(r"RADICACI[OÓ]N:\s*(\S+)"),
        apertura_con=g(r"CON:\s*(.+?)\s+DE:"),
        codigo_catastral=g(r"CODIGO CATASTRAL:\s*(\d+)"),
        codigo_catastral_ant=g(r"COD CATASTRAL ANT:\s*(\d+)"),
        nupre=g(r"NUPRE:[ \t]*([^\s]*)"),
        estado=g(r"ESTADO DEL FOLIO:\s*(\w+)"),
        turno=g(r"TURNO:\s*(\S+)"),
        expedido=g(r"FECHA:\s*([\d-]+)\s*\n?EXPEDIDO EN") or g(r"TURNO: \S+ FECHA:\s*([\d-]+)"),
        registrador=g(r"\n([A-ZÁÉÍÓÚÑ ]+)\nREGISTRADOR"),
    )
    r["circulo_nombre"] = r["circulo"].split("-", 1)[-1].strip() if r["circulo"] else ""
    if r["vereda"].startswith(("FECHA", "")) and "FECHA APERTURA" in r["vereda"]:
        r["vereda"] = ""
    # fecha de expedicion
    m = re.search(r"Impreso el (\d+) de (\w+) de (\d{4})", raw)
    r["fecha_expedicion"] = None
    if m and _sin_tildes(m.group(2)).lower() in [_sin_tildes(x) for x in MESES]:
        r["fecha_expedicion"] = dt.date(int(m.group(3)), [_sin_tildes(x) for x in MESES].index(_sin_tildes(m.group(2)).lower()) + 1, int(m.group(1)))
    # bloques de texto
    def entre(a, b):
        m = re.search(a + r"(.*?)" + b, T, re.S)
        return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
    r["linderos"] = entre(r"DESCRIPCION: CABIDA Y LINDEROS", r"AREA Y COEFICIENTE")
    area_blk = entre(r"AREA Y COEFICIENTE", r"COMPLEMENTACION")
    ha = re.search(r"AREA - HECTAREAS:\s*([\d.,]+)?\s*METROS CUADRADOS:\s*([\d.,]+)?", area_blk)
    r["area_estructurada"] = None
    if ha and (ha.group(1) or ha.group(2)):
        r["area_estructurada"] = float((ha.group(1) or "0").replace(",", ".")) * 10000 + float((ha.group(2) or "0").replace(".", "").replace(",", "."))
    m = re.search(r"EXTENSI[OÓ]N (?:DE )?([\d.,]+)[- ]?([\d]*)\s*(HAS|HECT|H\.|M2|MTS|METROS|FANEGADAS|FGS)", r["linderos"])
    r["area_narrativa"] = None
    if m:
        ent, dec, u = m.group(1), m.group(2), m.group(3)
        try:
            if u.startswith(("HAS", "HECT", "H.")):
                v = float(ent.replace(",", ".")) + (float("0." + dec) if dec else 0)
                r["area_narrativa"] = v * 10000
            elif u.startswith("FAN") or u == "FGS":
                r["area_narrativa"] = float(ent.replace(",", ".")) * 6400
            else:
                r["area_narrativa"] = float(ent.replace(".", "").replace(",", "."))
        except ValueError:
            pass
    if not r["area_narrativa"]:
        # 'TIENE UNA AREA DE 8 HECTAREAS Y 3.960 METROS CUADRADOS', 'CABIDA DE 12 HAS 500 M2', 'AREA DE 5.000 M2'
        num = lambda s: float(s.replace(".", "").replace(",", ".")) if re.fullmatch(r"\d{1,3}(\.\d{3})+(,\d+)?", s) else float(s.replace(",", "."))
        m = re.search(r"([\d.,]*\d)\s*(?:HECT[AÁ]REAS?|HAS\b\.?)(?:\s*(?:Y|,|CON|MAS|M[AÁ]S)?\s*([\d.,]*\d)\s*(?:METROS|MTS?\b\.?|M2|MT2))?", r["linderos"])
        m2 = re.search(r"(?:[AÁ]REA|CABIDA|EXTENSI[OÓ]N|SUPERFICIE)[A-ZÁÉÍÓÚ ]{0,30}?\s(?:DE\s)?([\d.,]*\d)\s*(?:METROS CUADRADOS|MTS?\.? ?2|M2|MT2|MTS\b)", r["linderos"])
        try:
            if m:
                r["area_narrativa"] = num(m.group(1)) * 10000 + (num(m.group(2)) if m.group(2) else 0)
            elif m2:
                r["area_narrativa"] = num(m2.group(1))
        except ValueError:
            pass
    r["complementacion"] = entre(r"COMPLEMENTACION(?: TRADICION)?:", r"DIRECCION DEL INMUEBLE")
    dirb = entre(r"DIRECCION DEL INMUEBLE", r"(?:DETERMINACION DEL INMUEBLE|MATRICULA ABIERTA|ANOTACION: Nro)")
    r["tipo_predio"] = g(r"Tipo Predio:[ \t]*([^\n]*)", T)
    mdir = re.search(r"DIRECCION DEL INMUEBLE\n(.*?)\n(?:DETERMINACION DEL INMUEBLE|MATRICULA ABIERTA|ANOTACION: Nro)", T, re.S)
    r["direccion"] = " ".join(l for l in (mdir.group(1).split("\n") if mdir else []) if not l.startswith("Tipo Predio")).strip()
    r["direccion"] = re.sub(r"^\d\)\s*", "", r["direccion"])
    r["nombre_predio"] = re.split(r"\s+\d\)\s+", r["direccion"])[0].strip() if r["direccion"] else ""
    r["destinacion"] = g(r"DESTINACION ECONOMICA:[ \t]*([^\n]*)", T)
    mb = re.search(r"MATRICULA ABIERTA CON BASE EN .*?\n(.*?)\n(?:ANOTACION|DIRECCION|$)", T, re.S)
    r["matriculas_base"] = re.findall(r"\d{3}\s*-\s*\d+", mb.group(1)) if mb else []
    r["matriculas_base"] = [x.replace(" ", "") for x in r["matriculas_base"]]
    tot = re.search(r"NRO TOTAL DE ANOTACIONES:\s*\*?(\d+)", T)
    r["total_anotaciones"] = int(tot.group(1)) if tot else None
    sal = re.search(r"SALVEDADES:.*?\n(.*?)(?:FIN DE ESTE DOCUMENTO|USUARIO:|$)", T, re.S)
    r["salvedades"] = re.sub(r"\s+", " ", sal.group(1)).strip() if sal else ""

    # anotaciones
    anots = []
    partes = re.split(r"(?=ANOTACION: Nro\s*\d+)", T)
    for p in partes[1:]:
        p = re.split(r"NRO TOTAL DE ANOTACIONES|SALVEDADES:", p)[0]
        m = re.match(r"ANOTACION: Nro\s*(\d+)\s+Fecha:\s*([\d-]+)\s+Radicaci[oó]n:\s*(\S+)", p)
        if not m:
            continue
        a = dict(nro=int(m.group(1)), fecha=m.group(2), fecha_d=_fecha(m.group(2)), radicacion=m.group(3))
        doc = re.search(r"Doc:\s*(.+?)(?:\s+VALOR ACTO:\s*\$?\s*([\d.,]*))?\n", p + "\n")
        a["doc"] = doc.group(1).strip() if doc else ""
        va = re.search(r"VALOR ACTO:\s*\$?\s*([\d.,]*)", p)
        a["valor"] = va.group(1) if va else ""
        md = re.match(r"(ESCRITURA|OFICIO|SENTENCIA|RESOLUCI[OÓ]N|AUTO|CERTIFICADO|ACTA|DOCUMENTO|PROVIDENCIA|DECRETO)\S*\s+(\S+)\s+DEL?\s+([\d-]+)\s*(.*)", a["doc"], re.I)
        a["doc_tipo"], a["doc_num"], a["doc_fecha"], a["doc_entidad"] = (md.groups() if md else ("", "", "", a["doc"]))
        a["doc_tipo"] = a["doc_tipo"].upper()
        a["doc_fecha_d"] = _fecha(a["doc_fecha"])
        canc = re.search(r"Se cancela anotaci[oó]n No:\s*([\d, y]+)", p)
        a["cancela"] = [int(x) for x in re.findall(r"\d+", canc.group(1))] if canc else []
        esp = re.search(r"ESPECIFICACION:\s*(.+?)(?=\nPERSONAS QUE INTERVIENEN|\nDE:|\nA:|$)", p, re.S)
        e = re.sub(r"\s+", " ", esp.group(1)).strip() if esp else ""
        a["especificacion"] = e
        # folios antiguos traen la especificación sin categoría: ': 915 ADJUDICACION EN LIQUIDACION…'
        me = re.match(r"([A-ZÁÉÍÓÚ ]*?):\s*(\d+)\s+(.*)", e)
        a["categoria"], a["codigo"], a["acto"] = (me.group(1).strip(), me.group(2), me.group(3).strip()) if me else ("", "", e)
        a["personas"] = [_persona(l) for l in p.split("\n") if re.match(r"(DE|A):\s", l)]
        anots.append(a)
    r["anotaciones"] = anots
    for a in anots:
        a["cancelada_por"] = None
    por_nro = {a["nro"]: a for a in anots}
    for a in anots:
        for n in a["cancela"]:
            if n in por_nro:
                por_nro[n]["cancelada_por"] = a["nro"]
    return r


def _clase(a):
    cat = _sin_tildes(a["categoria"]).upper()
    acto = _sin_tildes(a["acto"]).upper()
    if cat.startswith("CANCELACION"):
        return "cancelacion"
    if cat.startswith("FALSA TRADICION"):
        return "falsa_tradicion"
    if cat.startswith("MEDIDA CAUTELAR"):
        return "medida_cautelar"
    if cat.startswith("GRAVAMEN"):
        return "gravamen"
    if not cat:
        # folio sin categoría (códigos antiguos de tres cifras): se clasifica por el texto del acto
        if re.match(r"CANCELACI", acto):
            return "cancelacion"
        if re.search(r"EMBARGO|DEMANDA|MEDIDA CAUTELAR|PROHIBICI|SECUESTRO|OFERTA DE COMPRA", acto):
            return "medida_cautelar"
        if re.search(r"HIPOTECA|VALORIZACI|PRENDA|GRAVAMEN|ANTICRESIS", acto):
            return "gravamen"
        if re.search(r"FALSA TRADICI|DERECHOS Y ACCIONES|DERECHOS HERENCIALES|POSESI[OÓ]N|VENTA DE MEJORAS|COSA AJENA", acto):
            return "falsa_tradicion"
        if re.search(r"SERVIDUMBRE|USUFRUCTO|PATRIMONIO DE FAMILIA|AFECTACI[OÓ]N A VIVIENDA|CONDICI[OÓ]N RESOLUTORIA|USO Y HABITACI", acto):
            return "limitacion"
    if _TRANSFIERE.search(acto) or cat.startswith("MODO DE ADQUISICION"):
        return "transferencia"
    if cat.startswith("LIMITACION"):
        return "limitacion"
    return "otro"


_NO_PERSONA = re.compile(r"^(Y )?OTROS?\b|^PERSONAS INDETERMINADAS|^INDETERMINADOS|^HER[E]?DEROS (IN)?DETERMINADOS|^\(SIC\)$")
_RESET = re.compile(r"PERTENENCIA|PRESCRIPCI|REMATE|EXTINCI[OÓ]N DE DOMINIO|EXPROPIACI|ADJUDICACI[OÓ]N DE BALD|TITULACI[OÓ]N")


def _norm_nombre(s):
    s = _sin_tildes(s).upper()
    # una sociedad es la misma con o sin 'LTDA', 'LIMITADA' o 'EN LIQUIDACION'
    s = re.sub(r"\(SIC\)|\bVDA\.?\s*(DE)?\b|\bNIT#?.*$|\bCC#?.*$|\bEN LIQUIDACION\b|\bLIMITADA\b|\bLTDA\b|[^A-Z0-9 ]", " ", s)
    return " ".join(w for w in s.split() if w not in ("DE", "DEL", "LA", "LOS", "LAS", "Y"))


def titulares(r):
    """Reconstruye titulares y cuotas recorriendo las transferencias en orden de anotacion.
    Dominio pleno (X) lleva cuota; los compradores de 'derechos y acciones' (falsa tradicion, I)
    quedan sin cuota. Si un acto no permite calcular la cuota (venta de 'parte' sin %), la cuota
    queda en None ('por determinar') en vez de inventarla."""
    tit = {}  # clave normalizada -> dict(persona, cuota Fraction|None, anots, marca)

    def buscar(p):
        k = _norm_nombre(p["nombre"])
        if k in tit:
            return k
        for kk, t in tit.items():
            if p["id"] and t["persona"]["id"] == p["id"]:
                return kk
        tk = set(k.split())
        for kk in tit:
            ok = set(kk.split())
            if tk and ok and (tk <= ok or ok <= tk) and min(len(tk), len(ok)) >= 2:
                return kk
        import difflib
        for kk in tit:  # errores de digitacion del folio (SACHEZ / SANCHEZ)
            if difflib.SequenceMatcher(None, k, kk).ratio() >= 0.9:
                return kk
        return None

    for a in r["anotaciones"]:
        a["clase"] = _clase(a)
        if a["clase"] not in ("transferencia", "falsa_tradicion"):
            continue
        acto = _sin_tildes(a["acto"]).upper()
        de = [p for p in a["personas"] if p["rol"] == "DE" and not _NO_PERSONA.search(p["nombre"])]
        aa = [p for p in a["personas"] if p["rol"] == "A" and not _NO_PERSONA.search(p["nombre"])]
        if not aa:
            continue
        if re.search(r"USUFRUCTO", acto) and not re.search(r"NUDA", acto):
            a["clase"] = "limitacion"  # el usufructuario no es titular del dominio
            continue
        if re.search(r"SIMULACI|NULIDAD|RESOLUCI[OÓ]N DE(L)? CONTRATO|REVOCATORIA|RESCISI", acto):
            a["clase"] = "revocatoria"  # deja sin efecto un acto anterior: se revisa a mano
            continue
        exp_total = sum((Fraction(str(p["pct"])) for p in aa if p["pct"]), Fraction(0))
        area_det = bool(re.search(r"FRANJA|\d[\d.,]*\s*(HAS?\b|HECT|M2|MTS|METROS)|\bLOTE DE TERRENO\b|SEGREGAD", acto))
        reset = (not de) or (bool(_RESET.search(acto)) and not area_det) or abs(exp_total - 100) < 1
        por_area = (not reset) and area_det
        a["por_area"] = por_area
        falsa = a["clase"] == "falsa_tradicion" or all(p["marca"] == "I" for p in aa)
        if falsa:
            for p in de:
                k = buscar(p)
                if k and tit[k]["marca"] == "I":
                    tit.pop(k)
            for p in aa:
                k = buscar(p)
                if k:
                    tit[k]["anots"].append(a["nro"])
                    tit[k]["marca"] = tit[k]["marca"] if tit[k]["marca"] == "X" else "I"
                    tit[k]["mixto"] = tit[k]["marca"] == "X"
                else:
                    tit[_norm_nombre(p["nombre"])] = dict(persona=dict(p), cuota=None, anots=[a["nro"]], marca="I")
            continue
        # --- transferencia de dominio
        if por_area and tit:
            # venta o expropiacion de una parte del terreno (cuerpo cierto): el adquirente recibe un area,
            # no una cuota; normalmente se abre un folio nuevo. Los vendedores conservan el resto.
            for p in aa:
                if not buscar(p):
                    tit[_norm_nombre(p["nombre"])] = dict(persona=dict(p), cuota=None, anots=[a["nro"]], marca=p["marca"], area=True)
            continue
        if reset or not tit:
            sale = Fraction(1)
            fr = re.search(r"\b(\d{1,2})\s*/\s*(\d{1,3})\b", acto)
            if fr and re.search(r"A CADA UNO", acto) and int(fr.group(2)):
                sale = min(Fraction(1), Fraction(int(fr.group(1)), int(fr.group(2))) * len(aa))
            for k in [k for k, t in tit.items()]:
                tit.pop(k)
        else:
            ks = [buscar(p) for p in de]
            conocidos = [k for k in ks if k]
            if de and not conocidos:
                a["de_no_reconocido"] = True  # quien transfiere no coincide con ningún titular anterior: se avisa
            cuotas_de = [tit[k]["cuota"] for k in conocidos]
            sale = None if (not conocidos or any(c is None for c in cuotas_de)) else sum(cuotas_de, Fraction(0))
            m = re.search(r"(\d{1,3}(?:[.,]\d+)?)\s*%", acto)
            fr = re.search(r"\b(\d{1,2})\s*/\s*(\d{1,3})\b", acto)
            parcial = re.search(r"\bPARTE\b", acto) and not re.search(r"TOTALIDAD|DERECHOS? DE CUOTA|CUOTA PARTE", acto)
            if m and not any(p["pct"] for p in aa):
                v = Fraction(m.group(1).replace(",", ".")) / 100
                if cuotas_de and all(x is not None and abs(x - v) < Fraction(1, 1000) for x in cuotas_de):
                    v = sum(cuotas_de, Fraction(0))  # 'el 7,74 % que tiene cada uno'
                sale = v if sale is None or v < sale else sale
            elif fr and not any(p["pct"] for p in aa) and int(fr.group(2)):
                v = Fraction(int(fr.group(1)), int(fr.group(2)))
                if re.search(r"A CADA UNO", acto):
                    v *= len(aa)
                sale = v if (sale is None or v <= sale) else sale
                parcial = False
            elif parcial and not any(p["pct"] for p in aa):
                sale = None
            for k in conocidos:
                t = tit[k]
                if sale is not None and t["cuota"] is not None and len(conocidos) == 1:
                    t["cuota"] -= min(t["cuota"], sale)
                    if t["cuota"] <= 0:
                        tit.pop(k)
                elif sale is not None and t["cuota"] is not None:
                    tit.pop(k)
                elif not parcial:
                    tit.pop(k)
                else:
                    t["cuota"] = None
        exp = [p for p in aa if p["pct"]]
        suma_exp = sum((Fraction(str(p["pct"])) / 100 for p in exp), Fraction(0))
        escala = Fraction(1)
        if exp and sale is not None and len(exp) == len(aa) and abs(suma_exp - 1) < Fraction(1, 50) and sale < 1:
            escala = sale  # porcentajes relativos a la cuota transferida
        for p in aa:
            if p["pct"]:
                parte = Fraction(str(p["pct"])) / 100 * escala
            elif sale is None:
                parte = None
            else:
                parte = (sale - suma_exp) / max(1, len(aa) - len(exp))
            k = buscar(p)
            if k:
                t = tit[k]
                t["cuota"] = None if (parte is None or t["cuota"] is None) else t["cuota"] + parte
                t["anots"].append(a["nro"])
                t["marca"] = p["marca"] or t["marca"]
                if p["id"]:
                    t["persona"]["id"], t["persona"]["tipo_id"] = p["id"], p["tipo_id"]
            else:
                tit[_norm_nombre(p["nombre"])] = dict(persona=dict(p), cuota=parte, anots=[a["nro"]], marca=p["marca"])
    # completar cedulas vistas en cualquier anotacion (p. ej. en cancelaciones posteriores)
    ids = {}
    for a in r["anotaciones"]:
        for p in a["personas"]:
            if p["id"]:
                ids.setdefault(_norm_nombre(p["nombre"]), (p["tipo_id"], p["id"]))
    out = []
    for k, t in tit.items():
        if not t["persona"]["id"] and k in ids:
            t["persona"]["tipo_id"], t["persona"]["id"] = ids[k]
        out.append(dict(nombre=t["persona"]["nombre"], nombre_propio=t["persona"]["nombre_propio"],
                        tipo_id=t["persona"]["tipo_id"] or ("NIT" if re.search(r"\bS\.?A\.?S?\.?$|\bS\.A\.|LTDA|S\. ?EN C|CIA\b|SOCIEDAD|MUNICIPIO|BANCO", t["persona"]["nombre"]) else "CC"),
                        id=t["persona"]["id"], cuota=None if t["cuota"] is None else float(t["cuota"]),
                        anotaciones=t["anots"], marca=t["marca"], mixto=t.get("mixto", False), por_area=t.get("area", False)))
    out.sort(key=lambda t: (t["marca"] != "X", -(t["cuota"] or 0), t["nombre"]))
    conocidas = [t["cuota"] for t in out if t["marca"] != "I" and not t["por_area"]]
    tot = None if (not conocidas or any(c is None for c in conocidas)) else sum(conocidas)
    return out, tot


def _tokens_nombre(s):
    return set(re.sub(r"[^A-Z0-9 ]", " ", _sin_tildes(s or "").upper()).split())


def coincidencias_propietario(titulares, propietario):
    """Titulares inscritos que corresponden a quien ofrece el predio: por cédula/NIT (solo dígitos)
    o porque todas las palabras del nombre dado están en el nombre inscrito (sin tildes ni orden)."""
    if not propietario:
        return []
    ced = re.sub(r"\D", "", str(propietario.get("cedula") or ""))
    nom = _tokens_nombre(propietario.get("nombre"))
    out = []
    for t in titulares or []:
        tid = re.sub(r"\D", "", str(t.get("id") or ""))
        if (ced and tid and ced == tid) or (nom and len(nom) >= 2 and nom <= _tokens_nombre(t.get("nombre"))):
            out.append(t)
    return out


def cuota_txt(t):
    if t["marca"] == "I" and t["cuota"] is None:
        return "derechos y acciones (I)"
    if t["cuota"] is None:
        return "cuota por determinar"
    v = t["cuota"] * 100
    return (f"{v:.0f} %" if abs(v - round(v)) < 0.05 else f"{v:.2f} %".replace(".", ",")) + (" + derechos (I)" if t.get("mixto") else "")


def _titulo_de(r, nro):
    a = next((x for x in r["anotaciones"] if x["nro"] == nro), None)
    if not a:
        return ""
    t = f"{a['doc_tipo'].capitalize()[:3]}. {a['doc_num']}" if a["doc_tipo"] else a["doc"]
    if a["doc_fecha"]:
        t += f" del {a['doc_fecha']}"
    return t


def analizar(texto, propietario=None, hoy=None):
    """Parsea el CTL y arma el estudio preliminar. propietario = dict(nombre, cedula) opcional
    (quien ofrece el predio) para cruzarlo contra los titulares inscritos."""
    hoy = hoy or dt.date.today()
    r = parsear(texto)
    tit, tot = titulares(r)
    r["titulares"] = tit
    r["cuota_total"] = tot
    riesgos, docs = [], []
    anots = r["anotaciones"]
    por = {a["nro"]: a for a in anots}

    def riesgo(nivel, hallazgo, recomendacion):
        riesgos.append(dict(nivel=nivel, hallazgo=hallazgo, recomendacion=recomendacion))

    if not anots:
        riesgo("Alto", "No se pudieron leer anotaciones del certificado (¿PDF escaneado o de otro formato?).",
               "Cargar el certificado descargado de la SNR (PDF con texto) o transcribir las anotaciones.")
    if r["estado"] and r["estado"].upper() != "ACTIVO":
        riesgo("Alto", f"El folio aparece en estado {r['estado']}.", "Confirmar con la ORIP el estado del folio y el folio que lo sustituye.")

    vig = [a for a in anots if not a["cancelada_por"] and a.get("clase") in ("medida_cautelar", "gravamen", "limitacion", "falsa_tradicion")]
    for a in vig:
        acto = _sin_tildes(a["acto"]).upper()
        ref = f"anotación {a['nro']:03d} ({a['fecha']}, {a['doc'][:60]})"
        if a["clase"] == "medida_cautelar":
            riesgo("Alto", f"Medida cautelar VIGENTE: {a['acto'].lower()} — {ref}.",
                   "Solicitar el levantamiento de la medida y su inscripción antes de firmar; el predio no puede comprometerse con la medida vigente.")
            docs.append(f"Oficio de levantamiento de la medida cautelar de la anotación {a['nro']:03d} y su registro.")
        elif a["clase"] == "gravamen":
            nivel = "Medio" if "HIPOTECA" in acto else "Medio"
            acreedor = ", ".join(p["nombre_propio"] for p in a["personas"] if p["rol"] == "A")
            riesgo(nivel, f"Gravamen VIGENTE: {a['acto'].lower()} a favor de {acreedor or 'acreedor sin identificar'} — {ref}.",
                   "Solicitar paz y salvo o cancelación de la hipoteca, o autorización del acreedor para el arrendamiento/servidumbre del proyecto.")
            docs.append(f"Paz y salvo / escritura de cancelación del gravamen de la anotación {a['nro']:03d}.")
        elif a["clase"] == "falsa_tradicion":
            riesgo("Alto", f"Falsa tradición (dominio incompleto) — {ref}: {a['acto'].lower()}.",
                   "El titular no tiene dominio pleno; se requiere saneamiento (proceso de pertenencia o formalización ANT) antes de comprometer el predio.")
        else:
            if re.search(r"USUFRUCTO", acto):
                riesgo("Alto", f"Usufructo vigente — {ref}.", "El usufructuario debe suscribir el contrato; verificar vigencia y beneficiario.")
            elif re.search(r"VIVIENDA FAMILIAR|PATRIMONIO DE FAMILIA", acto):
                riesgo("Medio", f"{a['acto'].capitalize()} vigente — {ref}.",
                       "Requiere firma del cónyuge/compañero o levantamiento; verificar alcance frente a un arrendamiento de largo plazo.")
            elif re.search(r"SERVIDUMBRE", acto):
                riesgo("Medio", f"Servidumbre inscrita — {ref}: {a['acto'].lower()}.",
                       "Ubicar el trazado de la servidumbre y excluirlo del área útil del proyecto.")
            elif re.search(r"DEMANDA|PROHIBICI", acto):
                riesgo("Alto", f"{a['acto'].capitalize()} vigente — {ref}.", "Verificar el proceso judicial y su estado antes de continuar.")
            elif a.get("clase") == "limitacion" and not _TRANSFIERE.search(acto):
                riesgo("Medio", f"Limitación al dominio vigente — {ref}: {a['acto'].lower()}.", "Verificar su alcance y si afecta el uso del predio para el proyecto.")

    # titularidad
    if tit:
        sin_x = [t for t in tit if t["marca"] != "X"]
        incompletos = [t for t in tit if t["marca"] == "I"]
        if incompletos:
            riesgo("Alto", "Titulares con dominio incompleto (I): " + ", ".join(t["nombre_propio"] for t in incompletos) + ".",
                   "Sanear la falsa tradición antes de negociar; solo quien tiene dominio pleno puede comprometer el predio.")
        sin_marca = [t for t in sin_x if t["marca"] == ""]
        if sin_marca:
            riesgo("Alto", "El certificado no muestra marca X de dominio pleno para: " + ", ".join(t["nombre_propio"] for t in sin_marca) +
                   f" (anotación {', '.join(str(n) for n in sorted({t['anotaciones'][-1] for t in sin_marca}))}).",
                   "Verificar contra el documento original o con la ORIP si ya se reconoció dominio pleno (X) sobre su cuota.")
            docs.append("CTL con marca X de dominio pleno para todos los titulares.")
        if len(tit) > 1:
            riesgo("Medio", f"Predio en común y proindiviso: {len(tit)} titulares.",
                   "Todos los copropietarios deben firmar el contrato (o otorgar poder); confirmar que no hay oposición entre ellos.")
        if tot is None:
            riesgo("Medio", "No es posible determinar con certeza la cuota de cada titular a partir del folio (ventas de 'parte' o derechos sin porcentaje).",
                   "Revisar las escrituras de adquisición para fijar la cuota de cada titular.")
        elif abs(tot - 1) > 0.02:
            riesgo("Medio", f"Las cuotas reconstruidas de dominio pleno suman {tot * 100:.1f} % (no 100 %).",
                   "Revisar manualmente la aritmética de cuotas en las anotaciones de transferencia (puede haber ventas parciales no expresadas en %).")
        # adquisiciones recientes / sucesiones
        ult = max((t["anotaciones"][-1] for t in tit), default=None)
        if ult and ult in por and por[ult]["fecha_d"] and r["fecha_expedicion"]:
            a = por[ult]
            meses = (r["fecha_expedicion"] - a["fecha_d"]).days / 30.4
            suces = "SUCESI" in _sin_tildes(a["acto"]).upper()
            if suces and meses < 24:
                causante = ", ".join(p["nombre_propio"] for p in a["personas"] if p["rol"] == "DE")
                riesgo("Alto", f"La anotación {a['nro']:03d} (adjudicación en sucesión de {causante}) es reciente: se registró el {a['fecha']}, "
                       f"{meses:.0f} meses antes de la expedición del certificado.",
                       f"Solicitar el expediente completo de la sucesión de {causante} y confirmar que la partición quedó en firme, que se identificaron todos los herederos (incluido cónyuge supérstite) y que no hay impugnaciones.")
                docs.append(f"Expediente completo de la sucesión de {causante}, con la sentencia o acta de adjudicación y constancia de ejecutoria.")
            elif meses < 24:
                riesgo("Medio", f"El título más reciente (anotación {a['nro']:03d}, {a['acto'].lower()}) se registró hace {meses:.0f} meses.",
                       "Verificar la escritura y que no existan procesos pendientes sobre esa transferencia.")
    elif anots:
        riesgo("Alto", "No se pudo reconstruir la titularidad actual a partir de las anotaciones.", "Revisión manual de la cadena de tradición.")
    for a in anots:
        if a.get("clase") == "revocatoria":
            riesgo("Medio", f"La anotación {a['nro']:03d} ({a['acto'].lower()[:120]}) deja sin efecto un acto anterior.",
                   "Revisar la sentencia o documento y reconstruir a mano la titularidad resultante.")
        if a.get("de_no_reconocido"):
            quien = ", ".join(p["nombre_propio"] for p in a["personas"] if p["rol"] == "DE")
            riesgo("Medio", f"En la anotación {a['nro']:03d} ({a['acto'].lower()[:80]}) transfiere {quien}, que no coincide con ningún titular de las anotaciones anteriores.",
                   "Revisar si es el mismo titular escrito de otra forma o si falta una anotación intermedia; la cuota de los titulares queda por confirmar.")
        if a.get("por_area"):
            riesgo("Medio", f"La anotación {a['nro']:03d} transfiere un área determinada ({a['acto'].lower()[:100]}), no una cuota.",
                   "Confirmar el área remanente del folio y si el área transferida tiene folio propio (segregación).")
    por_area = [t for t in tit if t.get("por_area")]
    if por_area:
        riesgo("Medio", "Adquirentes de un área parcial que aparecen en el folio: " + ", ".join(t["nombre_propio"] for t in por_area) + ".",
               "Verificar si su área se segregó a otro folio; en ese caso no son titulares del área remanente.")

    # propietario que ofrece el predio vs. titulares inscritos
    if propietario and (propietario.get("cedula") or propietario.get("nombre")):
        ced = re.sub(r"\D", "", propietario.get("cedula") or "")
        coincide = coincidencias_propietario(tit, propietario)
        if not coincide:
            riesgo("Alto", f"La persona que ofrece el predio ({propietario.get('nombre') or ''} {('C.C. ' + _num_id(ced)) if ced else ''}) no figura como titular inscrito.",
                   "Verificar su calidad (poseedor, heredero no adjudicado, apoderado) y exigir el título o poder correspondiente.")
        r["propietario_verificado"] = bool(coincide)

    # consistencia y antiguedad
    orden = [(a["nro"], a["fecha_d"]) for a in anots if a["fecha_d"]]
    for (n1, f1), (n2, f2) in zip(orden, orden[1:]):
        if f2 < f1:
            riesgo("Bajo", f"Orden cronológico: la anotación {n1:03d} (inscrita {f1:%d-%m-%Y}) es posterior a la {n2:03d} (inscrita {f2:%d-%m-%Y}).",
                   "Aclarar con la ORIP el orden real de inscripción si se requiere trazabilidad exacta.")
    for a in anots:
        if a["doc_fecha_d"] and a["fecha_d"] and (a["fecha_d"] - a["doc_fecha_d"]).days > 730:
            riesgo("Bajo", f"La anotación {a['nro']:03d} ({a['acto'].lower()}) se inscribió {((a['fecha_d'] - a['doc_fecha_d']).days / 365):.0f} años después del documento ({a['doc_fecha']}).",
                   "Confirmar que durante ese lapso no se afectó la situación jurídica del predio.")
    if not r["area_estructurada"]:
        area_txt = f"{r['area_narrativa'] / 10000:,.2f} ha".replace(",", "X").replace(".", ",").replace("X", ".") if r["area_narrativa"] else "sin área"
        riesgo("Bajo", f"El campo estructurado de área del folio está en blanco; el área ({area_txt}) solo consta en la descripción narrativa de linderos.",
               f"Contrastar con la ficha catastral (número predial {r['codigo_catastral'] or 'sin número'}) y, si difieren, prever una actualización de cabida y linderos.")
    sin_gravamen = not any(a for a in vig if a.get("clase") in ("gravamen", "medida_cautelar"))
    hist = [a for a in anots if a.get("clase") in ("gravamen", "medida_cautelar") and a["cancelada_por"]]
    if sin_gravamen:
        riesgo("Bajo", "No hay gravámenes, embargos ni medidas cautelares vigentes" + (f": los {len(hist)} registros históricos están cancelados." if hist else "."),
               "Confirmar con un certificado de tradición actualizado (vigencia no mayor a 30 días) al momento de cualquier trámite.")
    if r["fecha_expedicion"] and (hoy - r["fecha_expedicion"]).days > 30:
        riesgo("Medio", f"El certificado se expidió el {r['fecha_expedicion']:%d-%m-%Y} ({(hoy - r['fecha_expedicion']).days} días): no está vigente para trámites.",
               "Solicitar un CTL actualizado antes de firmar.")
    if r["matriculas_base"]:
        riesgo("Informativo", f"El folio se abrió con base en la(s) matrícula(s) {', '.join(r['matriculas_base'])}.",
               "Si la operación exige el historial completo, solicitar el certificado de la matrícula antecedente.")
    rural = "VEREDA" in (r["vereda"] + r["direccion"]).upper() or bool(r["vereda"]) or (r["codigo_catastral"][5:7] == "00" if len(r["codigo_catastral"]) == 30 else False)
    if rural:
        riesgo("Informativo", f"El predio es rural ({('vereda ' + r['vereda'].title() + ', ') if r['vereda'] else ''}{r['municipio'].title()}, {r['depto'].title()}).",
               "Verificar en la ANT y la URT que no esté sujeto a restricciones de la Ley 160 de 1994 (UAF, baldíos) ni a procesos de restitución.")
    if tit:
        riesgo("Informativo", "El certificado no indica el estado civil de los titulares.",
               "Verificar con " + ", ".join(t["nombre_propio"] for t in tit) + " si existe sociedad conyugal o patrimonial vigente.")

    # documentos
    por_escritura = {}
    for t in tit:
        for n in t["anotaciones"]:
            a = por.get(n)
            if a and a["doc_tipo"].startswith("ESCRITURA"):
                por_escritura.setdefault(n, []).append(t["nombre_propio"])
    base_docs = []
    for n in sorted(por_escritura):
        a = por[n]
        base_docs.append(f"Escritura {a['doc_num']} del {a['doc_fecha']}, {_nombre_propio(a['doc_entidad'])} — "
                         f"{a['acto'].lower()} (título de {' y '.join(por_escritura[n])}).")
    docs = base_docs + docs + [
        "Certificado de tradición y libertad actualizado (vigencia no mayor a 30 días) al momento de cualquier trámite.",
        "Cédulas de los titulares actuales y confirmación de su estado civil.",
        f"Certificado catastral actualizado (número predial {r['codigo_catastral'] or 'por confirmar'}).",
        f"Paz y salvo del impuesto predial del año en curso, municipio de {r['municipio'].title()}.",
    ]
    vistos, docs_u = set(), []
    for d in docs:
        if d not in vistos:
            vistos.add(d)
            docs_u.append(d)
    orden_n = {"Alto": 0, "Medio": 1, "Bajo": 2, "Informativo": 3}
    riesgos.sort(key=lambda x: orden_n[x["nivel"]])
    r["riesgos"] = riesgos
    r["documentos"] = docs_u
    r["resumen"] = _resumen(r, vig)
    r["fecha_estudio"] = hoy
    return r


def _resumen(r, vig):
    anots, tit = r["anotaciones"], r["titulares"]
    out = []
    ft = [a for a in anots if a.get("clase") == "falsa_tradicion"]
    prim = next((a for a in anots if a.get("clase") == "transferencia"), None)
    if not ft and prim:
        out.append(f"La cadena de tradición no presenta falsa tradición en ninguna de las {len(anots)} anotaciones; "
                   f"el primer acto de transferencia del folio es la anotación {prim['nro']:03d} ({prim['acto'].lower()}, {prim['fecha']}).")
    elif ft:
        out.append(f"Se identifican {len(ft)} anotación(es) de falsa tradición ({', '.join(f'{a['nro']:03d}' for a in ft)}): la titularidad puede no ser plena.")
    vg = [a for a in vig if a.get("clase") in ("gravamen", "medida_cautelar")]
    hist = [a for a in anots if a.get("clase") in ("gravamen", "medida_cautelar") and a["cancelada_por"]]
    if vg:
        out.append("Hay cargas VIGENTES: " + "; ".join(f"{a['acto'].lower()} (anot. {a['nro']:03d})" for a in vg) + ".")
    else:
        out.append("No hay gravámenes ni medidas cautelares vigentes" +
                   (f"; los {len(hist)} registrados históricamente (" + ", ".join(f"{a['nro']:03d}" for a in hist) + ") fueron cancelados." if hist else "."))
    if tit:
        out.append("Titulares actuales: " + "; ".join(f"{t['nombre_propio']} ({cuota_txt(t)})" for t in tit) + ".")
    altos = [x for x in r.get("riesgos", []) if x["nivel"] == "Alto"]
    if altos:
        out.append("Puntos críticos a resolver antes de cualquier operación: " + " ".join(x["hallazgo"] for x in altos[:2]))
    return out


def leer_pdf(datos, propietario=None):
    ex = pdf_texto.extraer(datos)
    if ex["escaneado"]:
        return dict(ok=False, escaneado=True, error="El PDF no tiene texto (parece escaneado). Descarga el certificado original de la SNR o transcribe los datos.")
    if "ANOTACION" not in ex["texto"] and "MATRICULA" not in ex["texto"].upper():
        return dict(ok=False, error="El PDF no parece un certificado de tradición y libertad de la SNR.")
    r = analizar(ex["texto"], propietario)
    r["ok"] = True
    return r


def a_json(r):
    """Version serializable (fechas a texto) para el navegador."""
    def conv(v):
        if isinstance(v, dt.date):
            return v.isoformat()
        if isinstance(v, dict):
            return {k: conv(x) for k, x in v.items() if not k.endswith("_d")}
        if isinstance(v, list):
            return [conv(x) for x in v]
        return v
    return conv(r)


if __name__ == "__main__":
    import json
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    r = leer_pdf(open(sys.argv[1], "rb").read())
    j = a_json(r)
    for k in ("anotaciones",):
        j[k] = [f"{a['nro']:03d} {a['fecha']} [{a.get('clase')}] {a['acto'][:60]} canc={a['cancelada_por']} " +
                "; ".join(f"{p['rol']}:{p['nombre']}{'(' + p['marca'] + ')' if p['marca'] else ''}{' ' + str(p['pct']) + '%' if p['pct'] else ''}" for p in a["personas"]) for a in r["anotaciones"]]
    j.pop("complementacion", None)
    print(json.dumps(j, ensure_ascii=False, indent=1))
