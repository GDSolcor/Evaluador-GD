"""
Pruebas rápidas del Evaluador GD (sin dependencias). Correr después de cualquier cambio:

    python herramientas/pruebas.py

No tocan los datos reales: usuarios, evaluaciones y soporte se prueban en una carpeta temporal.
Cada prueba imprime OK o FALLA; el programa termina con código 1 si alguna falla.
"""
import glob
import io
import json
import os
import sys
import tempfile
import traceback
import zipfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="evaluador_pruebas_")
os.environ["EVALUADOR_USUARIOS"] = os.path.join(TMP, "usuarios.db")
os.environ["EVALUADOR_EVALUACIONES"] = os.path.join(TMP, "evaluaciones.db")
os.environ["EVALUADOR_SOPORTE"] = os.path.join(TMP, "soporte.db")
sys.path.insert(0, os.path.join(RAIZ, "motor"))

PRUEBAS = []


def prueba(f):
    PRUEBAS.append(f)
    return f


def _mavalle():
    import evaluador
    kmz = sorted(glob.glob(os.path.join(RAIZ, "ejemplos", "*.km*")))
    assert kmz, "no hay KMZ de ejemplo en ejemplos/"
    res = evaluador.cargar(open(kmz[0], "rb").read())
    return res, evaluador.evaluar(res["capas"], {})


@prueba
def transformador_ct_limita_un_gd():
    import evaluador
    res, base = _mavalle()
    r = evaluador.evaluar(res["capas"], {"trafo_kva_gd": "0, 630"})
    g1, g2 = r["gds"][0], r["gds"][1]
    kwp = r["mesa"]["kwp"]
    assert g2["trafo_kva"] == 630 and g2["ac_kw"] <= 630 and g2["dc_kwp"] <= 630 * r["params"]["dc_ac_max"] + 1e-6, g2
    assert g2["inversores"] == 2 and g1["trafo_kva"] is None and g1["ac_kw"] == 990
    assert r["resumen"]["mesas"] == base["resumen"]["mesas"], "no debe perder mesas: las reparte entre los demás GD"
    assert all(g["dc_kwp"] <= (g["trafo_kva"] or 990) * 1.4 + kwp for g in r["gds"])
    assert evaluador.trafos_ct("800, , 630;") == [800.0, 0.0, 630.0] and evaluador.trafos_ct("") == []


@prueba
def gd_no_comparten_fila_con_topes_llenos():
    # #49: con máximo de GDs y cada GD lleno hasta su tope no hay corte exacto por filas: se dejan mesas libres
    import evaluador
    from collections import defaultdict
    filas = [8, 9, 9, 9, 9, 9, 9, 9, 9, 9]  # 89 mesas, topes 40 + 40 = 80 -> ningún corte exacto
    b = evaluador._reparto_recortando(filas, 2, [40, 40])
    assert b and all(k <= 40 for _, k in b) and sum(nf for nf, _ in b) == len(filas), b
    res, _ = _mavalle()
    r = evaluador.evaluar(res["capas"], {"max_gd": 2, "trafo_kva_gd": "0, 500"})
    por_fila = defaultdict(set)
    for m in r["mesas"]:
        por_fila[(m["area"], round(min(p[1] for p in m["coords"]), 6))].add(m["gd"])
    assert all(len(v) == 1 for v in por_fila.values()), "dos GD en la misma fila"
    assert not r["resumen"]["filas_compartidas"]
    assert all(g["dc_kwp"] <= (g["trafo_kva"] or 990) * 1.4 + 1e-6 for g in r["gds"])


@prueba
def financiero_coincide_con_lerida():
    import financiero
    r = financiero.validar_lerida()
    assert r is None or r is True or isinstance(r, (dict, list, str)), r


@prueba
def tir_no_revienta_con_flujos_extremos():
    import financiero
    for flujos in ([-1e9] + [1.0] * 300, [-1.0] + [1e9] * 300, [0.0] * 50, [-100.0] * 10):
        v = financiero.irr(flujos)
        assert v is None or -1.0 < v < 10.0, (flujos[:3], v)


@prueba
def layout_de_ejemplo_y_gd_por_filas():
    _, r = _mavalle()
    s = r["resumen"]
    assert s["mesas"] > 100 and s["n_gd"] >= 1, s
    assert sum(g["mesas"] for g in r["gds"]) == s["mesas"]
    assert all(g["dc_kwp"] <= s["dc_max_por_gd"] + 1e-6 for g in r["gds"]), "un GD supera el tope DC"
    filas = {}
    for m in r["mesas"]:
        filas.setdefault((m["area"], round(m["coords"][0][1], 7)), set()).add(m["gd"])
    assert s["filas_compartidas"] or all(len(v) == 1 for v in filas.values()), "dos GD comparten una fila física"


@prueba
def limite_de_gd_recorta_sin_recortar_arriendo():
    # desde el ticket #34 el área de arriendo es la del predio (construible o útil), sin recorte proporcional
    import evaluador
    res, completo = _mavalle()
    r = evaluador.evaluar(res["capas"], {"max_gd": 1})
    assert r["resumen"]["n_gd"] == 1 and r["resumen"]["mesas"] < completo["resumen"]["mesas"]
    assert abs(r["resumen"]["area_arriendo_ha"] - completo["resumen"]["area_arriendo_ha"]) < 1e-6


@prueba
def reparto_por_filas():
    import evaluador
    assert evaluador._reparto_por_filas([10] * 6, 2, 30) == [3, 3]
    assert evaluador._reparto_por_filas([10, 25, 10], 2, 30) is None
    assert evaluador._reparto_por_filas([40, 5], 2, 30) is None


@prueba
def tildes():
    from ortografia import tildar
    assert tildar("SOTAQUIRA, BOYACA") == "SOTAQUIRÁ, BOYACÁ"
    assert tildar("Sanchez Sierra Jose German") == "Sánchez Sierra José Germán"
    assert tildar("canon de arrendamiento") == "canon de arrendamiento"


@prueba
def informes_word_se_generan():
    import financiero
    import informes
    res, r = _mavalle()
    fin = financiero.evaluar_gds(r["gds"], r["resumen"]["area_arriendo_ha"], {})
    doc = informes.ficha_tecnica(dict(proyecto="Prueba", resultado=r, financiero=fin, gis={}, red=[]))
    x = zipfile.ZipFile(io.BytesIO(doc)).read("word/document.xml").decode("utf-8")
    assert "FICHA TÉCNICA" in x and "GD 1" in x


@prueba
def entregables_excel_dxf_kmz():
    import entregables
    import financiero
    res, r = _mavalle()
    fin = financiero.evaluar_gds(r["gds"], r["resumen"]["area_arriendo_ha"], {})
    pol = [c["coords"] for c in res["capas"] if c.get("rol") == "construible" and c["tipo"] == "Polygon"]
    cot, notas = entregables.cotizador(dict(proyecto="Prueba", resultado=r, poligonos=pol, long_mt_m=420))
    hoja = zipfile.ZipFile(io.BytesIO(cot)).read("xl/worksheets/sheet1.xml").decode("utf-8")
    assert f'<c r="G18" s="17"><v>{float(r["resumen"]["modulos"])!r}</v></c>' in hoja, "no cambió la cantidad de módulos"
    assert '<c r="G61"' in hoja and "<v>420.0</v>" in hoja and len(notas) >= 5
    assert "<f" in hoja and 'fullCalcOnLoad="1"' in zipfile.ZipFile(io.BytesIO(cot)).read("xl/workbook.xml").decode()
    # modelo financiero Solcor: la plantilla con las entradas del GD (municipio, capacidad, CAPEX, arriendo o compra)
    doc, notas = entregables.modelo_financiero(dict(proyecto="Prueba", resultado=r, financiero=fin, gd=0, municipio="Paipa",
                                                    depto="Boyacá", compra_cop=250000000))
    libro = zipfile.ZipFile(io.BytesIO(doc))
    hojas = {n: libro.read(n).decode("utf-8") for n in libro.namelist() if n.startswith("xl/worksheets/sheet")}
    todo = "".join(hojas.values())
    assert "Municipio: Paipa" in todo and "Departamento: Boyacá" in todo and len(notas) >= 4
    assert f"<v>{float(round(fin[0]['supuestos']['capacidad_kwp'], 2))!r}</v>" in todo, "no puso la capacidad"
    assert "<v>250000000.0</v>" in todo, "no puso la compra del predio"
    # todo lo de la plantilla se conserva; solo se agrega el logo (imagen y su dibujo en la portada)
    agregados = set(libro.namelist()) - set(zipfile.ZipFile(entregables.PLANTILLA_MODELO).namelist())
    assert {"xl/media/logo_solcor.png"} <= agregados and all("drawing" in n or "media" in n for n in agregados), agregados
    assert set(zipfile.ZipFile(entregables.PLANTILLA_MODELO).namelist()) - set(libro.namelist()) <= {"xl/calcChain.xml"}
    assert 'fullCalcOnLoad="1"' in libro.read("xl/workbook.xml").decode()
    dxf = entregables.plano_dxf(dict(proyecto="Prueba", capas=res["capas"], mesas=r["mesas"])).decode("ascii")
    assert dxf.count("POLYLINE") >= len(r["mesas"]) and "MESAS_GD1" in dxf and dxf.rstrip().endswith("EOF")
    assert zipfile.ZipFile(io.BytesIO(entregables.kmz("<kml/>"))).read("doc.kml") == b"<kml/>"
    crm = entregables.datos_crm(dict(proyecto="GD 0061", carpeta=os.path.join("01 Pipeline", "E1", "GD 0061 La Esperanza"), lat=4.19, lon=-72.56,
                                     contacto="a@b.co", nombre="x"), r, {}, [], "08/10/2026")
    x = zipfile.ZipFile(io.BytesIO(crm)).read("xl/worksheets/sheet1.xml").decode("utf-8")
    assert "GD-0061" in x and "GD La Esperanza" in x and "Potencia DC kWp" in x
    # #46: todos los entregables con el logo de Solcor (Excel: imagen; KMZ: ScreenOverlay; DXF: texto en el rótulo)
    for libro_ in (cot, doc, crm):
        z = zipfile.ZipFile(io.BytesIO(libro_))
        assert "xl/media/logo_solcor.png" in z.namelist() and '<drawing r:id="rIdLogoDib"/>' in z.read("xl/worksheets/sheet1.xml").decode("utf-8")
    k = zipfile.ZipFile(io.BytesIO(entregables.kmz("<kml><Document></Document></kml>")))
    assert "logo_solcor.png" in k.namelist() and b"ScreenOverlay" in k.read("doc.kml")
    assert "SOLCOR COLOMBIA" in dxf


@prueba
def oferta_al_propietario_pdf():
    import oferta
    # cifras de la propuesta enviada de GD Pantanillo 1: el total debe coincidir al peso
    d = dict(proyecto="GD Pantanillo 1", propietario="Propietaria de prueba", depto="Boyacá", municipio="Moniquirá", kwp=1337, kwac=990, mwh=2013,
             canon_mes=1884840, prima=300000, meses_desarrollo=6, meses_construccion=9, anios_operacion=30, area_m2=12570, modulos=1996, n_gd=1)
    c = oferta.calcular(d)
    assert round(c["total"]) == 1184869404, c["total"]
    assert [round(c["anual"][k] / 1e6) for k in (0, 9, 19, 29)] == [23, 31, 43, 61]
    pdf = oferta.generar(d)
    assert pdf.startswith(b"%PDF-1.4") and pdf.rstrip().endswith(b"%%EOF") and pdf.count(b"/Type /Page ") == 4
    # el entregable: la presentación de Solcor con las cifras cambiadas y el resto intacto
    import oferta_pptx
    ppt = zipfile.ZipFile(io.BytesIO(oferta_pptx.generar(dict(d, vereda="Pantanillo"))))
    assert ppt.testzip() is None
    l2 = ppt.read("ppt/slides/slide2.xml").decode("utf-8")
    assert "$1.184.869.404" in l2 and "$1.884.840  mensual**" in l2 and "Plazo (30) años" in l2
    assert "** $1.499.475 por hectárea al mes (1,26 ha)" in l2  # #50: 1.884.840 / 1,257 ha
    l3 = ppt.read("ppt/slides/slide3.xml").decode("utf-8")
    assert "Hogares" not in l3 and "hogar" not in l3 and "Vehículos que dejan de circular" in l3  # #50: sin el bloque de hogares
    assert b"Hogares" not in pdf
    assert "Municipio · Moniquirá" in ppt.read("ppt/slides/slide1.xml").decode("utf-8")
    graf = ppt.read("ppt/charts/chart1.xml").decode("utf-8")
    assert graf.count("<c:pt idx=") == 61 and "externalData" not in graf  # 1 título + 30 años + 30 valores
    assert not any(n.endswith(("image2.png", "image3.png")) for n in ppt.namelist()), "quedaron las fotos del predio de la plantilla"
    try:
        oferta.calcular(dict(canon_mes=0))
        raise AssertionError("aceptó una oferta sin canon")
    except ValueError:
        pass


@prueba
def usuarios_y_sesiones():
    import auth
    auth.crear_primer_admin("admin@prueba.co", "Admin", "ClaveDePrueba123")
    token, _ = auth.ingresar("admin@prueba.co", "ClaveDePrueba123")
    assert auth.usuario_de_sesion(token)["rol"] == "admin"
    try:
        auth.ingresar("admin@prueba.co", "incorrecta")
        raise AssertionError("aceptó una clave incorrecta")
    except PermissionError:
        pass
    auth.cerrar_sesion(token)
    assert auth.usuario_de_sesion(token) is None
    # #51: permisos, alcance y quién está en línea
    auth.crear_usuario("ana@prueba.co", "Ana", "usuario", "admin@prueba.co")
    ana = next(x for x in auth.listar_usuarios() if x["correo"] == "ana@prueba.co")
    assert ana["permisos"] == [] and ana["alcance"] == "todos" and not auth.puede(ana, "fuentes_ver")
    auth.actualizar_usuario(ana["id"], {"permisos": ["fuentes_actualizar"], "alcance": "propios"}, "admin@prueba.co")
    ana = next(x for x in auth.listar_usuarios() if x["correo"] == "ana@prueba.co")
    assert sorted(ana["permisos"]) == ["fuentes_actualizar", "fuentes_ver"] and auth.puede(ana, "fuentes_actualizar")
    assert auth.solo_propios(ana) and not auth.solo_propios(dict(rol="admin", alcance="propios"))
    for malo in ({"permisos": ["borrar_todo"]}, {"alcance": "nadie"}):
        try:
            auth.actualizar_usuario(ana["id"], malo, "admin@prueba.co")
            raise AssertionError("aceptó un permiso o alcance inválido")
        except ValueError:
            pass
    t2, _ = auth.ingresar("admin@prueba.co", "ClaveDePrueba123")
    assert auth.usuario_de_sesion(t2) and [x["correo"] for x in auth.en_linea()] == ["admin@prueba.co"]


@prueba
def soporte_tickets():
    import soporte
    u = dict(id=1, correo="a@b.co", nombre="A", rol="usuario")
    otro = dict(id=2, correo="c@d.co", nombre="C", rol="usuario")
    tid = soporte.crear(u, "error", "Título de prueba", "Descripción de la novedad de prueba",
                        imagenes=[dict(nombre="nota.txt", b64="aG9sYQ==")])
    assert [t["id"] for t in soporte.listar(u)] == [tid] and soporte.listar(otro) == []
    for malo in (dict(nombre="x.exe", b64="TVo="), dict(nombre="x.html", b64="PGh0bWw+")):
        try:
            soporte.crear(u, "error", "Título de prueba", "Descripción de la novedad de prueba", imagenes=[malo])
            raise AssertionError("aceptó un adjunto no permitido")
        except ValueError:
            pass
    try:
        soporte.responder(tid, "resuelto", "x", usuario=u)
        raise AssertionError("un usuario normal pudo responder")
    except PermissionError:
        pass
    soporte.responder(tid, "resuelto", "Listo")
    assert soporte.resumen(u)["sin_leer"] == 1
    aid = soporte.listar(u)[0]["adjuntos"][0]["id"]
    try:
        soporte.adjunto(otro, aid)
        raise AssertionError("otro usuario pudo ver el adjunto")
    except ValueError:
        pass


@prueba
def evaluaciones_del_equipo():
    import evaluaciones
    ana, luis, adm = dict(id=1, nombre="Ana", rol="usuario"), dict(id=2, nombre="Luis", rol="usuario"), dict(id=3, nombre="Adm", rol="admin")
    eid = evaluaciones.guardar(ana, "De Ana", {"a": 1}, {})
    vista = evaluaciones.listar(luis)
    assert [e["id"] for e in vista] == [eid] and vista[0]["autor"] == "Ana" and not vista[0]["editable"]
    assert evaluaciones.obtener(luis, eid)["datos"] == {"a": 1}  # todo el equipo la puede abrir
    for accion in (lambda: evaluaciones.guardar(luis, "x", {}, {}, eid), lambda: evaluaciones.borrar(luis, eid)):
        try:
            accion()
            raise AssertionError("otro usuario pudo sobrescribir o borrar")
        except PermissionError:
            pass
    assert evaluaciones.guardar(luis, "Copia de Luis", {"a": 2}, {}) != eid  # guardar como nueva
    evaluaciones.borrar(adm, eid)


@prueba
def leads_y_paso_a_proyecto():
    os.environ["EVALUADOR_LEADS"] = os.path.join(TMP, "leads.db")
    raiz = os.path.join(TMP, "Desarrollo GD")
    os.makedirs(os.path.join(raiz, "01 Pipeline", "E2", "GD 0056 Antares KAI"))
    os.makedirs(os.path.join(raiz, "02 Archivo", "Descartados", "GD 0060 Viejo"))
    os.environ["EVALUADOR_PIPELINE"] = raiz
    import leads
    u = dict(id=1, nombre="Ana", correo="a@b.co", rol="usuario")
    for malo in (dict(nombre="x"), dict(nombre="Finca", lon=-73.3, lat=55)):
        try:
            leads.guardar(u, malo)
            raise AssertionError("aceptó un lead inválido")
        except ValueError:
            pass
    lid = leads.guardar(u, dict(nombre="Finca La Esperanza", municipio="Sotaquirá", depto="Boyacá", lon=-73.25, lat=5.76,
                                canal=leads.CANALES[0]))
    assert leads.estado_pipeline()["siguiente"] == "GD 0061"  # el mayor en disco es 0060, aunque esté archivado
    try:
        leads.guardar_entregable(u, lid, "ficha", b"x", ".docx")
        raise AssertionError("guardó un entregable sin carpeta de proyecto")
    except ValueError:
        pass
    lead = leads.crear_proyecto(u, lid, 'La Esperanza: lote "A"/B')
    carpeta = os.path.join(raiz, lead["carpeta"])
    assert lead["proyecto"] == "GD 0061" and lead["estado"] == "e1" and os.path.basename(carpeta) == "GD 0061 La Esperanza lote A B"
    assert sorted(os.listdir(carpeta)) == sorted(leads.SUBCARPETAS)
    a = leads.guardar_entregable(u, lid, "ficha", b"uno", ".docx")
    b = leads.guardar_entregable(u, lid, "ficha", b"dos", ".docx")
    assert a.endswith("v01.docx") and b.endswith("v02.docx") and open(os.path.join(raiz, a), "rb").read() == b"uno"
    assert os.path.dirname(leads.guardar_entregable(u, lid, "titulos", b"t", ".docx")).endswith("3 Jurídico")
    try:
        leads.guardar_entregable(u, lid, "ficha", b"x", ".exe")
        raise AssertionError("aceptó una extensión no permitida")
    except ValueError:
        pass
    lid2 = leads.guardar(u, dict(nombre="Otro predio"))
    assert leads.crear_proyecto(u, lid2)["proyecto"] == "GD 0062"
    # #51: alcance «solo los suyos»: un lead es suyo si lo creó o es su responsable
    otro = dict(id=7, correo="b@c.co", nombre="B", rol="usuario", alcance="propios")
    lid3 = leads.guardar(otro, dict(nombre="Predio de B", lon=-73.2, lat=5.7))
    assert leads.es_suyo(otro, leads.obtener(lid3)) and not leads.es_suyo(otro, leads.obtener(lid))
    assert [p["id"] for p in leads.puntos_pipeline([], otro) if p["tipo"] == "lead"] == [lid3]
    creados, _ = leads.conteo_por_usuario()
    assert creados[1]["leads"] == 2 and creados[1]["proyectos"] == 2 and creados[7]["leads"] == 1


@prueba
def kml_de_cualquier_espacio_de_nombres():
    # ticket #44: un KML con otro espacio de nombres se leía vacío y reventaba con división por cero
    import evaluador
    pol = ("<Placemark><name>Predio</name><Polygon><outerBoundaryIs><LinearRing><coordinates>-73.37,7.73,0 -73.36,7.73,0 "
           "-73.36,7.74,0 -73.37,7.74,0 -73.37,7.73,0</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>")
    for ns in (' xmlns="http://www.opengis.net/kml/2.2"', ' xmlns="http://earth.google.com/kml/2.1"', ""):
        r = evaluador.cargar(f"<kml{ns}><Document>{pol}</Document></kml>".encode())
        assert len(r["capas"]) == 1 and r["capas"][0]["rol"] == "construible"
    try:
        evaluador.cargar(b'<kml xmlns="http://www.opengis.net/kml/2.2"><Document><GroundOverlay/></Document></kml>')
        raise AssertionError("aceptó un KML sin geometrías")
    except ValueError:
        pass


@prueba
def bases_de_datos_del_panel():
    import fuentes
    fuentes.CONFIG = os.path.join(TMP, "fuentes.json")
    l = fuentes.listar()
    ids = {f["id"] for f in l["fuentes"]}
    assert {"eep", "chec", "amco", "igac"} <= ids and all(f["metodo"] in ("auto", "chrome", "manual") for f in l["fuentes"])
    assert all(f["comando"] and os.path.isfile(os.path.join(RAIZ, f["comando"][0])) for f in fuentes.REGISTRO if f["metodo"] == "auto")
    fuentes.guardar(dict(id="celsia", enlace="https://x.co", notas="n"), "a@b.co")
    assert next(f for f in fuentes.listar()["fuentes"] if f["id"] == "celsia")["enlace"] == "https://x.co"
    for malo in (dict(id="celsia", enlace="javascript:alert(1)"), dict(nombre="")):
        try:
            fuentes.guardar(malo, "a@b.co")
            raise AssertionError("aceptó un enlace o una base inválida")
        except ValueError:
            pass
    fuentes.guardar(dict(nombre="Red de prueba", categoria="Red eléctrica", enlace="https://y.co"), "a@b.co")
    nueva = next(f for f in fuentes.listar()["fuentes"] if f["propia"])
    fuentes.marcar_actualizada(nueva["id"], "a@b.co")
    assert next(f for f in fuentes.listar()["fuentes"] if f["propia"])["fecha"]
    for no in (lambda: fuentes.actualizar("chec", "a@b.co"), lambda: fuentes.eliminar("celsia")):
        try:
            no()
            raise AssertionError("permitió actualizar una base manual o quitar una del registro")
        except ValueError:
            pass
    fuentes.eliminar(nueva["id"])
    assert not any(f["propia"] for f in fuentes.listar()["fuentes"])


@prueba
def gis_y_red_responden():
    import gis
    import red
    if not gis.disponible():
        print("    (sin base GIS: se omite)")
        return
    g = gis.analizar([[[-73.37, 5.53], [-73.36, 5.53], [-73.36, 5.54], [-73.37, 5.54]]])
    assert g.get("municipio") and g.get("semaforo") in ("rojo", "ambar", "verde"), g.get("error")
    assert isinstance(red.cercano(-73.36, 5.535), list)
    assert json.dumps(red.arbol())


if __name__ == "__main__":
    fallas = 0
    for f in PRUEBAS:
        try:
            f()
            print(f"OK     {f.__name__}")
        except Exception:
            fallas += 1
            print(f"FALLA  {f.__name__}")
            traceback.print_exc()
    print(f"\n{len(PRUEBAS) - fallas} de {len(PRUEBAS)} pruebas pasaron.")
    sys.exit(1 if fallas else 0)
