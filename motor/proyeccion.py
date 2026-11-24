"""
MAGNA-SIRGAS / Origen Nacional (CTM12, EPSG:9377) <-> WGS84 geograficas, sin librerias.

Transversa de Mercator por series de Kruger en n hasta sexto orden (Karney 2011):
error de micrometros en todo el territorio colombiano. MAGNA-SIRGAS y WGS84 se tratan
como el mismo datum (diferencia < 1 m, irrelevante para visualizacion y prefactibilidad).
"""
import math

A_ELIP = 6378137.0
F_ELIP = 1 / 298.257222101  # GRS80
LAT0, LON0, K0, FE, FN = 4.0, -73.0, 0.9992, 5_000_000.0, 2_000_000.0

_n = F_ELIP / (2 - F_ELIP)
_e = math.sqrt(F_ELIP * (2 - F_ELIP))
_A = A_ELIP / (1 + _n) * (1 + _n ** 2 / 4 + _n ** 4 / 64 + _n ** 6 / 256)
_n2, _n3, _n4, _n5, _n6 = (_n ** k for k in range(2, 7))
_ALFA = [
    _n / 2 - 2 * _n2 / 3 + 5 * _n3 / 16 + 41 * _n4 / 180 - 127 * _n5 / 288 + 7891 * _n6 / 37800,
    13 * _n2 / 48 - 3 * _n3 / 5 + 557 * _n4 / 1440 + 281 * _n5 / 630 - 1983433 * _n6 / 1935360,
    61 * _n3 / 240 - 103 * _n4 / 140 + 15061 * _n5 / 26880 + 167603 * _n6 / 181440,
    49561 * _n4 / 161280 - 179 * _n5 / 168 + 6601661 * _n6 / 7257600,
    34729 * _n5 / 80640 - 3418889 * _n6 / 1995840,
    212378941 * _n6 / 319334400,
]
_BETA = [
    _n / 2 - 2 * _n2 / 3 + 37 * _n3 / 96 - _n4 / 360 - 81 * _n5 / 512 + 96199 * _n6 / 604800,
    _n2 / 48 + _n3 / 15 - 437 * _n4 / 1440 + 46 * _n5 / 105 - 1118711 * _n6 / 3870720,
    17 * _n3 / 480 - 37 * _n4 / 840 - 209 * _n5 / 4480 + 5569 * _n6 / 90720,
    4397 * _n4 / 161280 - 11 * _n5 / 504 - 830251 * _n6 / 7257600,
    4583 * _n5 / 161280 - 108847 * _n6 / 3991680,
    20648693 * _n6 / 638668800,
]


def _xi_eta(lat, dlon):
    phi, lam = math.radians(lat), math.radians(dlon)
    s = math.sin(phi)
    t = math.sinh(math.atanh(s) - _e * math.atanh(_e * s))
    xi_p = math.atan2(t, math.cos(lam))
    eta_p = math.atanh(math.sin(lam) / math.sqrt(1 + t * t))
    xi, eta = xi_p, eta_p
    for j, a in enumerate(_ALFA, 1):
        xi += a * math.sin(2 * j * xi_p) * math.cosh(2 * j * eta_p)
        eta += a * math.cos(2 * j * xi_p) * math.sinh(2 * j * eta_p)
    return xi, eta


_XI0 = _xi_eta(LAT0, 0.0)[0]


def a_ctm12(lon, lat):
    """WGS84 (lon, lat en grados) -> CTM12 (este, norte en metros)."""
    xi, eta = _xi_eta(lat, lon - LON0)
    return FE + K0 * _A * eta, FN + K0 * _A * (xi - _XI0)


# Orígenes MAGNA-SIRGAS anteriores a CTM12 (Gauss-Krüger): (lat0, lon0, k0, falso este, falso norte).
# Muchos operadores de red siguen entregando sus shapefiles en ellos (p. ej. EPSG:3116, «MAGNA Colombia Bogotá»).
ORIGENES = {
    "ctm12": (LAT0, LON0, K0, FE, FN),
    "bogota": (4.596200416666666, -74.07750791666666, 1.0, 1_000_000.0, 1_000_000.0),
    "oeste": (4.596200416666666, -77.07750791666666, 1.0, 1_000_000.0, 1_000_000.0),
    "oeste_oeste": (4.596200416666666, -80.07750791666666, 1.0, 1_000_000.0, 1_000_000.0),
    "este_central": (4.596200416666666, -71.07750791666666, 1.0, 1_000_000.0, 1_000_000.0),
    "este_este": (4.596200416666666, -68.07750791666666, 1.0, 1_000_000.0, 1_000_000.0),
}


def a_wgs84(este, norte, origen=None):
    """CTM12 (este, norte) -> WGS84 (lon, lat en grados). origen: clave de ORIGENES para otros orígenes MAGNA."""
    lat0, lon0, k0, fe, fn = ORIGENES[origen] if origen else (LAT0, LON0, K0, FE, FN)
    xi0 = _XI0 if not origen else _xi_eta(lat0, 0.0)[0]
    xi = (norte - fn) / (k0 * _A) + xi0
    eta = (este - fe) / (k0 * _A)
    xi_p, eta_p = xi, eta
    for j, b in enumerate(_BETA, 1):
        xi_p -= b * math.sin(2 * j * xi) * math.cosh(2 * j * eta)
        eta_p -= b * math.cos(2 * j * xi) * math.sinh(2 * j * eta)
    tau_p = math.sin(xi_p) / math.sqrt(math.sinh(eta_p) ** 2 + math.cos(xi_p) ** 2)
    tau = tau_p
    for _ in range(6):  # Newton: latitud conforme -> geodesica
        sigma = math.sinh(_e * math.atanh(_e * tau / math.sqrt(1 + tau * tau)))
        tp = tau * math.sqrt(1 + sigma * sigma) - sigma * math.sqrt(1 + tau * tau)
        dtau = (tau_p - tp) / (1 + tp * tp) * (1 + (1 - _e * _e) * tau * tau) / ((1 - _e * _e) * math.sqrt(1 + tau * tau))
        tau += dtau
        if abs(dtau) < 1e-14:
            break
    lat = math.degrees(math.atan(tau))
    lon = lon0 + math.degrees(math.atan2(math.sinh(eta_p), math.cos(xi_p)))
    return lon, lat


def bbox_a_ctm12(lon0, lat0, lon1, lat1):
    """Caja envolvente en CTM12 de una caja geografica (muestrea bordes: la proyeccion curva los lados)."""
    pts = []
    for i in range(5):
        for j in range(5):
            pts.append(a_ctm12(lon0 + (lon1 - lon0) * i / 4, lat0 + (lat1 - lat0) * j / 4))
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)
