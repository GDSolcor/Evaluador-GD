# Evaluación de la interfaz y plan de mejora · Evaluador GD v2.2

10 de octubre de 2026. Revisión de la interfaz en computador y celular con criterio de desarrollo de producto: qué tan fácil es para alguien nuevo llegar de un predio a un resultado, y qué tan difícil es equivocarse.

## Diagnóstico en una frase

La herramienta hace mucho y lo hace bien, pero **muestra todo al mismo tiempo y con el mismo peso**. Quien ya la conoce trabaja rápido; quien llega por primera vez no sabe por dónde empezar ni qué es opcional. El trabajo de fondo no es agregar funciones: es ordenar las que hay alrededor del recorrido real del usuario.

## Lo que ya funciona bien

- **Resultado inmediato:** al cargar el predio, la evaluación corre sola. No hay que «configurar» antes de ver algo.
- **Valores de fábrica sensatos:** se puede evaluar sin tocar ningún parámetro.
- **El mapa es el centro:** layout, capas y red se leen de un vistazo, con colores por GD e inversor.
- **Textos en lenguaje del negocio:** los botones dicen lo que hacen y los avisos explican la causa.
- **Estado visible:** «sin guardar», «guardada», lead en evaluación.

## Hallazgos, por gravedad

### Altos: frenan a un usuario nuevo o permiten errores costosos

| # | Hallazgo | Por qué importa |
|---|---|---|
| 1 | El panel izquierdo tiene ocho secciones al mismo nivel, y mezcla datos de entrada, parámetros avanzados y entregables. | No comunica un orden. El usuario nuevo no sabe que solo la sección 1 es obligatoria. |
| 2 | El botón principal, «Evaluar predio», está al final del panel, después de ocho secciones. | La acción más importante es la menos visible. |
| 3 | La pantalla vacía no orienta: un mapa de Colombia y una frase bajo el mapa. | El primer minuto decide si la herramienta «se entiende». |
| 4 | «Pasar a E1» usa una ventana genérica del navegador para una acción que no se deshace y escribe en SharePoint. | Es fácil confirmar sin leer. Debe mostrar qué carpeta se crea, con qué número y en qué ruta. |
| 5 | Si la sesión vence o se recarga la página, se pierde lo que no se haya guardado. | Castiga al usuario por algo que no controla. |
| 6 | En celular se ofrece la herramienta completa de escritorio. | En campo solo se necesitan cuatro cosas: ubicarse, tomar foto, registrar el lead y anotar. |

### Medios: hacen el uso más lento o menos claro

| # | Hallazgo | Por qué importa |
|---|---|---|
| 7 | Los resultados son una sola columna larga con ocho bloques. | Para ver el modelo financiero hay que pasar por todo lo demás. |
| 8 | La barra superior tiene siete botones con el mismo peso visual. | «Guardar» no destaca cuando hay cambios pendientes. |
| 9 | Los mensajes aparecen en sitios distintos: bajo un botón, en una ventana del navegador, en una nota. | El usuario no sabe dónde mirar. |
| 10 | Los entregables son seis botones sueltos, algunos deshabilitados sin que se vea qué falta. | No se sabe qué está listo ni qué hace falta para lo demás. |
| 11 | El mapa acumula controles a la izquierda: zoom, capas, regla y tres botones de Earth. | En pantallas medianas tapan el predio. |
| 12 | Tres palabras para cosas parecidas: evaluación, proyecto y lead. | Confunde qué se guarda, qué se comparte y qué pasa a SharePoint. |
| 13 | No hay «deshacer» general. | Borrar un elemento o cambiar un rol por error obliga a rehacer. |
| 14 | Los campos numéricos aceptan cualquier valor. | Un pasillo de 0 o un DC/AC de 14 dan resultados absurdos sin aviso. |

### Bajos: pulido

| # | Hallazgo |
|---|---|
| 15 | Los iconos son emojis, que cambian de aspecto entre Windows, iPhone y Android. |
| 16 | Los textos de ayuda son grises y pequeños (12 px); cuesta leerlos al sol. |
| 17 | No todos los controles se alcanzan con el teclado ni muestran el foco. |
| 18 | Mientras se evalúa un predio grande no hay indicador de progreso. |
| 19 | Los nombres de los operadores de red no tienen leyenda de colores fuera del panel. |

## Principios para el rediseño

1. **Un recorrido, no un tablero.** La pantalla debe decir en qué paso estás y cuál sigue.
2. **Lo esencial primero, lo avanzado a un clic.** Los parámetros que casi nunca cambian no deben competir con el predio.
3. **Cada acción irreversible se confirma mostrando su efecto exacto.**
4. **Nada se pierde por accidente:** borrador automático y deshacer.
5. **El celular es otra tarea, no la misma pantalla más pequeña.**
6. **Un solo lugar para los mensajes.**

## Plan por etapas

### Etapa 1 · Orientar al usuario nuevo (1 a 2 sesiones)

| Acción | Resuelve | Estado |
|---|---|---|
| Guía interactiva paso a paso, ofrecida en la primera visita | 1, 3 | **Hecho** |
| Manual de uso dentro de la herramienta, con buscador | 3, 12 | **Hecho** |
| Pantalla de inicio con tres tarjetas: «Tengo un KMZ», «Tengo una coordenada», «Viene de un lead» | 3 | Por hacer |
| «Evaluar predio» fijo al pie del panel, siempre visible | 2 | Por hacer |
| Indicador de progreso durante la evaluación | 18 | Por hacer |

### Etapa 2 · Ordenar el panel y los resultados (2 a 3 sesiones)

| Acción | Resuelve |
|---|---|
| Agrupar el panel en cuatro pasos: **Predio → Diseño → Negocio → Entregables**, con el paso actual resaltado | 1 |
| Dejar plegados «Mesa y módulo», «Capacidad» y «Modelo financiero» bajo un título «Parámetros avanzados», con un resumen de una línea de los valores en uso | 1 |
| Resultados en pestañas: Resumen, Layout, Restricciones, Conexión, Financiero, Predios | 7 |
| Entregables como lista de verificación: cada uno con su estado («listo», «falta el CTL») y un botón «Generar todos» | 10 |
| Barra de herramientas única en el mapa, con capas, medir y Earth agrupados | 11 |

### Etapa 3 · Evitar errores y pérdidas (2 sesiones)

| Acción | Resuelve |
|---|---|
| Borrador automático en el navegador y oferta de recuperarlo al volver | 5 |
| Diálogo propio para «Pasar a E1»: número asignado, ruta completa, subcarpetas y casilla de confirmación | 4 |
| Deshacer y rehacer para elementos, roles y dibujo (`Ctrl+Z`) | 13 |
| Rangos y unidades en los campos numéricos, con aviso junto al campo | 14 |
| Mensajes unificados: avisos breves en una esquina y errores con el botón «Reportar a soporte» | 9 |
| «Guardar» resaltado cuando hay cambios pendientes | 8 |

### Etapa 4 · Modo campo para celular (2 sesiones)

| Acción | Resuelve |
|---|---|
| Pantalla de campo: mapa, «Estoy aquí», tomar foto, datos del propietario, nota de voz o texto y guardar como lead | 6 |
| Fotos del predio guardadas con el lead y copiadas a la carpeta del proyecto | 6 |
| Funcionar con señal débil: el lead se guarda en el teléfono y se envía cuando hay conexión | 6 |
| Botones de al menos 44 px y textos de ayuda con mejor contraste | 16 |

### Etapa 5 · Pulido y mantenimiento (2 a 3 sesiones)

| Acción | Resuelve |
|---|---|
| Juego de iconos propio en lugar de emojis | 15 |
| Navegación completa por teclado y foco visible | 17 |
| Vocabulario único: **lead** (prospecto), **evaluación** (análisis guardado), **proyecto** (lead en E1 con carpeta) | 12 |
| Partir `index.html` en archivos por tema, para que los cambios siguientes sean seguros | Robustez |
| Pruebas automáticas de interfaz para el recorrido principal | Robustez |

### Etapa 6 · Actualización de las bases de datos GIS (administrador) (3 a 4 sesiones)

Hoy las capas se actualizan corriendo programas desde la consola. Esta etapa lo lleva a una sección **«Datos»** del panel de administración, visible solo para el administrador o el desarrollador.

**Tablero de fuentes.** Una fila por fuente, con lo que hoy no se ve en ningún lado:

| Columna | Qué muestra |
|---|---|
| Fuente y capas | Por ejemplo «EPM · circuitos, transformadores, subestaciones» |
| Fecha de corte | Cuándo se descargó la versión en uso |
| Registros | Cuántos elementos tiene |
| Antigüedad | Aviso en ámbar a los 3 meses y en rojo a los 6 |
| Forma de actualizar | Automática, asistida o por archivo |
| Última actualización | Quién la hizo, cuándo y con qué resultado |

**Tres formas de actualizar, según la fuente:**

| Forma | Fuentes | Cómo funciona |
|---|---|---|
| **Automática** | CENS, EPM, ICANH, ANH, EDEQ | Botón «Actualizar». Corre en segundo plano con barra de avance, respeta las pausas entre consultas y se detiene sola si el portal falla. Se puede reanudar. |
| **Asistida** | EBSA, ENERCA, EMSA (visores con captcha) | El administrador entra al visor y resuelve el captcha; el Evaluador entrega el recolector listo para pegar y luego recibe el archivo. El captcha nunca se salta. |
| **Por archivo** | GIS Solcor, catastro IGAC, hidrografía IGAC, recurso solar, Celsia, plantilla del cotizador | El administrador sube el paquete o el archivo nuevo. El Evaluador lo valida antes de aceptarlo. |

**Protecciones en toda actualización:**

| Protección | Para qué |
|---|---|
| Respaldo de la versión anterior antes de reemplazar | Poder volver atrás con un clic si la nueva sale mal |
| Validación antes de publicar: estructura, número mínimo de registros, coordenadas dentro de Colombia | Que una descarga incompleta no reemplace una base buena |
| Comparación con la versión anterior: cuántos elementos entran, salen y cambian | Detectar una fuente que cambió de formato o vino recortada |
| La base nueva se arma aparte y se cambia al final | Los usuarios siguen trabajando durante la actualización |
| Bitácora de actualizaciones | Saber qué versión de cada capa respalda un informe |
| Límite de ritmo fijo por portal, sin opción de acelerarlo | Evitar bloqueos de los geoportales |

**En los informes.** Cada reporte ya cita sus fuentes; con la bitácora, citará además la fecha de corte real de cada capa usada.

**Programación (opcional).** Revisión mensual que avisa al administrador qué fuentes están desactualizadas. La descarga la sigue lanzando una persona.

**Lo que no cubre.** No agrega fuentes nuevas ni negocia accesos: Afinia sigue requiriendo usuario, y Air-e, CHEC, CEDENAR y Electrohuila siguen sin publicar datos utilizables.

## Cómo sabremos que mejoró

Tres medidas sencillas, con dos o tres personas del equipo que no conozcan la herramienta:

| Medida | Hoy (estimado) | Meta |
|---|---|---|
| Tiempo desde el ingreso hasta ver el primer layout, sin ayuda | No medido | Menos de 3 minutos |
| Personas que generan un entregable sin preguntar | No medido | 3 de 3 |
| Tickets de «no encuentro» o «no entiendo» por semana | No medido | Tendencia a cero tras la etapa 2 |

Conviene hacer la primera medición **antes** de la etapa 2, con la guía ya disponible, para tener punto de comparación.

## Orden recomendado

Etapa 1 (lo que falta) → Etapa 3 → Etapa 2 → Etapa 6 → Etapa 4 → Etapa 5.

La etapa 6 no depende de las demás y se puede adelantar en cualquier momento. La ubico después de la 2 porque no afecta al usuario del día a día; conviene adelantarla si antes hay que renovar el catastro o el GIS Solcor, que hoy tienen corte de agosto y septiembre de 2026.

La etapa 3 va antes que la 2 porque protege el trabajo del usuario y cuesta poco; la 2 es la de mayor efecto visible pero también la que más cambia la pantalla, y conviene hacerla con la medición inicial ya tomada.
