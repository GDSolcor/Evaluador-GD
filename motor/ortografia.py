"""
Restaura tildes y eñes en textos que llegan sin ellas (los certificados de tradición y el catastro
escriben en mayúsculas sin tilde: "SOTAQUIRA", "ANDRES", "DIRECCION").

Trabaja palabra por palabra con un diccionario de formas sin ambigüedad: nombres y apellidos comunes,
vocabulario registral y catastral, y los nombres de municipios y departamentos del GIS. No toca palabras
que cambian de sentido con la tilde (esta/está, el/él, si/sí, publico/público como verbo, etc.).
"""
import re
import unicodedata

_PALABRAS = """
Adán Adrián Agustín Álvaro Álvarez Andrés Ángel Ángela Ángeles Angélica Aníbal Aristóbulo Bárbara Belén Benjamín
Bermúdez Calderón Cárdenas Carreño Castañeda Castrillón César Chávez Concepción Córdoba Cortés Cristóbal Damián
Darío Débora Delfín Díaz Efraín Elías Emérita Encarnación Fabián Fabián Farfán Félix Fermín Fernández Flórez Galán Gaitán
Galván Gálvez García Garzón Germán Gómez González Guzmán Gutiérrez Héctor Hernán Hernández Hipólito Ibáñez Inés Iván
Jerónimo Jesús Jiménez Joaquín José Josué Julián Lázaro León Leónidas López Lucía Luján Márquez Martín Martínez María
Matías Máximo Medellín Mejía Méndez Mónica Moisés Morán Muñoz Narváez Nicolás Noé Núñez Ordóñez Óscar Pabón Páez Patiño
Peláez Peña Peñaloza Pérez Pinzón Quiñones Raúl Ramírez Ramón Rincón Rodríguez Rocío Román Rubén Sánchez Salomón
Sebastián Simón Sofía Solís Suárez Tomás Téllez Valentín Vásquez Vázquez Velásquez Velázquez Vélez Verónica Víctor
Yáñez Zúñiga Zárate Avendaño Acuña Briceño Londoño Montaño Nariño Ocaña Saldaña Triviño Niño Carreño Cedeño
Beltrán Bohórquez Barón Cifuentes Chacón Colón Duarte Durán Echeverría Garcés Girón Marín Millán Obregón Padrón Rendón
Roldán Rolón Tobón Tristán Villamarín Alarcón Aragón Bernabé Cárdenas Céspedes Cáceres Cúcuta Bogotá Boyacá Tunja
dirección estación vía matrícula círculo ubicación área número cédula título títulos tradición anotación anotaciones
adjudicación sucesión liquidación partición división donación cancelación constitución declaración aclaración
actualización ampliación limitación afectación resolución descripción inscripción jurisdicción nación construcción
construcciones propiedad hectáreas línea líneas eléctrica eléctrico energía común público pública únicamente según
también además más allí aquí así después través jurídico jurídica crédito créditos hipotecario económico económica
agrícola agrícolas técnico técnica último última únicos único única útil años compañía sociedad anónima limitada
país región rural urbano catastral notaría notarías pública escritura resolución oficio sentencia juzgado católica
parroquia diócesis arquidiócesis corporación fundación asociación cooperativa administración inversión inversiones
comercialización distribución generación transmisión producción explotación extensión porción fracción situación
condición condiciones obligación obligaciones posesión prohibición enajenación expropiación restitución reversión
servidumbre tránsito acueducto oleoducto gasoducto poliducto eléctricas hídrica hídrico río quebrada cañada caño
páramo montaña peñón ciénaga laguna vereda corregimiento inspección caserío kilómetro kilómetros metros océano
norte oriente occidente sur camión jardín almacén galpón corazón razón ningún algún según cónyuge supérstite
heredero herederos mayoría carácter cláusula índice código códigos artículo artículos parágrafo días período períodos
exploración perforación evaluación operación concesión reserva protección conservación restauración zonificación
válido válida vigésimo décimo séptimo sexto vigésima décima séptima trámite trámites ámbito depósito propósito
""".split()
# palabras del diccionario que NO se corrigen aunque aparezcan arriba por error de edición
_EXCLUIR = {"rio", "leon", "martin", "marin", "roman", "baron", "nino", "pena", "montano", "via", "cano", "canada", "mas",
            "ultimo", "ultima", "publica", "titulo", "numero", "valido", "valida", "tramite", "deposito", "proposito", "limitada",
            "anonima", "indice", "codigo", "articulo", "ano", "anos", "norte", "oriente", "occidente", "sur", "metros", "vereda",
            "sociedad", "propiedad", "duarte", "cifuentes", "tunja", "quebrada", "laguna", "corregimiento", "servidumbre",
            "acueducto", "oleoducto", "gasoducto", "poliducto", "escritura", "oficio", "sentencia", "juzgado", "parroquia",
            "rural", "urbano", "catastral", "cooperativa", "heredero", "herederos", "hectareas"}
# estas sí, aunque coincidan con un verbo poco probable en un documento registral
_FORZAR = {"via": "vía", "numero": "número", "titulo": "título", "codigo": "código", "ultimo": "último", "ultima": "última",
           "publica": "pública", "anonima": "anónima", "indice": "índice", "articulo": "artículo", "rio": "río",
           "martin": "Martín", "marin": "Marín", "leon": "León", "roman": "Román", "tramite": "trámite", "hectareas": "hectáreas",
           "anos": "años", "cedulas": "cédulas", "gravamenes": "gravámenes", "areas": "áreas", "numeros": "números",
           "matriculas": "matrículas", "vias": "vías", "rios": "ríos"}


def _plano(s):
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


_mapa = None


def _dicc():
    global _mapa
    if _mapa is None:
        m = {}
        for w in _PALABRAS:
            k = _plano(w).lower()
            if k != w.lower() and k not in _EXCLUIR:
                m[k] = w.lower()
        m.update(_FORZAR)
        try:  # nombres de municipios y departamentos (solo palabras de 4+ letras con tilde en vocal)
            import gis
            vistos_sin = set()
            cand = {}
            for l in gis._indice_lugares():
                for w in re.findall(r"[^\W\d_]+", l["nombre"]):
                    k = _plano(w.replace("ñ", "\0").replace("Ñ", "\0")).replace("\0", "ñ").lower()
                    if k == w.lower():
                        vistos_sin.add(k)
                    elif len(k) >= 4:
                        cand[k] = w.lower()
            for k, v in cand.items():
                if k not in vistos_sin and k not in m and k not in _EXCLUIR:
                    m[k] = v
        except Exception:
            pass
        _mapa = {k: v.lower() for k, v in m.items()}
    return _mapa


def _como(orig, nuevo):
    if orig.isupper() and len(orig) > 1:
        return nuevo.upper()
    if orig[:1].isupper():
        return nuevo[:1].upper() + nuevo[1:]
    return nuevo


def tildar(texto):
    """Devuelve el texto con las tildes y eñes que el diccionario reconoce sin ambigüedad."""
    if not texto or not isinstance(texto, str):
        return texto
    d = _dicc()
    return re.sub(r"[^\W\d_]+", lambda mo: _como(mo.group(0), d[mo.group(0).lower()]) if mo.group(0).lower() in d else mo.group(0), texto)
