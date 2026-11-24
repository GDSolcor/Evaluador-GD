# Acceso del equipo e integración con el pipeline de GD

8 de octubre de 2026. Propuesta para que 2 a 5 personas usen el Evaluador, también en remoto y desde el celular en campo, y para conectar la evaluación con el lead, la carpeta del proyecto en SharePoint y los entregables.

## Recomendación

**Un solo Evaluador central, al que todos entran por el navegador con una dirección HTTPS**, en dos fases:

1. **Fase 1 (2 a 3 semanas):** un PC siempre encendido en la oficina hace de servidor. Se publica con un túnel seguro, y como ese PC tiene la carpeta «Desarrollo GD» sincronizada, el Evaluador crea carpetas y guarda entregables escribiendo directamente ahí. No necesita permisos nuevos de Microsoft.
2. **Fase 2 (cuando el uso lo pida):** se pasa a un servidor en la nube y la conexión con SharePoint se hace por la API de Microsoft (Graph).

La razón: el celular en campo descarta instalar una copia en cada PC, y los 20 GB de datos (catastro IGAC 13 GB, GIS 6 GB) hacen costoso y lento repartir copias.

## Alternativas de acceso

| | A. Servidor en la oficina + túnel | B. Servidor en la nube | C. Copia en cada PC |
|---|---|---|---|
| **Celular en campo** | Sí | Sí | No |
| **Remoto** | Sí | Sí | Solo en el PC que tenga la copia |
| **Evaluaciones compartidas** | Sí, una sola base | Sí, una sola base | No, cada PC tiene las suyas |
| **Actualizaciones** | Una sola vez, en el servidor | Una sola vez | Hay que repartir cada versión |
| **SharePoint** | Escribe en la carpeta sincronizada | Requiere registrar una aplicación en Microsoft 365 | Escribe en la carpeta sincronizada de cada uno |
| **Costo mensual** | Cero o casi (el PC y la luz) | Un servidor con 30 GB de disco | Cero |
| **Depende de** | Que ese PC esté encendido y con internet | El área de TI de Solcor | Cada usuario |
| **Punto débil** | Si se va la luz en la oficina, nadie entra | Más trámite inicial | Sin celular, datos dispersos |

Para la alternativa A hay dos formas de publicar el PC sin abrir puertos del router:

- **Cloudflare Tunnel con Access:** da una dirección tipo `evaluador.solcor.co` con HTTPS y exige ingreso con el correo corporativo antes de llegar al Evaluador. Requiere que TI apunte un subdominio. Es la que recomiendo.
- **Tailscale:** red privada entre los equipos del grupo. No necesita dominio, pero cada persona instala la aplicación en su PC y su celular.

## Lo que hay que ajustar antes de abrirlo

Son los puntos A a F de `DIAGNOSTICO.md`. Los indispensables:

- Cookie de sesión `Secure` y dirección pública fija para los enlaces de correo.
- Límite a las solicitudes de recuperación de contraseña.
- Evaluaciones compartidas por proyecto (hoy cada usuario solo ve las suyas).
- Interfaz adaptada al celular: hoy funciona, pero el panel lateral y el mapa compiten por el espacio en pantallas angostas.

## El celular en campo

- **«Estoy aquí»:** un botón que centra el mapa en la ubicación del teléfono y la deja como punto del predio. Solo funciona con HTTPS, que es otra razón para el túnel.
- **Foto georreferenciada:** el Evaluador puede leer la coordenada que el iPhone guarda en la foto y ubicar el predio. Una salvedad: al subir una foto desde el navegador, iOS solo incluye la ubicación si en el selector de fotos se deja activa la opción «Ubicación»; las fotos tomadas en ese momento desde la página no la traen. Por eso el botón «Estoy aquí» es el camino más confiable y la foto el complemento.
- **Fotos del predio en la evaluación:** quedan como evidencia y se copian a la carpeta del proyecto.

## Flujo integrado: lead → evaluación → proyecto

```
1. Lead            Nombre, contacto, municipio, punto o predio, canal de origen
2. Evaluación      La que ya existe; queda ligada al lead
3. Crear proyecto  Asigna el siguiente ID, crea la carpeta y guarda los entregables
4. CRM             Escribe la fila en la hoja Registro
```

### Crear proyecto

Sigue las convenciones v02 que ya definiste:

- Toma el siguiente ID libre del CRM (hoy van en GD 0056).
- Crea `01 Pipeline\E1\GD 00NN Nombre` con las diez subcarpetas: `0 Resumen`, `1 Predial`, `2 Consultas`, `3 Jurídico`, `4 Conexión`, `5 Energía`, `6 Capex`, `7 Opex`, `8 Financiera`, `9 Comercial`.
- Nombra cada archivo como `AAAAMMDD E1-Hx descripción v01`.
- Mide la ruta antes de escribir, para no pasar el límite de 260 caracteres de Windows.

### Entregables y dónde quedan

| Entregable | Carpeta | Estado |
|---|---|---|
| Ficha técnica (Word) | `0 Resumen` | Ya existe |
| KMZ y KML del layout | `1 Predial` | Ya existe el KML; falta empaquetar el KMZ |
| Reporte de caracterización predial (Word) | `1 Predial` | Ya existe |
| Estudio de títulos preliminar (Word) | `3 Jurídico` | Ya existe |
| Plano de layout | `5 Energía` | Por desarrollar: PDF a escala y DXF para AutoCAD |
| Cotizador ajustado en cantidades | `6 Capex` | Por desarrollar: llenar `260824_Cotizador_GD_Solar.xlsx` con módulos, mesas, inversores y longitudes |
| Modelo financiero (Excel) | `8 Financiera` | Por desarrollar: exportar supuestos y flujos del modelo que ya corre el Evaluador |

### El CRM en Excel

`20260914_v10_Seguimiento_GD_CRM.xlsx` es un libro con validaciones y fórmulas. Reescribirlo completo las perdería. Ya tienes resuelto cómo hacerlo bien: `registrar_e1.py` escribe celdas puntuales dentro del archivo sin tocar el resto. Propongo reutilizar esa técnica para que «Crear proyecto» agregue la fila del lead con municipio, áreas, potencia DC y AC, PVOUT, carpeta y ruta de etapa.

Una salvedad: si alguien tiene el Excel abierto, la escritura falla o genera una copia en conflicto. El Evaluador debe detectar el bloqueo, dejar la fila en cola y reintentar.

## Plan propuesto

| Etapa | Qué incluye | Esfuerzo |
|---|---|---|
| 1 | Ajustes de seguridad, evaluaciones compartidas, interfaz para celular, botón «Estoy aquí» | 2 a 3 sesiones |
| 2 | Módulo de leads y «Crear proyecto» con carpeta y entregables existentes | 2 sesiones |
| 3 | Escritura en el CRM | 1 a 2 sesiones |
| 4 | Entregables nuevos: KMZ, plano de layout, Excel financiero, cotizador | 3 a 4 sesiones |
| 5 | Publicación: servidor de oficina y túnel | 1 sesión, más el trámite con TI |

Las etapas 1 y 2 se pueden hacer ya en este PC, sin esperar la decisión del servidor.

## Decisiones que necesito de ti

1. **Servidor:** ¿qué PC queda encendido en la oficina, o prefieres ir directo a la nube?
2. **Dominio:** ¿TI puede crear un subdominio como `evaluador.solcor.co`? Si no, vamos con Tailscale.
3. **ID del proyecto:** ¿el Evaluador asigna el siguiente GD automáticamente, o lo sigue asignando Jonatan en el CRM?
4. **Momento de crear la carpeta:** ¿al crear el lead (va a `Prospección sin registrar`) o solo cuando pasa a E1?
5. **Evaluaciones:** ¿todo el equipo ve todas, o cada quien las suyas más las que le compartan?
6. **Cotizador y modelo financiero:** ¿me autorizas a leer `260824_Cotizador_GD_Solar.xlsx` y el modelo de Lérida para diseñar la exportación?
