"""
Decodificador mínimo de Mapbox Vector Tiles (.pbf) con la librería estándar.

Suficiente para teselas de pg_tileserv / martin: capas, atributos y geometrías (punto, línea,
polígono) convertidas a lon/lat. Referencia: https://github.com/mapbox/vector-tile-spec (v2).
"""
import math
import struct


def _varint(b, i):
    r = s = 0
    while True:
        c = b[i]
        i += 1
        r |= (c & 0x7F) << s
        if c < 0x80:
            return r, i
        s += 7


def _campos(b):
    """Recorre un mensaje protobuf: (numero_campo, tipo, valor)."""
    i, n = 0, len(b)
    while i < n:
        k, i = _varint(b, i)
        f, t = k >> 3, k & 7
        if t == 0:
            v, i = _varint(b, i)
        elif t == 1:
            v, i = b[i:i + 8], i + 8
        elif t == 2:
            ln, i = _varint(b, i)
            v, i = b[i:i + ln], i + ln
        elif t == 5:
            v, i = b[i:i + 4], i + 4
        else:
            raise ValueError(f"tipo protobuf {t} no soportado")
        yield f, t, v


def _empacados(b):
    out, i = [], 0
    while i < len(b):
        v, i = _varint(b, i)
        out.append(v)
    return out


def _zz(n):
    return (n >> 1) ^ -(n & 1)


def _valor(b):
    for f, t, v in _campos(b):
        if f == 1:
            return v.decode("utf-8", "replace")
        if f == 2:
            return struct.unpack("<f", v)[0]
        if f == 3:
            return struct.unpack("<d", v)[0]
        if f in (4, 5):
            return v
        if f == 6:
            return _zz(v)
        if f == 7:
            return bool(v)
    return None


def _geometria(cmds, tipo, a_lonlat):
    x = y = 0
    partes, actual = [], []
    i = 0
    while i < len(cmds):
        c = cmds[i]
        i += 1
        cid, cnt = c & 7, c >> 3
        if cid in (1, 2):
            for _ in range(cnt):
                x += _zz(cmds[i])
                y += _zz(cmds[i + 1])
                i += 2
                if cid == 1 and actual:
                    partes.append(actual)
                    actual = []
                actual.append(a_lonlat(x, y))
        elif cid == 7:
            if actual:
                actual.append(actual[0])
    if actual:
        partes.append(actual)
    if tipo == 1:
        return "Point", [p[0] for p in partes]
    if tipo == 2:
        return "LineString", partes
    return "Polygon", partes


def decodificar(datos, z, x, y):
    """-> {capa: [dict(id, props, tipo, coords)]} con coordenadas lon/lat."""
    out = {}
    for f, t, capa in _campos(datos):
        if f != 3:
            continue
        nombre, claves, valores, feats, extent = "", [], [], [], 4096
        for g, _, v in _campos(capa):
            if g == 1:
                nombre = v.decode()
            elif g == 3:
                claves.append(v.decode())
            elif g == 4:
                valores.append(_valor(v))
            elif g == 5:
                extent = v
            elif g == 2:
                feats.append(v)
        n = 2 ** z

        def a_lonlat(px, py, ext=extent):
            gx = (x + px / ext) / n
            gy = (y + py / ext) / n
            lon = gx * 360.0 - 180.0
            lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * gy))))
            return (round(lon, 7), round(lat, 7))

        lista = []
        for fb in feats:
            fid, tags, tipo, geom = None, [], 0, []
            for g, _, v in _campos(fb):
                if g == 1:
                    fid = v
                elif g == 2:
                    tags = _empacados(v)
                elif g == 3:
                    tipo = v
                elif g == 4:
                    geom = _empacados(v)
            props = {claves[tags[k]]: valores[tags[k + 1]] for k in range(0, len(tags) - 1, 2)}
            gt, coords = _geometria(geom, tipo, a_lonlat)
            lista.append(dict(id=fid, props=props, tipo=gt, coords=coords))
        out[nombre] = lista
    return out


def tesela(lon, lat, z):
    n = 2 ** z
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    return x, y
