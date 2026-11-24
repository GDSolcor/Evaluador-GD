# Evaluador GD · Solcor — v2

> Arquitectura, flujo de información, fuentes, referencias y despliegue en SharePoint: ver [ARQUITECTURA.md](ARQUITECTURA.md).
> La v1 quedó congelada en `Downloads/Personal/Evaluador GD v1` (se abre con su propio `.bat`, puerto 8764).

Herramienta local: predio (KMZ, dibujo, predio catastral o CTL) → layout automático → capacidad por
bloque GD → arriendo y modelo financiero → estudio de títulos y reporte predial en Word.

## Cómo abrirlo
Doble clic en **`Abrir Evaluador GD.bat`**. Se abre en el navegador (http://localhost:8765).
Cierra la ventana negra para apagarlo. Solo necesita Python (o una carpeta `python` portátil junto al
`.bat`); no instala nada. El mapa satelital necesita internet.

## Novedades de la v2
- **Paneles deseados** y **máximo de GDs** (sección 4): el layout se recorta a mesas enteras y se
  queda con las más cercanas al CT / punto de conexión (o al centro del área si no hay CT).
- **Módulo desde la ficha técnica** (sección 3, "＋ Ficha"): se extraen potencia(s), dimensiones, peso,
  eficiencia y celdas; tras revisarlos queda en la lista desplegable.
- **Configuración de la mesa**: filas × columnas y orientación **vertical (retrato, "V")** u
  **horizontal (paisaje, "H")**, con esquema y dimensiones de la mesa.
- **Inversores** como elemento del proyecto, con huella máxima de 4 m².
- **Marca Solcor** en la interfaz, el ingreso y los informes.
- **Sección 8 · Propietario, CTL y entregables**: carga el CTL (PDF de la SNR) y el nombre/cédula de
  quien ofrece el predio; la herramienta lee el folio, reconstruye titulares y cuotas, ubica el predio
  en el catastro IGAC por número predial ("Evaluar este predio") y genera:
  - **Estudio de Títulos Preliminar** (Word, formato de Monsalve 083-4434).
  - **Reporte de Caracterización Predial** (Word, formato de Monsalve), con imagen satelital del predio
    y del layout, catastro, restricciones GIS, hidrografía y red.

## Cómo usarlo
1. Arrastra el KMZ/KML del predio (o usa un archivo de la carpeta `ejemplos`).
2. Revisa en **Capas** que la detección automática acertó:
   - **Construible**: polígonos con "bloque", "área útil"… (si no hay, "cerramiento", "predio", "lote"; si no, el más grande).
   - **Exclusión**: nombres con franja, retiro, ronda, vía, árbol, servidumbre…
   - **Exclusión (línea)**: canales, redes y ríos, con el buffer configurable.
   - **Mesa existente**: polígonos pequeños tipo `M001` / `R025`; se usan para comparar con un layout ya hecho.
3. Ajusta la mesa (hay dos presets: Mavalle LONGi 650 2V×14 y estándar SOLCOR Jinko 730 2V×10), el pasillo y el retiro.
4. Ingresa la tarifa de arriendo (COP/ha/mes).
5. **Exportar layout KML** para abrir el resultado en Google Earth.

## Calibración (Mavalle v04)
Con el preset Mavalle (pasillo 1,9 m, retiro 0, vías excluidas) da **213 mesas** contra **219** del
layout de detalle v04 (−2,7 %), repartidas en 3 GD de 71 mesas (v04: 3 × 72).
Es una estimación rápida del potencial del terreno; no reemplaza el layout de detalle
(no ubica CT, inversores ni zanjas de cableado).

Desde el 1-oct-2026, en áreas de menos de 30 ha cada fila de mesas se acomoda por separado dentro del
polígono (antes todas las filas compartían las mismas columnas y quedaban rincones vacíos donde una
mesa cabía corrida unos metros). Con ese acomodo Mavalle da **232 mesas** (+5,9 % sobre las 219 del
v04, que además reserva espacio para CT, inversores y vías internas: ubícalos como elementos para
descontarlos).

## Modelo financiero (réplica del modelo Solcor)
`motor/financiero.py` reproduce el modelo Excel de Solcor: motor mensual a 31 años, CAPEX por
hitos, deuda IBR + spread girada con los hitos (gracia en obra con interés capitalizado), OPEX
regulatorio (CND-SIC-LAC, representación, CGM, ICA, 4×1000), impuestos con DER 50 % y depreciación
acelerada de la Ley 1715, y las cuatro TIR de la Portada (proyecto, equity pre-tax, SAS, óptimo).

Validación (las TIR coinciden con el Excel a 0,000 pb):
- `20260923 Modelo Financiero Lérida 0.1 km.xlsx`: 17,64 % / 22,84 % / 21,27 % / 52,96 %
- `20260909 Modelo Financiero Monsalve 1.xlsx`: 16,94 % / 21,31 % / 19,74 % / 50,13 %

Para revalidar: `python motor/financiero.py`.
Los supuestos por defecto (curva PPA, IBR, IPC, OPEX, impuestos) están en
`motor/supuestos_solcor.json`; en la interfaz se editan EPC, RTB, O&M, deuda y fecha de firma.
Cada bloque GD corre su propio modelo (como en Solcor, un modelo por GD).

## Capas base GIS Solcor
Restauradas desde `SharePoint > Desarrollo GD > 00 Área GD > 03 Herramientas > Georeferenciacion >
GIS_SOLCOR_Respaldo` (106 partes, SHA-256 verificado) en `datos/gis/GIS_SOLCOR/`:
736.672 geometrías en 13 capas + rásteres de pendiente (~30 m) y PVOUT Solargis.

- **En el mapa**: botón de capas (arriba a la derecha) → activa las que necesites. Cada capa se
  carga solo en la vista actual y a partir de cierto zoom, para no saturar el navegador.
- **Análisis del predio** (automático al cargar el KMZ): cruza el polígono construible con PNN,
  RUNAP, áreas CAR, ANT, prioridades de conservación, títulos ANM y proyectos ANLA; da municipio,
  semáforo, red eléctrica más cercana (ENEL/ESSA/EBSA), pendiente media/P90 y PVOUT.
  El botón "Usar PVOUT en el modelo" pasa el PVOUT a la producción específica del modelo financiero.
- Sin dependencias: `motor/gis.py` lee el `.npy` por ventanas y el GeoTIFF con `zlib`
  (el `query_engine.py` original del paquete requiere numpy y Pillow).
- Cobertura: la red eléctrica solo cubre ENEL, ESSA y EBSA; la pendiente, 4 zonas del centro del país.

## Estructura
- `servidor.py`: servidor local (librería estándar).
- `motor/evaluador.py`: parseo, clasificación de capas, grilla de mesas, capacidad y arriendo.
- `motor/financiero.py` + `motor/supuestos_solcor.json`: modelo financiero por GD.
- `motor/gis.py`: capas base y análisis de restricciones (lee `datos/gis/GIS_SOLCOR`).
- `motor/layout_engine.py`: copia del motor de la skill `layout-preliminar` (proyección y geometría).
- `web/index.html`: interfaz (Leaflet).

## Novedades de la v2.1 (28-sep-2026)
- **Elementos por GD**: con n GDs, cada uno tiene su punto de conexión, cuarto eléctrico + CT, línea MT e
  inversores (AC por GD ÷ potencia del inversor, sección 4). Selector "Asignar a GD", lista de lo que falta
  por GD (clic en lo que falta para ubicarlo) y "⊡ Ubicar inversores" junto al CT del GD activo.
- **Editar vértices** (✎ en la sección 7 o en los polígonos dibujados): arrastrar, agregar (● punto medio)
  o quitar (clic derecho) vértices; al terminar se recalcula el layout.
- **Medir distancias** (📏 en el mapa): clic por punto, doble clic para terminar; muestra tramos, total y,
  si hay 3 o más puntos, el área y perímetro del recorrido cerrado. Esc borra.
- **Red eléctrica**: ESSA completa (geoportal CREG 174) y circuitos EBSA por zonas (visor GD-Connect);
  las capas o tramos repetidos se ocultan (`motor/red_duplicados.py`).
- **Estudio de títulos**: la verificación del oferente se hace al generar el Word con los datos del formulario.

## Red de OR (29-sep-2026)
- **EBSA**: circuitos GD-Connect por zonas (visor con reCAPTCHA que resuelve el usuario; recolector en el
  navegador + `herramientas/importar_ebsa.py <archivo>`). Semillas por municipio:
  `herramientas/semillas_ebsa.py <códigos DANE> --salida <archivo>`.
- **CENS** (Norte de Santander, sur del Cesar y de Bolívar) y **EPM** (Antioquia): red MT real por circuito,
  transformadores (CENS con disponibilidad AGPE) y subestaciones, desde servicios ArcGIS públicos:
  `python herramientas/descargar_red_arcgis.py cens|epm` y luego `python motor/red_complementaria.py`.
  No se bajan capas de clientes ni propietarios (datos personales).
- **EDEQ** (Quindío): transformadores con circuito y disponibilidad AGPE del mapa de disponibilidad público
  (`python herramientas/descargar_red_edeq.py`, una consulta por transformador, lenta y reanudable);
  el trazado de sus circuitos es estimado, como en ESSA.

## Arqueología (ICANH) e hidrocarburos (ANH) (29-sep-2026)
`python herramientas/descargar_capas_arcgis.py [icanh|anh]` → `datos/capas_complementarias.db` (se une a las capas GIS).
- **ICANH** (grupo "Arqueología"): áreas y parques arqueológicos protegidos (restricción), zonificación de planes de
  manejo municipales (revisar), hallazgos positivos de PAP (revisar), sitios arqueológicos a menos de 300 m (revisar) y
  programas de arqueología preventiva previos (información).
- **ANH** (grupo "Superposición"): mapa de tierras (revisar solo las áreas asignadas: exploración, evaluación,
  producción; las reservadas, disponibles y el basamento cristalino son información), pozos a menos de 200 m (revisar) y
  líneas sísmicas 2D que cruzan el predio (información).
- El reporte predial las incluye en la tabla de entidades restrictivas (antes eran consulta manual).

## Siguientes pasos sugeridos
- Exportar/importar evaluaciones como archivo para compartirlas por SharePoint.
- Servidor central (un PC o VM) si el equipo necesita un solo login y evaluaciones compartidas.
- Orientación este-oeste (preset carpa) y pendiente del terreno para el pasillo.

## Soporte técnico (tickets)

El botón **Soporte** de la barra superior abre el formulario de tickets: tipo (error, ajuste, mejora, duda),
título, descripción y hasta 5 capturas (arrastrar, buscar o pegar con Ctrl+V). La herramienta adjunta sola
el contexto: versión, evaluación abierta, municipio, posición del mapa y el último error del servidor.

- **Mis tickets** muestra el estado y la respuesta de cada uno; el botón marca las respuestas sin leer.
- El administrador ve **Todos** y puede cambiar el estado y responder desde ahí.
- Barrido diario: `python herramientas\barrido_soporte.py` escribe `datos\soporte_barrido.md` con los tickets
  abiertos (errores primero) y las rutas de sus imágenes. Para responder desde consola:
  `python herramientas\barrido_soporte.py responder 12 resuelto "Texto de la respuesta"`.
- Datos: `datos\soporte.db` e imágenes en `datos\soporte_adjuntos\` (módulo `motor\soporte.py`).

## Cambios del barrido del 8-oct-2026

- Panel de capas: casilla por grupo para encender o apagar todas sus capas; nombres de capitales con la capa Municipios; mapa de calor PVOUT Solargis (`/api/gis/pvout.png`, se guarda en `datos\cache_pvout.png`).
- Produccion: la casilla "Tomar el PVOUT Solargis del sitio" usa el PVOUT del predio en evaluaciones nuevas.
- Layout: dos GD no comparten fila fisica (`evaluador._reparto_por_filas`).
- Informes Word: ficha rapida del estudio de titulos a dos columnas, tildes restauradas (`motor\ortografia.py`) y nueva Ficha tecnica del proyecto (`/api/informe/ficha`).
- Mapa: botones para abrir la vista en Google Earth (KML para la app de escritorio), Earth web y Street View.
- Soporte: los tickets aceptan archivos (PDF, KMZ/KML, Word, Excel, CSV, TXT, ZIP, DWG/DXF), ademas de imagenes.

## Versión 2.2 (8-oct-2026)

Autor y desarrollador: Manuel Antonio Bayona Cely. Ver `LICENCIA.md`.

### Leads y paso a proyecto

Botón **Leads** en la barra superior (módulo `motor\leads.py`, base `datos\leads.db`):

1. **Registrar el lead:** nombre, propietario, contacto, municipio, coordenada (escrita, del mapa o «Aquí» con el GPS del celular), canal y notas.
2. **Evaluar:** carga el lead en el mapa; la evaluación que se guarde queda ligada a él.
3. **Pasar a E1 · crear proyecto:** asigna el siguiente ID leyendo las carpetas `GD NNNN` de `01 Pipeline` y `02 Archivo`, y crea `01 Pipeline\E1\GD 00NN Nombre` con las subcarpetas `0 Resumen` a `9 Comercial`.
4. **Exportar entregables a la carpeta:** guarda el KML, la ficha técnica, el reporte predial y el estudio de títulos en su subcarpeta, con el nombre `AAAAMMDD E1 descripción v01`. No pisa versiones anteriores.

La carpeta «Desarrollo GD» se busca en `EVALUADOR_PIPELINE`, en `datos\config_pipeline.json` (`{"raiz": "..."}`) o en la carpeta sincronizada del usuario de Windows.

### Entregables nuevos (`motor\entregables.py`, `motor\xlsx.py`)

| Entregable | Botón | Carpeta del proyecto |
|---|---|---|
| Cotizador ajustado en cantidades (Excel) | Informes → Cotizador | `6 Capex` |
| Modelo financiero (Excel, valores sin fórmulas) | Informes → Modelo financiero | `8 Financiera` |
| Plano del layout (DXF en CTM12, una capa por GD) | Informes → Plano | `5 Energía` |
| KMZ del layout | Leads → Exportar entregables | `1 Predial` |
| Datos para el CRM (Excel con los campos de la hoja Registro) | Leads → Exportar entregables | `0 Resumen` |

El cotizador parte de `datos\plantillas\cotizador_gd.xlsx` (cotizador de GD Monsalve, agosto de 2026): solo cambia cantidades; precios, IVA y márgenes son los de la plantilla. Para actualizar precios, reemplazar ese archivo conservando las mismas filas.

### Equipo

- Todo el equipo ve y abre todas las evaluaciones y leads. Solo el autor o un administrador puede sobrescribir o borrar una evaluación; los demás guardan una copia propia.
- Solo el administrador o quien lo cargó puede borrar un módulo del catálogo.

### Celular

En pantallas angostas el mapa ocupa la pantalla y el panel de datos se abre con el botón ☰. En «Coordenada y dibujo»: **Estoy aquí** (ubicación del dispositivo; requiere HTTPS fuera de este PC) y **Desde una foto** (lee la coordenada GPS del EXIF de un JPEG).

### Publicación con HTTPS

Al publicar la herramienta detrás de un túnel o proxy HTTPS, definir la dirección pública para que los enlaces de correo no dependan de la petición y la cookie de sesión salga marcada `Secure`:

```bash
setx EVALUADOR_URL_PUBLICA "https://evaluador.ejemplo.co"
```

### Pruebas

```bash
python herramientas\pruebas.py
```
