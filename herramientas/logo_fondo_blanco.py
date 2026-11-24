"""
Genera web/marca/solcor_documentos.png: el logo positivo sobre fondo blanco opaco (con un margen).

Por qué: el logo original tiene fondo transparente y letras azul oscuro. Word para la web (y Word con tema
oscuro) pinta la hoja de negro y deja la imagen tal cual, así que «SOLCOR COLOMBIA» desaparece y solo se ven
los cuadros verdes (ticket #46). Con fondo blanco el logo se ve igual en cualquier modo.

Uso:  python herramientas/logo_fondo_blanco.py
"""
import os
import struct
import zlib

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORIGEN = os.path.join(BASE, "web", "marca", "solcor_positivo.png")
DESTINO = os.path.join(BASE, "web", "marca", "solcor_documentos.png")


def leer_png(datos):
    assert datos[:8] == b"\x89PNG\r\n\x1a\n", "no es PNG"
    pos, idat, ihdr = 8, b"", None
    while pos < len(datos):
        n, tipo = struct.unpack(">I4s", datos[pos:pos + 8])
        cuerpo = datos[pos + 8:pos + 8 + n]
        if tipo == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", cuerpo)
        elif tipo == b"IDAT":
            idat += cuerpo
        pos += 12 + n
    w, h, prof, color, _, _, entrelazado = ihdr
    if prof != 8 or color not in (2, 6) or entrelazado:
        raise ValueError("solo PNG RGB/RGBA de 8 bits sin entrelazar")
    bpp = 4 if color == 6 else 3
    crudo = zlib.decompress(idat)
    paso = w * bpp
    filas, prev, i = [], bytearray(paso), 0
    for _ in range(h):
        f = crudo[i]; linea = bytearray(crudo[i + 1:i + 1 + paso]); i += 1 + paso
        for x in range(paso):
            a = linea[x - bpp] if x >= bpp else 0
            b = prev[x]
            c = prev[x - bpp] if x >= bpp else 0
            if f == 1:
                linea[x] = (linea[x] + a) & 255
            elif f == 2:
                linea[x] = (linea[x] + b) & 255
            elif f == 3:
                linea[x] = (linea[x] + (a + b) // 2) & 255
            elif f == 4:
                p = a + b - c; pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                linea[x] = (linea[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        filas.append(linea); prev = linea
    return w, h, bpp, filas


def escribir_png(w, h, filas_rgb):
    def trozo(tipo, cuerpo):
        return struct.pack(">I", len(cuerpo)) + tipo + cuerpo + struct.pack(">I", zlib.crc32(tipo + cuerpo) & 0xffffffff)
    crudo = b"".join(b"\x00" + bytes(f) for f in filas_rgb)
    return (b"\x89PNG\r\n\x1a\n" + trozo(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + trozo(b"IDAT", zlib.compress(crudo, 9)) + trozo(b"IEND", b""))


def sobre_blanco(datos, margen=0.06):
    w, h, bpp, filas = leer_png(datos)
    m = int(h * margen)
    W, H = w + 2 * m, h + 2 * m
    blanco = bytearray(b"\xff" * (W * 3))
    salida = [bytearray(blanco) for _ in range(m)]
    for f in filas:
        linea = bytearray(b"\xff" * (m * 3))
        for x in range(w):
            r, g, b = f[x * bpp], f[x * bpp + 1], f[x * bpp + 2]
            a = f[x * bpp + 3] if bpp == 4 else 255
            linea += bytes(((v * a + 255 * (255 - a)) // 255) for v in (r, g, b))
        linea += b"\xff" * (m * 3)
        salida.append(linea)
    salida += [bytearray(blanco) for _ in range(m)]
    return escribir_png(W, H, salida)


if __name__ == "__main__":
    with open(ORIGEN, "rb") as fh:
        png = sobre_blanco(fh.read())
    with open(DESTINO, "wb") as fh:
        fh.write(png)
    print(DESTINO, len(png), "bytes")
