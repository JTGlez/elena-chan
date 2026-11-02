# Elena: avatar de conversación para CoreS3

Estado: **aprobado por el usuario** el 2026-11-01. Fecha: 2026-11-01.

Este documento especifica la primera integración del avatar. No autoriza una
escritura al robot ni constituye evidencia de funcionamiento en hardware.

## 1. Objetivo y decisiones de la conversación

Sustituir las caritas de Xiaozhi por Elena y hacer visible cuándo escucha,
espera una respuesta y habla. El retrato debe ocupar el lienzo completo de
320 × 240, conservando el encuadre del kit v3-platinum, igual al de v2.1-fit.

El usuario eligió la opción 2: un controlador de avatar sobre el motor gráfico
existente. También indicó que no le interesan los subtítulos. Por tanto, esta
variante no mostrará transcripciones del usuario ni texto de las respuestas,
incluidas las notificaciones habladas.

Las decisiones siguientes fueron propuestas técnicas del autor y quedaron
adoptadas con la aprobación del documento: indicadores mínimos de batería/conexión; avisos
operativos legibles; temporizadores de 5 y 30 segundos; presupuestos de memoria
y respuesta visual; y nombre de la nueva placa. Se distinguen de las
preferencias expresadas inicialmente por el usuario.

### Alcance de la primera entrega

- Los 12 GIFs ligeros, sus equivalencias de emociones y un respaldo estático de Elena.
- Controlador de estado visual y una variante de firmware propia de Elena.
- Animación de hablar en bucle, con final coordinado con la reproducción.
- Presentación sin subtítulos, conservación de las funciones existentes y pruebas.
- Recursos, configuración y herramientas suficientes para reproducir la imagen.

### Fuera de esta entrega

- Boca dirigida por amplitud, reconocimiento de fonemas o sincronización de visemas.
- Nuevos movimientos de servos, sensores de caricias, gestos físicos o coreografías.
- Nuevas herramientas MCP, pantallas de preferencias o selector de personajes.
- Sueño automático por inactividad, avisos de correo u otras automatizaciones.
- Cambios de STT, LLM, TTS, protocolos de red o personalidad de Hermes.
- Redibujar el kit o implementar el firmware privado de Nous-Girl.

No se debe conseguir la animación a costa de introducir cortes de audio,
bloquear el bucle principal, ocultar instrucciones necesarias de configuración
o modificar el comportamiento de otras placas.

## 2. Alternativas consideradas

| Alternativa | Ventaja | Coste o límite | Decisión |
|---|---|---|---|
| Sustituir solamente los recursos | Menor cambio; aprovecha la selección actual de emociones | Escuchar sigue seleccionando `neutral`; no coordina por sí sola espera y habla | No elegida |
| Controlador de avatar con LVGL y GIFs existentes | Estados visuales coherentes; conserva el motor y las integraciones actuales | Requiere eventos semánticos, variante de placa y validación de concurrencia | Elegida por el usuario |
| Integración completa de visemas desde el inicio | Boca ligada a la voz y posibilidad de mantener distintas caras al hablar | Requiere nuevas señales de audio, composición de parches y más validación visual | Aplazada como posible segunda etapa |

## 3. Base comprobada y límites de la evidencia

Firmware analizado: `a61ab80ab87b1aebf22406c97f02bddfc55198da`, basado en
Xiaozhi `4632dc5`; la diferencia entre ambos es el seguimiento de
`dependencies.lock`. Servidor de referencia de la combinación actual:
`966200ebc9e0f83fedddda23d63fda1ae7a2aea2`. Se mantienen ESP-IDF v6.1 y los locks.

Se consultaron con Graphify los grafos aislados de Xiaozhi `4632dc5` y
Nous-Girl. Las relaciones se contrastaron con estos archivos del checkout:

| Archivo | Hecho relevante |
|---|---|
| `firmware/main/boards/m5stack/core-s3/m5stack_core_s3.cc` | Inicializa el LCD y crea `SpiLcdDisplay` de 320 × 240 |
| `firmware/main/assets.cc` | Lee `emoji_collection` de `index.json` y registra recursos por nombre |
| `firmware/main/display/lcd_display.cc` | Reproduce GIFs; busca iconos de fuentes si falta una emoción; superpone barras y texto |
| `firmware/main/display/lvgl_display/emoji_collection.cc` | Busca coincidencias exactas; no interpreta `_emote_aliases.json` |
| `firmware/main/application.cc` | Recibe `stt`, `llm.emotion` y `tts`; gestiona estados, alertas e interrupciones |
| `firmware/main/audio/audio_service.cc` | Expone `IsPlaybackIdle()` y notifica el drenado de reproducción |
| `firmware/scripts/build_default_assets.py` | Empaqueta fuentes, modelos de voz e imágenes; copiar archivos extra no los registra como emociones |
| `firmware/partitions/v2/16m.csv` | Reserva 8 MiB a los recursos |

El enum actual de estados del dispositivo no tiene un estado `thinking`.
La espera será un estado del controlador visual, sin alterar la máquina de
estados de audio/conexión. No se inferirá desde textos traducidos de la interfaz.

Origen del kit: `elena-chan-avatar-kit-v3-platinum/`. Sustituye a v2.1-fit, que
queda obsoleto: cambia el color del cabello y conserva poses, encuadre y nombres.
Actualmente esa carpeta no está seguida por Git. El kit es material de origen, no
un módulo: cada pieza entra al fork solo cuando una entrega la necesita, y el kit
completo no se versiona. La integración debe incorporar los recursos necesarios
al fork para que una clonación limpia pueda compilar; no puede depender de que
esa carpeta siga presente en la máquina.

Comprobaciones realizadas durante el análisis. El tamaño total y los cuadros por
GIF se midieron en v3; el resto se midió en v2.1-fit y debe repetirse con v3 al
integrar los recursos:

- 12 GIFs, 34 cuadros en total por vuelta (2 o 3 por archivo), 320 × 240, opacos,
  bucle infinito.
- Tamaño total: 795.710 bytes, aproximadamente 777,1 KiB; coincide con el manifiesto.
- Decodificador C del firmware ejecutado en el ordenador con adaptadores de LVGL:
  68 cuadros por configuración, dos vueltas de cada GIF, con caché LZW activada
  y desactivada. Ambas configuraciones coincidieron píxel a píxel con Pillow.
- El decodificador reserva 384.000 bytes para lienzo e índices de un GIF de este
  tamaño, además de estructura, tabla LZW y recursos de LVGL. No es una medición
  del consumo completo en el ESP32.
- Los 29 parches de boca suman 194.590 bytes RGB565 y usan una caja de
  `(188, 144, 61, 55)`. No se incorporan al binario de esta entrega.

Las animaciones ligeras contienen sobre todo variaciones de símbolos y detalles.
`look_left`, `look_right`, `shake` y `email` son representaciones estilizadas;
sus nombres no garantizan movimientos literales de ojos o cabeza. La reducción
de escala tampoco recupera partes ya recortadas en la ilustración fuente.

No se ha probado este avatar en el robot. El rendimiento, color, encuadre y
ausencia de interferencia con audio siguen pendientes de validación física.

## 4. Presentación

El lienzo del avatar será 320 × 240, sin estirar, recortar ni volver a reducir
las imágenes. Se conserva la figura de 280 × 210 situada en `(20, 30)` y su
fondo crema. No habrá franja inferior ni reloj o texto de conversación permanente.

Propuesta: mostrar batería, silencio y conexión mediante los indicadores
existentes en una capa pequeña y transparente en la parte superior. Deben
acomodarse sin tapar ojos ni boca; el margen del kit no sustituye esta revisión.

«Sin subtítulos» no elimina las instrucciones para activar/configurar el robot,
el progreso de actualización, los errores ni la advertencia de batería baja.
Esos avisos pueden superponerse o sustituir temporalmente al avatar, con el
texto mínimo necesario. Desaparecen cuando su dueño los resuelve o cancela;
no se establece una expiración genérica que pueda borrar un error persistente.

Se conservan las vistas previas de imágenes ya soportadas: una vista previa
explícita suspende el GIF y, al terminar, restaura el estado visual vigente.
El uso de una vista previa no habilita subtítulos.

## 5. Controlador visual

`AvatarController` mantendrá estado de conversación, emoción normalizada,
estado de TTS, avisos activos y generación local del turno. Resolverá una
expresión de forma determinista. La clase que dibuja no decidirá estados de
audio, ni usará el contenido de las transcripciones para hacerlo.

### Estados y eventos

| Condición o evento válido | Resultado visual |
|---|---|
| Inicio, conexión o reposo sin emoción vigente | `neutral` |
| Captura de conversación habilitada | `listening` |
| `stt` final no vacío del turno activo | `thinking`, hasta TTS, nueva captura, cancelación, error o plazo de espera |
| Emoción del LLM durante espera o habla | Se guarda; no interrumpe la animación de actividad |
| `tts.start` válido | `speaking` en bucle; se abre el ciclo visual de TTS |
| `tts.stop` y reproducción aún pendiente | Continúa `speaking` |
| Audio momentáneamente vacío antes de `tts.stop` | Continúa `speaking`; una pausa entre fragmentos no termina el ciclo |
| `tts.stop` y `IsPlaybackIdle()` verdadero | Termina el ciclo; vuelve a la actividad real o a la emoción de reposo |
| Interrupción, cierre de canal o desconexión | Cancela espera/TTS visual; recalcula desde el estado real |
| Entrada en notificación hablada | `speaking`, hasta final/cancelación y drenado según el ciclo de notificaciones existente |
| Aviso operativo prioritario | El aviso se presenta sin que una emoción posterior lo borre |

La primera etapa empieza `speaking` con `tts.start`, que puede preceder al
primer sonido. Su animación no mide la intensidad ni sigue fonemas. El final
normal sí exige las dos condiciones: fin de TTS y ausencia de audio pendiente.
Para notificaciones se respeta además su identificador de reproducción existente.

Orden propuesto de presentación: aviso operativo prioritario; vista previa
explícita; habla/notificación activa; espera de respuesta; escucha; emoción de
reposo; neutral. Los propietarios existentes de los avisos conservan su
capacidad de resolverlos; esto no cambia las prioridades del audio.

Las actualizaciones rutinarias a `neutral` del código base no se interpretarán
como una emoción nueva del LLM ni borrarán por accidente `thinking` o `speaking`.
La integración distinguirá el origen semántico de cada actualización.

### Duración, recuperación y generaciones

- Una emoción de conversación se conserva durante el turno. Después de terminar
  la respuesta queda disponible durante 5 segundos para el reposo; no desplaza
  a `listening`. Un turno nuevo restablece la emoción a `neutral`.
- Tras 30 segundos esperando el inicio de TTS, `thinking` vuelve a la actividad
  real o a `neutral`; se registra el motivo. No se cancela por ello la petición
  de red, y una respuesta posterior válida aún puede activar `speaking`.
- Si falta `tts.stop`, un ciclo sin audio pendiente y sin actividad de audio
  durante 30 segundos puede terminar visualmente. El temporizador se reinicia
  al aceptar audio del ciclo. Nunca se usa para cortar audio ni vaciar colas.
- Una interrupción o cambio de turno invalida temporizadores y callbacks locales
  anteriores. Un evento de drenado siempre se comprueba contra el estado actual.
- En modos de escucha simultánea, una nueva captura efectiva/interrupción tiene
  que actualizar el turno visual sin esperar una transición a otro `DeviceState`.
  Un cambio de VAD a silencio, por sí solo, no activa `thinking`.

La generación es local. El protocolo existente no permite identificar todos
los mensajes tardíos de turnos distintos que lleguen por la misma conexión.
Se descartan eventos incompatibles con el ciclo actual y callbacks locales
obsoletos; no se promete resolver mensajes de servidor indistinguibles sin
identificadores de turno. Esta entrega no modifica el protocolo para agregarlos.

### Emociones y alias

Los 12 nombres de GIF son válidos directamente. Además:

| Nombres recibidos | Recurso |
|---|---|
| `loving`, `love`, `kissy`, `embarrassed` | `pet` |
| `crying` | `sad` |
| `sleepy` | `sleeping` |
| `doubtful`, `dizzy`, `confused`, `surprised`, `shocked` | `shake` |
| `laughing`, `funny`, `silly`, `cool`, `delicious`, `confident` | `happy` |
| `winking` | `look_left` |
| `relaxed`, `angry`, `scowl` | `neutral` |
| `talking` | `speaking` |
| `mail`, `sending` | `email` |
| Desconocido, vacío, inválido o demasiado largo | `neutral` |

Son equivalencias propuestas, no expresiones nuevas dibujadas por el kit.
El límite de entrada será 40 caracteres ASCII; se aceptan letras, números,
`_` y `-`, normalizando mayúsculas. No se forman rutas desde nombres de red.
Una emoción llamada `speaking` no abre un ciclo real de TTS: es solamente una
elección gráfica y su vigencia sigue las reglas de las emociones.

Las animaciones `sleeping`, `pet`, `shake`, `look_left`, `look_right` y `email`
no activan por sí solas acciones físicas ni tareas. En esta entrega pueden
aparecer como emociones, pero no tienen disparadores nuevos de sensores/MCP.

## 6. Requisitos funcionales

Los requisitos usan el formato EARS: condición/evento y comportamiento exigido.

| ID | Requisito |
|---|---|
| FR-01 | Mientras la variante Elena esté activa, el sistema deberá mostrar el avatar en el lienzo nativo completo y ocultar el texto de conversación y la franja inferior. |
| FR-02 | Cuando llegue texto de STT, TTS o notificación hablada, el sistema deberá mantenerlo fuera de la pantalla sin modificar su procesamiento de audio/red. |
| FR-03 | Cuando se habilite una captura de conversación válida, el controlador deberá seleccionar `listening`, salvo una prioridad visual superior. |
| FR-04 | Cuando se reciba STT final no vacío del turno activo, el controlador deberá seleccionar `thinking` y aplicar las condiciones de salida de la sección 5. |
| FR-05 | Cuando comience un ciclo de TTS válido, el controlador deberá seleccionar `speaking` y recordar las emociones posteriores sin cambiar la animación de actividad. |
| FR-06 | Mientras un ciclo TTS normal esté activo, el controlador deberá terminar `speaking` únicamente después de `tts.stop` y reproducción drenada, salvo cancelación/error o recuperación por ausencia de actividad. |
| FR-07 | Cuando cambie la generación local, el controlador deberá invalidar sus callbacks y plazos anteriores sin modificar la máquina de estados del dispositivo. |
| FR-08 | Cuando llegue una emoción válida, el controlador deberá normalizarla y resolverla según la tabla de alias y la prioridad visual. |
| FR-09 | Cuando falte un recurso o falle la asignación/decodificación de un GIF, el sistema deberá intentar `neutral` una sola vez y después usar el respaldo estático de Elena, sin bucles de reintento. |
| FR-10 | Mientras exista un aviso operativo prioritario, el sistema deberá conservarlo hasta que su propietario lo resuelva, sin sustituirlo por una emoción de conversación. |
| FR-11 | Cuando se solicite una vista previa explícita, el sistema deberá conservar su comportamiento actual y restaurar después el estado de avatar vigente. |
| FR-12 | Mientras se repita la misma expresión resuelta, el sistema deberá conservar el reproductor actual en lugar de reiniciar o reasignar el GIF. |
| FR-13 | Cuando se compile la variante desde una clonación limpia, el empaquetado deberá incluir gráficos, respaldo, fuentes y modelos de voz necesarios, sin depender de rutas locales del kit. |
| FR-14 | Cuando se compile cualquier otra placa, el sistema deberá conservar su selección de recursos y comportamiento visual existentes. |
| FR-15 | Mientras el controlador esté activo, el sistema deberá procesar sus eventos en el bucle principal y ejecutar las operaciones LVGL bajo el bloqueo gráfico correspondiente. |
| FR-16 | Cuando se inicie, termine o cancele una notificación hablada, el controlador deberá seguir su ciclo e identificador existentes, manteniendo ocultos sus subtítulos. |

## 7. Arquitectura y límites de integración

Se añadirá una placa `firmware/main/boards/elena-stackchan/`, con identidad
`elena-stackchan`, target ESP32-S3 y una sola fábrica `DECLARE_BOARD`.
Reutiliza el hardware/pines del CoreS3, sin editar la placa genérica para
introducir el avatar. No añade soporte nuevo para servos u otros periféricos.
La implementación puede reutilizar helpers del CoreS3 o copiar el mínimo
necesario dejando documentada la procedencia y evitando una refactorización amplia.

Componentes previstos:

- `AvatarController`: resolución de eventos/estado, prioridad, alias y plazos;
  lógica comprobable en host, separada de LVGL y sin dependencia del servidor.
- `ElenaAvatarDisplay`: especialización de `SpiLcdDisplay` que mantiene el
  reproductor GIF existente, adapta las capas y diferencia emoción de actividad.
- Recursos y empaquetado de Elena: colección registrada en `index.json`, con
  alias que apuntan a los mismos archivos, sin duplicar los bytes de los GIFs.
- Hooks opcionales y semánticos en las interfaces comunes de pantalla/aplicación:
  cambio de actividad, STT final, TTS, cancelación/drenado y avisos. Por defecto
  no alteran otras pantallas. La interfaz común no menciona clases de Elena.

El controlador no deducirá estados comparando textos como «Escuchando» o
«Hablando», ni tratará cualquier `SetEmotion("neutral")` como un mensaje del LLM.
Los callbacks de red/audio comunicarán eventos mediante `Application::Schedule`
o bits existentes. Las tareas de audio no dibujarán ni esperarán al bloqueo LVGL.
Los avisos repetidos se agruparán y la actividad de audio se resumirá en una
marca temporal o señal equivalente: no se añadirá una tarea en cola ni una
asignación por cada bloque PCM/paquete para mantener el plazo de recuperación.

Se aprovecharán `on_playback_drained` e `IsPlaybackIdle()`. No hacen falta
análisis PCM ni asignaciones por bloque de audio en esta etapa. Se cubrirá el
orden real de `tts.stop`, `STATE_CHANGED` y `PLAYBACK_DRAINED`: que el dispositivo
ya esté en escucha no significa que haya terminado todo el audio pendiente.

Registro de placa: `config.json` → `scripts/build.py` → Kconfig → CMake → fuentes.
También se adaptarán la selección de `BOARD`/`BOARD_VARIANT` del Makefile y el
helper `tools/build_firmware_image.py`, que hoy fija el CoreS3 genérico. El
manifiesto de entrega debe identificar la placa que realmente se ha compilado.
La compatibilidad OTA se revisará explícitamente al cambiar la identidad.

## 8. Recursos y reproducción del build

El fork contendrá los 12 GIFs seleccionados, alias, licencia/procedencia
disponible y un inventario de hashes. Solo se incluirán recursos utilizados;
los PNGs de trabajo, variantes HQ y headers de visemas no son entradas del
binario de esta entrega. No se regenerará el arte silenciosamente.

El respaldo será una imagen estática de `neutral` preparada en RGB565 y
disponible desde la aplicación, sin depender de la partición de recursos ni
de una nueva decodificación completa. Su descriptor y mecanismo de dibujo
deben permitir la recuperación si falla el heap usado por el GIF. El respaldo
tiene un coste adicional aproximado de 150 KiB en flash de aplicación, a
comprobar junto con el tamaño final de la partición de app.

La imagen de recursos conservará las fuentes y modelos de voz requeridos.
`_emote_aliases.json` será una entrada del generador, no una promesa de que el
loader actual lo interpreta. CMake debe depender de los GIFs, alias y script,
para regenerar el resultado cuando cambien, sin borrar archivos manualmente.

No se cambian particiones, dependencias o locks por conveniencia. Si la imagen
completa no cabe en el mapa vigente, la implementación debe informar del dato
y revisar el diseño; no mover offsets ni reemplazar recursos de voz en silencio.

La entrega se generará con la habilidad local `stackchan-build` y se guardará
en `dist/firmware/<fecha>-<hora>-<propósito>-<commit>/`. Cualquier escritura física
usará exclusivamente `stackchan-flash`, con confirmación explícita del usuario.
No se copiarán direcciones de red, SSIDs, tokens ni backups a archivos seguidos.

## 9. Requisitos no funcionales

Estos son objetivos de aceptación propuestos, no resultados medidos:

| ID | Objetivo y forma de comprobarlo |
|---|---|
| NFR-01 | Desde que el bucle principal acepta un evento visual hasta presentar el primer cuadro: como máximo 150 ms, midiendo al menos 100 cambios con audio activo. |
| NFR-02 | Un único GIF decodificado por pantalla. Frente al mismo build sin el avatar: incremento máximo de pico de 1 MiB en PSRAM y 64 KiB en SRAM interna, usando los mismos modelos y funciones habilitadas. |
| NFR-03 | Tras calentamiento y 500 cambios de expresión, la memoria libre estabilizada de cada heap no caerá más de 32 KiB respecto al punto estabilizado inicial; no habrá reinicios, fallos de asignación ni crecimiento sostenido. Registrar también el mayor bloque libre. |
| NFR-04 | Una sesión física de 10 minutos con escucha, respuesta e interrupciones no introducirá cortes audibles, fallos de reproducción o pérdidas atribuibles al avatar frente al build base. Registrar condiciones, errores y observación humana. |
| NFR-05 | Las duraciones de los cuadros se conservarán según el GIF; medir retrasos de renderizado y comprobar que la animación no se congela bajo carga. No se exige un FPS superior al del recurso. |
| NFR-06 | Nombres de emociones acotados y resueltos contra una tabla fija; ninguna carga de archivos, ejecución o petición de red derivada del nombre. |
| NFR-07 | La integración no añadirá registros del texto hablado ni datos personales; registrará únicamente transiciones y diagnósticos gráficos necesarios. |
| NFR-08 | Una clonación limpia producirá app y recursos de la variante con entradas versionadas, dependencias fijadas y selección de placa verificable. |

## 10. Tratamiento de errores

| Caso | Comportamiento |
|---|---|
| Emoción desconocida o entrada inválida | Normalizar a `neutral`; no mostrar una carita de las fuentes |
| GIF ausente/corrupto o fallo al abrirlo | Intentar neutral y después el respaldo estático; registrar diagnóstico acotado |
| Partición de recursos inválida | Mantener el respaldo estático y la ruta operativa de recuperación existente |
| STT vacío o VAD a silencio | No iniciar espera de respuesta |
| Espera superior a 30 s | Volver a la actividad real sin cancelar red/audio |
| Fin TTS con audio pendiente | Mantener `speaking` hasta el drenado |
| Drenado momentáneo entre fragmentos | Mantener `speaking` mientras no haya fin TTS |
| Ausencia de fin TTS | Recuperación visual después de 30 s sin actividad y sin audio pendiente |
| Interrupción/desconexión | Invalidar turno visual y plazos; conservar el manejo existente del audio |
| Alerta durante habla | Presentar alerta sin que una emoción posterior la oculte; no alterar audio desde el controlador |
| Evento local de un turno anterior | Descartarlo; no restaurar animaciones ni emociones antiguas |

## 11. Criterios de aceptación

Cada fila expresa Dado / Cuando / Entonces. Las comprobaciones físicas siguen
pendientes hasta contar con una imagen y autorización para escribirla.

| ID | Dado | Cuando | Entonces |
|---|---|---|---|
| AC-01 | Variante Elena recién arrancada | Queda lista para conversar | Se ve Elena con el encuadre original, sin franja inferior ni texto de conversación |
| AC-02 | Captura activa y turno válido | Llega STT final no vacío | Pasa de `listening` a `thinking`, sin mostrar el texto |
| AC-03 | Espera de respuesta | Llega `tts.start` y luego audio | Se reproduce `speaking`; una emoción del LLM queda guardada |
| AC-04 | TTS activo con audio pendiente | Llega `tts.stop` | Sigue `speaking` hasta que `IsPlaybackIdle()` sea verdadero |
| AC-05 | TTS activo | Se vacían las colas entre dos fragmentos antes de `tts.stop` | No termina el ciclo de habla |
| AC-06 | Una respuesta interrumpida | Empieza otra captura y llega un callback local anterior | El callback no altera el nuevo turno; aparece la actividad vigente |
| AC-07 | Emociones de la tabla y nombres inválidos | Se recorren todas las entradas | Se selecciona el recurso esperado; ninguna entrada provoca un emoji original o una carga de ruta |
| AC-08 | Ausencia/corrupción de un GIF y después de toda la colección | Se intenta mostrar una expresión | Se usa neutral o el respaldo estático y el robot conserva audio y recuperación |
| AC-09 | Conversación o notificación hablada | Llegan transcripciones, frases y subtítulos | Ninguno aparece sobre el avatar |
| AC-10 | Un problema operativo o activación pendiente | Se presenta el aviso y llegan emociones | El aviso sigue legible hasta su resolución; la configuración sigue siendo posible |
| AC-11 | Un GIF activo | Se recibe repetidamente la misma expresión resuelta | No se reinicia la animación ni se crea otro decodificador |
| AC-12 | Vista previa explícita | Se cierra o vence su plazo | Vuelve la expresión correspondiente al estado actual, sin subtítulos |
| AC-13 | Espera sin respuesta o TTS sin fin y sin audio | Vence el plazo correspondiente de 30 s | Se recupera el estado visual sin cortar audio ni cancelar la petición |
| AC-14 | Repositorio limpio sin la carpeta original del kit | Se compila Elena y después CoreS3 genérico | Ambos seleccionan su placa/recursos; los cambios de un GIF invalidan el empaquetado de Elena |
| AC-15 | Firmware en el CoreS3 con audio activo | Se ejecutan las sesiones y cambios definidos en NFR-01 a NFR-05 | Se registran resultados y se cumplen los objetivos de respuesta, memoria y audio |
| AC-16 | Modos de escucha automático, manual y simultáneo disponibles | Se repite un turno y una interrupción en cada uno | La actividad visual coincide con el ciclo real y no depende de una transición de estado inexistente |

Validación automatizada: lógica del controlador con orden de eventos, alias,
plazos y generaciones; empaquetado y hashes; decodificación de todos los cuadros;
ausencia de reinicios innecesarios del GIF; y pruebas existentes del repositorio.
La comprobación del controlador debe usar un reloj controlable.

Validación de compilación: variante Elena, CoreS3 genérico y al menos un camino
sin pantalla/OLED cuando se toquen interfaces comunes; tamaño de app/recursos;
`make check-locks` y `make test`. Si un hook afecta a ambos transportes, verificar
WebSocket y MQTT/UDP. Los dos tests omitidos upstream del servidor se conservan.

Validación física: encuadre/color, indicadores, configuración, avisos, notificación
hablada, escucha, espera, habla, interrupción, desconexión/reconexión y memoria
durante las sesiones anteriores. Registrar qué modos/transportes se probaron y
cuáles no. Actualizar `docs/tested-versions.md` solo con evidencia real.

## 12. Trabajo posterior a la aprobación del diseño

- [ ] Elaborar el plan de implementación y pruebas a partir de este spec.
- [ ] Incorporar recursos seleccionados, procedencia y hashes al fork.
- [ ] Registrar la placa Elena en toda la cadena de selección y herramientas raíz.
- [ ] Implementar y comprobar el controlador visual y sus hooks opcionales.
- [ ] Adaptar presentación, prioridad de avisos y respaldo estático.
- [ ] Integrar el empaquetado con fuentes/modelos de voz y dependencias de CMake.
- [ ] Ejecutar pruebas de host, builds y comprobación de locks/tamaños.
- [ ] Generar una imagen reproducible con `stackchan-build`.
- [ ] Tras confirmación explícita, escribir y validar con `stackchan-flash`.
- [ ] Publicar primero el commit del fork y después su puntero raíz cuando corresponda.
- [ ] Registrar resultados y combinación probada; separar cualquier etapa de visemas.

Quedan a criterio de implementación los nombres exactos de tipos/hooks, reparto
de archivos, uso de helpers reutilizables y estructura de fixtures. Deben
respetar los comportamientos, límites y criterios definidos aquí. Si los
objetivos físicos no se cumplen, se documentarán los resultados y se revisará
el diseño antes de ampliar alcance o cambiar dependencias/particiones.
