"""
Modelo financiero GD — replica del modelo Solcor (Lérida 20260923 / Monsalve 20260909).

Misma estructura que el Excel:
  Supuestos -> Motor mensual (31 años) -> Deuda -> Impuestos (Ley 1715) -> Retornos.
Cada bloque de calculo cita la fila del Excel que replica (Motor!13 = fila 13 de la hoja Motor),
para poder auditarlo lado a lado. Las TIR se anualizan como en la Portada:
(IRR mensual + 1)^12 - 1.

Validado contra "20260923 Modelo Financiero Lérida 0.1 km.xlsx" (ver validar_lerida()).
"""

import calendar
import copy
import json
import os
from datetime import date

BASE = os.path.dirname(os.path.abspath(__file__))
MESES_HORIZONTE = 372  # columnas B..NI del Motor


def _cargar_defecto():
    with open(os.path.join(BASE, "supuestos_solcor.json"), encoding="utf-8") as f:
        return json.load(f)


SUPUESTOS_DEFECTO = _cargar_defecto()


# --------------------------------------------------------------------------
# utilidades de fecha y financieras (equivalentes Excel)
# --------------------------------------------------------------------------

def edate(d: date, meses: int) -> date:
    y, m = divmod(d.month - 1 + meses, 12)
    y += d.year
    return date(y, m + 1, min(d.day, calendar.monthrange(y, m + 1)[1]))


def pmt(r, n, pv):
    """Equivalente a -PMT(r, n, pv) de Excel (pago positivo)."""
    if n <= 0:
        return 0.0
    if r == 0:
        return pv / n
    return pv * r / (1 - (1 + r) ** -n)


def irr(flujos, guess=0.02):
    """TIR periodica. Newton desde guess (como Excel) y biseccion de respaldo.
    Con flujos mensuales a 30+ años, (1+r)**i se desborda (r alto) o se anula (r cerca de -1):
    la busqueda se limita a tasas mensuales entre -50 % y 100 % (anual de -99,98 % a +400.000 %)."""
    LO, HI = -0.5, 1.0

    def npv(r):
        try:
            return sum(f / (1 + r) ** i for i, f in enumerate(flujos))
        except (OverflowError, ZeroDivisionError):
            return None

    def dnpv(r):
        try:
            return sum(-i * f / (1 + r) ** (i + 1) for i, f in enumerate(flujos))
        except (OverflowError, ZeroDivisionError):
            return None

    r = guess
    for _ in range(100):
        v, d = npv(r), dnpv(r)
        if v is None or not d:
            break
        r2 = r - v / d
        if not LO < r2 < HI:  # Newton se sale del rango: resuelve la biseccion
            break
        if abs(r2 - r) < 1e-12:
            return r2
        r = r2
    lo, hi = LO, HI
    vlo, vhi = npv(lo), npv(hi)
    if vlo is None or vhi is None or vlo * vhi > 0:
        return None
    for _ in range(300):
        mid = (lo + hi) / 2
        if npv(lo) * npv(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def anualizar(r):
    return None if r is None else (1 + r) ** 12 - 1


# --------------------------------------------------------------------------
# curva de precios PPA (Supuestos!B79:F111)
# --------------------------------------------------------------------------

def _ipc(s, anio):
    """IPC por año calendario: Supuestos!F52 (<=2026), F53 (2027), F54 (2028), C52 (LP)."""
    if anio <= 2026:
        return s["ipc_2026"]
    if anio == 2027:
        return s["ipc_2027"]
    if anio == 2028:
        return s["ipc_2028"]
    return s["ipc_lp"]


def factor_inicial(s, cod):
    """Factor IPC del primer año de operación (Supuestos!D79). Sin mes base: 1 + IPC 2027, como la plantilla. Con
    mes base negociado del PPA («AAAA-MM»): el IPC acumulado mes a mes desde ese mes hasta el COD."""
    base = str(s.get("ppa_mes_base") or "")[:7]
    try:
        by, bm = int(base[:4]), int(base[5:7])
    except ValueError:
        return 1 + s["ipc_2027"]
    meses = (cod.year - by) * 12 + cod.month - bm
    f = 1.0
    for k in range(max(0, meses)):
        anio = by + (bm - 1 + k) // 12
        f *= (1 + _ipc(s, anio)) ** (1 / 12)
    return f


def curva_precios(s, anio_cod, cod=None):
    reales = [float(v) for v in s["ppa_real"] if v not in (None, "")]
    n = 33  # filas 79..111
    # Supuestos!C112: decrecimiento real implicito de la curva ingresada, extrapolado despues
    cagr = (reales[-1] / reales[0]) ** (1 / (len(reales) - 1)) - 1 if len(reales) > 1 and reales[0] else 0.0
    while len(reales) < n:
        reales.append(reales[-1] * (1 + cagr))
    filas = []
    factor = factor_inicial(s, cod) if cod else 1 + s["ipc_2027"]  # Supuestos!D79 = 1*(1+$F$53)
    for k in range(n):
        anio = anio_cod + k
        if k > 0:
            factor *= 1 + _ipc(s, anio)
        filas.append(dict(anio=anio, real=reales[k], factor=factor, precio=reales[k] * factor))
    return filas, cagr


# --------------------------------------------------------------------------
# modelo
# --------------------------------------------------------------------------

def correr(supuestos=None):
    s = copy.deepcopy(SUPUESTOS_DEFECTO)
    s.update({k: v for k, v in (supuestos or {}).items() if v is not None})

    kwp, kw = s["capacidad_kwp"], s["capacidad_kw"]
    capex = s["capex_epc"] + s["capex_rtb"]                       # Supuestos!C17
    if not kwp or kwp <= 0:
        raise ValueError("El bloque GD no tiene capacidad DC (0 kWp): revisa el módulo (Wp) y la configuración de la mesa.")
    capex_kwp = capex / kwp                                         # Supuestos!C13
    arriendo_mes = s["canon_ha_mes"] * s["area_ha"]                 # Supuestos!E42/12
    firma = date.fromisoformat(s["fecha_firma"])
    hitos = s["hitos"]                                              # Supuestos!B27:D31
    cod = edate(firma, sum(h["mes"] for h in hitos))                # Supuestos!C33
    fechas_hito, acum = [], 0
    for h in hitos:
        acum += h["mes"]
        fechas_hito.append((edate(firma, acum), h["pct"]))

    precios, cagr = curva_precios(s, cod.year, cod)
    por_anio = {p["anio"]: p for p in precios}

    def factor(anio_cal):   # IFERROR(INDEX(Supuestos!D79:D111, ...), 1)
        return por_anio[anio_cal]["factor"] if anio_cal in por_anio else 1.0

    def ibr(anio):          # Supuestos!C69:C73
        c = s["ibr"]
        if anio <= 2025:
            return c["2025"]
        return c.get(str(anio), c["lp"]) if anio <= 2028 else c["lp"]

    vida_meses = s["anios_operacion"] * 12
    m_rows = []
    saldo_prev = 0.0
    for i in range(MESES_HORIZONTE):
        f = edate(firma, i)                                                         # Motor!4
        mes_op = (f.year - cod.year) * 12 + f.month - cod.month                     # Motor!8
        op = 1 if 1 <= mes_op <= vida_meses else 0                                  # Motor!7
        a = (mes_op - 1) // 12 + 1 if op else 0                                     # Motor!9
        capex_m = -capex * sum(p for fh, p in fechas_hito if fh == f)               # Motor!11

        deg = 0 if a <= 1 else s["degradacion_anio1"] + s["degradacion_siguientes"] * (a - 2)
        gen = kwp * (s["produccion_especifica"] / 12) * (1 - deg) * (1 - s["castigo_generacion"]) if op else 0.0
        anio_precio = cod.year + a - 1
        precio = por_anio[anio_precio]["precio"] if op and anio_precio in por_anio else 0.0
        ingresos = gen * precio                                                     # Motor!15

        fx = factor(anio_precio) if op else 1.0
        opex = {}
        opex["seguros"] = -capex_kwp * kwp * s["seguros_pct"] / 12 * fx if op else 0.0          # Motor!17
        opex["om"] = -(s["om_cop_kwp_anio"] * kwp) / 12 * fx if op else 0.0                     # Motor!18
        rec = 0.0
        for anio_rec in (s["recambio_inversores_anio_1"], s["recambio_inversores_anio_2"]):   # Motor!19-20
            if anio_rec <= s["anios_operacion"] and f == edate(cod, anio_rec * 12):
                rec -= s["recambio_inversores_pct"] * capex_kwp * kwp * factor(cod.year + anio_rec - 1)
        opex["recambio_inversores"] = rec
        opex["arriendo"] = -arriendo_mes * fx if op else 0.0                                     # Motor!21
        opex["cnd_sic_lac"] = -(s["cnd_sic_lac_cop_kw_mes"] * kwp) * fx if op else 0.0           # Motor!22
        opex["arranque_parada"] = -gen * s["arranque_parada_cop_kwh"] * fx if op else 0.0        # Motor!23
        opex["representacion"] = -gen * s["representacion_cop_kwh"] * fx if op else 0.0         # Motor!24
        opex["cgm"] = -gen * s["cgm_cop_kwh"] * fx if op else 0.0                                # Motor!25
        opex["ica"] = -(s["ica_cop_kw_anio"] * (1 + s["ipc_2027"]) * kwp) / 12 * fx if op else 0.0  # Motor!26
        opex["gmf_4x1000"] = sum(opex.values()) * 0.004                                          # Motor!27
        opex_total = sum(opex.values())
        ebitda = ingresos + opex_total                                              # Motor!29

        # Deuda — Motor!31-38
        desemb = -capex_m * s["apalancamiento"]
        saldo_ini = desemb if i == 0 else saldo_prev + desemb
        tasa = (1 + ibr(f.year) + s["spread_ibr"]) ** (1 / 12) - 1
        restantes = max(0, s["plazo_deuda_anios"] * 12 - (mes_op - 1))
        if saldo_ini <= 1 or not op:
            pago = 0.0
        else:
            pago = min(pmt(tasa, restantes, saldo_ini), saldo_ini * (1 + tasa)) if restantes > 0 else 0.0
        interes = saldo_ini * tasa if saldo_ini > 1 else 0.0
        amort = pago - interes
        saldo_fin = max(0.0, saldo_ini - amort)
        saldo_prev = saldo_fin

        m_rows.append(dict(fecha=f, anio=f.year, mes=f.month, mes_op=mes_op, op=op, anio_op=a,
                           capex=capex_m, gen=gen, precio=precio, ingresos=ingresos, opex=opex,
                           opex_total=opex_total, ebitda=ebitda, desembolso=desemb, saldo_ini=saldo_ini,
                           pago=pago, interes=interes, amort=amort, saldo_fin=saldo_fin,
                           fcf_proyecto=capex_m + ebitda,                                   # Motor!42
                           fcf_equity=capex_m + ebitda + desemb - pago))                    # Motor!43

    # Impuestos — hoja Impuestos (año calendario desde el COD)
    imp = []
    perdida_prev, der_pool_prev = 0.0, 0.0
    anio_ini = cod.year
    der_total = capex * s["deduccion_especial_pct"]
    anios_dep = round(1 / s["depreciacion_acelerada_anual"]) if s["depreciacion_acelerada_anual"] else 0
    for k in range(35):
        y = anio_ini + k
        ebitda_y = sum(r["ebitda"] for r in m_rows if r["anio"] == y)
        int_y = sum(r["interes"] for r in m_rows if r["anio"] == y)
        dep = capex * s["depreciacion_acelerada_anual"] if y <= anio_ini + anios_dep - 1 else 0.0
        renta_op = ebitda_y - int_y - dep                                           # F
        perdida = min(0.0, renta_op + perdida_prev)                                 # G
        gravable = max(0.0, renta_op + perdida_prev)                                # H
        nuevo = der_total if y == anio_ini else 0.0
        der = min(der_pool_prev + nuevo, 0.5 * gravable) if y <= anio_ini + 14 else 0.0  # I
        der_pool = der_pool_prev + nuevo - der                                      # J
        imp_sas = (gravable - der) * s["impuesto_renta"]                            # L
        imp_opt = (renta_op - nuevo) * s["impuesto_renta"]                          # M
        imp.append(dict(anio=y, ebitda=ebitda_y, intereses=int_y, depreciacion=dep, renta_operativa=renta_op,
                        der_aplicada=der, impuesto_sas=imp_sas, impuesto_optimo=imp_opt))
        perdida_prev, der_pool_prev = perdida, der_pool
    imp_por_anio = {r["anio"]: r for r in imp}

    for r in m_rows:  # Motor!44-47: se paga en abril el impuesto del año anterior
        prev = imp_por_anio.get(r["anio"] - 1)
        r["imp_sas"] = -prev["impuesto_sas"] if r["mes"] == 4 and prev else 0.0
        r["imp_opt"] = -prev["impuesto_optimo"] if r["mes"] == 4 and prev else 0.0
        r["fcf_equity_sas"] = r["fcf_equity"] + r["imp_sas"]
        r["fcf_equity_opt"] = r["fcf_equity"] + r["imp_opt"]

    tir = dict(
        proyecto_pretax=anualizar(irr([r["fcf_proyecto"] for r in m_rows])),
        equity_pretax=anualizar(irr([r["fcf_equity"] for r in m_rows])),
        equity_sas=anualizar(irr([r["fcf_equity_sas"] for r in m_rows])),
        equity_optimo=anualizar(irr([r["fcf_equity_opt"] for r in m_rows])),
    )

    # Resumen anual por año de operación (para la tabla de la interfaz)
    anual = []
    for a in range(1, s["anios_operacion"] + 1):
        rs = [r for r in m_rows if r["anio_op"] == a]
        anual.append(dict(anio_op=a, anio=cod.year + a - 1,
                          gen_mwh=sum(r["gen"] for r in rs) / 1000,
                          precio=rs[0]["precio"] if rs else 0,
                          ingresos=sum(r["ingresos"] for r in rs),
                          opex=sum(r["opex_total"] for r in rs),
                          ebitda=sum(r["ebitda"] for r in rs),
                          servicio_deuda=sum(r["pago"] for r in rs),
                          fcf_equity_sas=sum(r["fcf_equity_sas"] for r in rs)))

    año1 = anual[0]
    dscr = [x["ebitda"] / x["servicio_deuda"] for x in anual if x["servicio_deuda"] > 0]
    return dict(
        supuestos=s,
        cod=cod.isoformat(),
        capex=dict(total=capex, epc=s["capex_epc"], rtb=s["capex_rtb"], cop_kwp=capex_kwp,
                   usd_kwp=capex_kwp / s["trm"], deuda=capex * s["apalancamiento"],
                   equity=capex * (1 - s["apalancamiento"])),
        tir=tir,
        anio1=dict(gen_mwh=año1["gen_mwh"], precio=año1["precio"], ingresos=año1["ingresos"],
                   opex=año1["opex"], ebitda=año1["ebitda"],
                   margen_ebitda=año1["ebitda"] / año1["ingresos"] if año1["ingresos"] else 0,
                   arriendo=arriendo_mes * 12 * por_anio[cod.year]["factor"]),
        dscr_min=min(dscr) if dscr else None,
        beneficios=dict(der=der_total * s["impuesto_renta"], depreciacion=capex * s["impuesto_renta"]),
        precios=precios[:s["anios_operacion"]], cagr_real=cagr, anual=anual,
    )


# --------------------------------------------------------------------------
# puente con el evaluador: un modelo por bloque GD
# --------------------------------------------------------------------------

def evaluar_gds(gds, area_arriendo_ha, params_fin):
    """Corre el modelo para cada bloque GD del layout. El arriendo se reparte por
    kWp entre bloques. params_fin trae EPC en COP/kWp y RTB por GD."""
    p = dict(params_fin or {})
    epc_kwp = p.pop("epc_cop_kwp", SUPUESTOS_DEFECTO["epc_cop_kwp"])
    total_kwp = sum(g["dc_kwp"] for g in gds) or 1
    out = []
    for g in gds:
        s = dict(p)
        s["capacidad_kwp"] = g["dc_kwp"]
        s["capacidad_kw"] = g["ac_kw"]
        s["capex_epc"] = epc_kwp * g["dc_kwp"]
        s["area_ha"] = area_arriendo_ha * g["dc_kwp"] / total_kwp
        out.append(correr(s))
    return out


# --------------------------------------------------------------------------
# TRM vigente
# --------------------------------------------------------------------------
_trm_cache = {}


def trm_vigente():
    """TRM certificada por la Superintendencia Financiera (la misma que publica el Banco de la República), desde el
    conjunto abierto de datos.gov.co. Se consulta como mucho una vez por hora."""
    import json as _json
    import time as _time
    import urllib.request
    if _trm_cache.get("t", 0) > _time.time() - 3600:
        return _trm_cache["r"]
    url = "https://www.datos.gov.co/resource/32sa-8pi3.json?$order=vigenciadesde%20DESC&$limit=1"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "EvaluadorGD-Solcor/2.2"}), timeout=15) as r:
            d = _json.load(r)[0]
        res = dict(ok=True, trm=float(d["valor"]), desde=d.get("vigenciadesde", "")[:10], hasta=d.get("vigenciahasta", "")[:10],
                   fuente="Superintendencia Financiera de Colombia (TRM certificada), datos.gov.co")
    except Exception as e:  # sin internet o servicio caído: el usuario escribe la TRM a mano
        return dict(ok=False, motivo=f"No se pudo consultar la TRM ({str(e)[:80]}). Escríbela a mano.")
    _trm_cache.update(t=_time.time(), r=res)
    return res


def validar_lerida():
    """Supuestos de 'Modelo Financiero Lérida 0.1 km' (23-sep-2026) y TIR de su Portada."""
    s = dict(capacidad_kwp=1337, capacidad_kw=990, capex_epc=3492406248, capex_rtb=168008958,
             produccion_especifica=1650, om_cop_kwp_anio=40000, area_ha=1.2, canon_ha_mes=2000000,
             ppa_real=[342, 337.25, 332.5, 327.75, 313.5, 305.9, 305.9, 299.25, 299.25, 296.4, 296.4, 296.4, 294.5, 294.5,
                       289.75, 289.75], ppa_mes_base="")
    esperado = dict(proyecto_pretax=0.17637930227377119, equity_pretax=0.22840512050139172,
                    equity_sas=0.21267315647347984, equity_optimo=0.52963614928193148)
    r = correr(s)
    return {k: (r["tir"][k], v, r["tir"][k] - v) for k, v in esperado.items()}


if __name__ == "__main__":
    for k, (calc, exc, dif) in validar_lerida().items():
        print(f"{k:16s} calculado {calc:.6%}  Excel {exc:.6%}  dif {dif * 1e4:+.3f} pb")
