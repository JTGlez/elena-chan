# Elena Avatar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrar a Elena en el CoreS3 con estados de conversación, emociones y pantalla sin subtítulos.

**Architecture:** Una placa propia selecciona `ElenaAvatarDisplay`, derivada de `SpiLcdDisplay`, y un controlador sin dependencias de LVGL resuelve los eventos semánticos de conversación. La aplicación publica eventos opcionales; el reproductor GIF y el servicio de audio existentes conservan sus responsabilidades. Los recursos se empaquetan con las fuentes y modelos de voz vigentes.

**Tech Stack:** ESP-IDF v6.1, ESP32-S3/CoreS3, C++, LVGL existente, Python estándar para herramientas/pruebas, compilador C/C++ de host.

**Spec:** [Diseño aprobado](../specs/2026-11-01-elena-avatar-design.md).

## Global Constraints

- Trabajar en la rama raíz `feature/elena-avatar`; los cambios de firmware se hacen en una rama de trabajo de su fork y se integran en `elena` antes de publicar el puntero raíz. Verificar aislamiento antes de ejecutar; conservar el kit original sin seguimiento y sin modificarlo.
- Mantener ESP-IDF v6.1 y los locks; no editar `sdkconfig*`, `build/`, componentes administrados ni recursos generados manualmente.
- Placa `elena-stackchan`, directorio `firmware/main/boards/elena-stackchan/`, target ESP32-S3, una fábrica `DECLARE_BOARD`. No cambiar la placa CoreS3 genérica.
- Lienzo 320 × 240; figura 280 × 210 en `(20, 30)`; sin transcripciones, texto de respuestas, franja inferior ni reloj permanente.
- Conservar indicadores mínimos, instrucciones de configuración, alertas operativas y vistas previas. No añadir servos, MCP, visemas ni cambios de protocolo/servidor.
- Emoción de reposo: 5 segundos después de la respuesta. Recuperación visual por espera/silencio de TTS: 30 segundos. No cortar audio por esos plazos.
- Primer cuadro en como máximo 150 ms; incremento de pico máximo de 1 MiB PSRAM y 64 KiB SRAM interna; pérdida estabilizada máxima de 32 KiB por heap tras 500 cambios.
- No añadir trabajo gráfico ni asignaciones por bloque PCM/paquete en las tareas de audio. Los hooks genéricos son opcionales y no cambian otras pantallas.
- Construir imágenes únicamente con `stackchan-build`. Toda escritura física requiere confirmación explícita y `stackchan-flash`; nunca usar directamente `idf.py flash` o esptool.
- No publicar tokens, datos de red, configuraciones privadas o backups. Publicar el commit del fork antes de publicar su puntero raíz.

## Review Focus

1. `tts.stop` y drenado en cualquier orden, incluidos silencios entre fragmentos: terminar la animación solo cuando corresponda. Pruebas de tareas 1 y 4.
2. Conversación nueva sin cambio de `DeviceState`, y callbacks anteriores después de una interrupción: mostrar el turno vigente. Pruebas de tareas 1 y 4.
3. Texto de notificaciones o configuración que comparte `SetChatMessage`: ocultar conversación y conservar avisos necesarios sin comparar traducciones. Pruebas de tareas 3 y 4.
4. Recurso ausente/corrupto o heap insuficiente: mantener Elena estática y audio operativo, sin recuperar los emojis originales. Pruebas de tareas 2 y 3.
5. Clonación sin el kit local, cambio incremental de un GIF y regreso a CoreS3 genérico: builds reproducibles con recursos/modelos y placa correctos. Pruebas de tareas 2, 3 y 5.

## Mapa de archivos e interfaces

Todos los caminos son relativos a la raíz `elena-chan`, salvo indicación contraria.

| Unidad | Archivos y responsabilidad |
|---|---|
| Contrato opcional de pantalla | Nuevos `firmware/main/display/conversation_visual.h`; cambios pequeños en `display.h` para capacidades/eventos con implementación vacía |
| Resolución de estado | Nuevos `firmware/main/boards/elena-stackchan/avatar_controller.{h,cc}`; reloj recibido como dato, prioridad, alias, generaciones y plazos |
| Placa y vista | Nuevos `elena_stackchan.cc`, `config.h`, `config.json`, `elena_avatar_display.{h,cc}`, `README.md` en la carpeta de placa; codec reutilizado del CoreS3 sin modificarlo |
| Recursos | Nueva carpeta de placa `assets/` con los 12 GIFs ligeros, alias, respaldo RGB565, hashes y procedencia; headers de respaldo generados en el directorio de build |
| Preparación/empaquetado | Nuevo `firmware/scripts/elena_avatar_assets.py`; extensión acotada de `scripts/build_default_assets.py`; selección y dependencias en `main/CMakeLists.txt` y Kconfig |
| Eventos de aplicación | Cambios en `firmware/main/application.{h,cc}`; señal de turno, TTS, audio pendiente, alertas y notificaciones; sin lógica de Elena |
| Integración raíz | `Makefile`, `tools/build_firmware_image.py`, sus pruebas y documentación; selección verificable de placa/variante |
| Evidencia | `docs/avatar-validation.md` y, después de pruebas físicas, `docs/tested-versions.md`; datos privados y binarios en rutas ignoradas |

Contrato de la tarea 1, compartido por las tareas posteriores:

```cpp
enum class ConversationActivity { Idle, Connecting, Listening, Maintenance };
enum class ConversationVisualEventKind {
    Activity, TurnStarted, FinalTranscript, Emotion, TtsStarted, TtsStopped,
    PlaybackState, AudioActivity, NotificationStarted, NotificationFinished,
    Cancelled, NoticeStarted, NoticeCleared, Tick
};
struct ConversationVisualEvent {
    ConversationVisualEventKind kind;
    uint32_t generation = 0;
    uint32_t playback_id = 0;
    uint64_t now_ms = 0;
    ConversationActivity activity = ConversationActivity::Idle;
    bool playback_idle = true;
    std::string_view emotion;
};
// Métodos nuevos de Display; por defecto false y no-op, respectivamente.
virtual bool SupportsConversationVisuals() const;
virtual void OnConversationVisualEvent(const ConversationVisualEvent& event);
```

`emotion` es prestada solo durante la llamada; antes de cruzar `Schedule` se
captura una cadena propietaria. Los eventos no contienen transcripciones.
Los avisos usan los métodos de texto existentes después de marcar su contexto
con `NoticeStarted` o actividad `Maintenance`.

---

### Tarea 1: Contrato y controlador de avatar comprobable en host

**Files:** crear el contrato y `avatar_controller.{h,cc}`; modificar `firmware/main/display/display.h`; crear `firmware/scripts/tests/test_avatar_controller.py` y `firmware/scripts/tests/fixtures/avatar_controller_test.cc`.

**Interfaces:** consume el spec. Produce `AvatarController::Handle(const ConversationVisualEvent&) -> void`, `AvatarController::Resolve(uint64_t now_ms) const -> AvatarPresentation`, `NormalizeAvatarEmotion(std::string_view) -> const char*`. `AvatarPresentation` contiene `const char* emotion`, `bool notice_active`, `bool maintenance` y `bool operator==(const AvatarPresentation&) const`; las cadenas resueltas son estáticas.

- [ ] Escribir el harness C++ con reloj explícito, invocado desde `unittest` siguiendo `test_ogg_demuxer.py`. Caso `test_stop_requires_drained`: TTS iniciado y `playback_idle=false`; `TtsStopped` mantiene `speaking`; `PlaybackState` con `true` termina el ciclo. Caso inverso: drenado antes de stop mantiene `speaking` hasta stop.
- [ ] Añadir casos `test_new_turn_rejects_stale_callbacks`, `test_realtime_capture_starts_new_turn`, `test_empty_stt_is_filtered_at_boundary`, `test_mood_expires_at_5000_ms`, `test_wait_expires_at_30000_ms`, `test_audio_activity_extends_missing_stop_deadline`, `test_notification_id_is_checked`, `test_notice_survives_emotion` y `test_all_aliases_and_invalid_names`. Assertar exactamente los recursos/plazos de las secciones 5 y 10 del spec, incluidos 40/41 caracteres, ASCII inválido y mayúsculas.
- [ ] Ejecutar `python3 -m unittest discover -s firmware/scripts/tests -p test_avatar_controller.py -v`; debe fallar por ausencia del contrato/controlador. Implementar las interfaces y estados mínimos hasta pasar todos los casos; mantener el enum de dispositivo intacto. El filtrado de STT vacío corresponde al adaptador de aplicación; el controlador recibe solamente `FinalTranscript` válido.
- [ ] Verificar los empates: aviso > vista previa gestionada por la vista > TTS/notificación > espera > escucha > emoción > neutral. No reiniciar deadlines por repetir el mismo estado; no terminar TTS cuando drena temporalmente y no ha llegado stop. Incluir estas aserciones en el caso de audio:

```cpp
AvatarController c;
ConversationVisualEvent e{ConversationVisualEventKind::TurnStarted, 1, 0, 0};
c.Handle(e);
e.kind = ConversationVisualEventKind::TtsStarted;
c.Handle(e);
e.kind = ConversationVisualEventKind::PlaybackState;
e.playback_idle = false;
c.Handle(e);
e.kind = ConversationVisualEventKind::TtsStopped;
c.Handle(e);
assert(std::string_view(c.Resolve(0).emotion) == "speaking");
e.kind = ConversationVisualEventKind::PlaybackState;
e.playback_idle = true;
c.Handle(e);
assert(std::string_view(c.Resolve(0).emotion) != "speaking");
```

- [ ] Ejecutar de nuevo el archivo de pruebas y las pruebas existentes de scripts; revisar el diff y crear un commit pequeño en el fork: `feat: add conversation avatar state controller`.

### Tarea 2: Recursos versionados, respaldo y empaquetado incremental

**Files:** crear `assets/gifs/*.gif`, `assets/aliases.json`, `assets/neutral.rgb565`, `assets/SHA256SUMS`, `assets/PROVENANCE.md` dentro de la placa; crear `firmware/scripts/elena_avatar_assets.py`, `firmware/scripts/tests/test_elena_avatar_assets.py` y utilidades C/headers de host en `firmware/scripts/tests/fixtures/avatar_gif/`; modificar `firmware/scripts/build_default_assets.py` y sus pruebas.

**Interfaces:** consume la tabla de alias y el decodificador real `gifdec.c`. Produce `validate_avatar_assets(asset_dir: Path) -> dict`, `prepare_avatar_fallback(gif_path: Path, output: Path) -> None`, `write_avatar_fallback_header(rgb565: Path, output: Path) -> None`. Extender el empaquetador con opciones opcionales `--emoji_directory` y `--emoji_aliases`; conservar valores/firmas anteriores por defecto.

- [ ] Escribir `test_manifest_and_all_frames_decode`, `test_aliases_share_files`, `test_preserves_srmodels_and_font`, `test_missing_file_or_invalid_alias_fails`, `test_fallback_matches_neutral_rgb565` y `test_output_changes_after_gif_change`. Comprobar 12 archivos, 865886 bytes originales, 34 cuadros/vuelta y dos vueltas con ambas opciones de caché LZW; hashes y dimensiones exactos. La lectura de cada imagen queda acotada a su archivo.
- [ ] Ejecutar `python3 -m unittest discover -s firmware/scripts/tests -p test_elena_avatar_assets.py -v` y confirmar el fallo inicial. Implementar los helpers usando biblioteca estándar; para preparar/verificar píxeles, compilar el decodificador real con adaptadores mínimos de host, sin añadir dependencias de Python.
- [ ] Copiar solo los GIFs ligeros del kit original. Generar el respaldo desde el primer cuadro de `neutral.gif`, RGB565 little endian, exactamente 153600 bytes. Documentar el comando reproducible de preparación y su procedencia. No presentar una licencia inexistente como concedida.
- [ ] Empaquetar los alias como entradas de `emoji_collection` que comparten `file`; no copiar un GIF por alias ni usar solamente `extra_files`. Reutilizar fuentes y modelos seleccionados por sdkconfig. El código y el JSON de alias deben resolver la misma tabla; comprobarlo en el harness de la tarea 1.
- [ ] Ejecutar las pruebas nuevas y `python3 -m unittest discover -s firmware/scripts/tests -p test_build_default_assets.py -v`. Commit del fork: `feat: package Elena avatar assets and static fallback`.

### Tarea 3: Placa Elena y vista sin subtítulos

**Files:** crear los archivos de placa/vista indicados en el mapa; modificar `firmware/main/Kconfig.projbuild` y `firmware/main/CMakeLists.txt`; crear `firmware/scripts/tests/test_elena_avatar_display.py`, su fixture C++ y casos de registro en `test_build.py`.

**Interfaces:** consume controlador, contrato, colección y header de respaldo. Produce `ElenaAvatarDisplay : public SpiLcdDisplay` con constructor compatible con el de SPI existente; overrides de `SetupUI`, `SetEmotion`, `SetChatMessage`, `SetHideSubtitle`, `OnConversationVisualEvent`, `SupportsConversationVisuals`, `SetPreviewImage`, `SetTheme`, `SetStatus` y `UpdateStatusBar`. Conservar las dos sobrecargas de `ShowNotification` y adaptar su visibilidad si hace falta. Helpers privados `ApplyPresentation(const AvatarPresentation&) -> void` y `ShowStaticFallback() -> void`.

- [ ] Escribir pruebas que ejecuten la clase con adaptadores de host de LVGL/pantalla: `test_conversation_and_notification_text_stays_hidden`, `test_operational_notice_and_maintenance_text_remain_visible`, `test_same_expression_does_not_reopen_gif`, `test_missing_and_oom_use_static_elena`, `test_theme_and_asset_reload_preserve_presentation`, `test_preview_restores_current_state`, `test_clock_and_notification_timer_do_not_restore_normal_status_text`. Comprobar que `SetHideSubtitle(false)` no reactiva conversación en esta variante. Ejecutar `python3 -m unittest discover -s firmware/scripts/tests -p test_elena_avatar_display.py -v`; confirmar el fallo inicial.
- [ ] Implementar capas de 320 × 240, indicadores superiores mínimos y contexto de avisos. Usar `LcdDisplay::SetEmotion` para recursos válidos; comprobar existencia/carga antes de aceptar la presentación, impedir el fallback de fuentes y reintentar neutral una sola vez. La imagen estática tiene descriptor/datos disponibles sin nuevas reservas del tamaño de un cuadro. Si el intento falla, no marcar esa expresión como correctamente aplicada.
- [ ] Crear la placa a partir del hardware CoreS3 sin editar sus archivos: copiar lo necesario en `elena_stackchan.cc` y `config.h`; reutilizar `cores3_audio_codec.{h,cc}` por rutas explícitas de include/CMake. En `config.json`: `type=elena-stackchan`, `target=esp32s3`, variante `elena-stackchan`, símbolo explícito `CONFIG_BOARD_TYPE_ELENA_STACKCHAN=y`, mismas opciones de cámara/PSRAM. Omitir `manufacturer`: el validador prohíbe declararlo en una carpeta plana de `main/boards/`. Registrar ese símbolo en Kconfig y seleccionar exclusivamente esta fábrica en CMake.
- [ ] Añadir al build la preparación del header en `build/`, la colección de Elena y sus dependencias de archivos. `assets/` no entra en el glob de fuentes. No incluir fuentes de la placa genérica que contengan otra fábrica. Añadir pruebas de listado y resolución de la nueva variante, conservando el listado CoreS3 existente.
- [ ] Ejecutar las pruebas nuevas, `python3 -m unittest discover -s firmware/scripts/tests -p test_build.py -v` y el listado de placas. Comprobar invalidación de CMake al modificar un GIF en una copia temporal. Documentar la placa y commit del fork: `feat: add Elena StackChan board and avatar display`.

### Tarea 4: Eventos semánticos de aplicación y fin de audio

**Files:** modificar `firmware/main/application.{h,cc}`; crear `firmware/main/display/conversation_visual_session.{h,cc}` y `firmware/scripts/tests/test_conversation_visual_session.py` con fixture C++; registrar la fuente común en CMake.

**Interfaces:** consume el contrato de la tarea 1. Produce `ConversationVisualSession::BeginTurn(uint64_t now_ms) -> uint32_t`, `ConversationVisualSession::MakeEvent(ConversationVisualEventKind kind, uint64_t now_ms) const -> ConversationVisualEvent`, `ConversationVisualSession::Accepts(uint32_t generation) const -> bool`. Añadir `Application::DispatchConversationVisualEvent(const ConversationVisualEvent&) -> void`, llamado en el bucle principal, y `Application::BeginConversationVisualTurn() -> void`.

- [ ] Escribir pruebas de integración del adaptador con un receptor falso que usa el controlador real: `test_stop_state_change_and_drain_permutations`, `test_realtime_capture_without_state_transition`, `test_local_callbacks_are_generation_guarded`, `test_notification_finish_and_cancel`, `test_empty_stt_and_vad_silence_do_not_think`, `test_operational_notice_lifecycle`, `test_no_events_for_unsupported_display`. Afirmar que un stop antiguo sin identidad de servidor no se promete distinguir; el filtrado garantizado es de callbacks locales y ciclos incompatibles.
- [ ] Ejecutar `python3 -m unittest discover -s firmware/scripts/tests -p test_conversation_visual_session.py -v`; confirmar fallo. Implementar el adaptador y hooks opcionales en `Initialize`, cambios de estado, STT/LLM/TTS, apertura/cierre de canal, interrupción, notificación, `Alert`/`DismissAlert`, activación y actualización. Distinguir neutral rutinario de emoción del LLM. Las notificaciones conservan `playback_id`.
- [ ] Crear un timer opcional de 50 ms únicamente para pantallas con capacidad: su callback activa `MAIN_EVENT_CONVERSATION_VISUAL` en el bit 14, agregado a `ALL_EVENTS`. El main loop emite `Tick` y comprueba `IsPlaybackIdle()`; `MAIN_EVENT_PLAYBACK_DRAINED` también revalida el estado actual. Destruir el timer en el destructor. No usar comparaciones con textos traducidos.
- [ ] Resumir actividad de audio aceptado en una marca temporal/generación atómica y consumir cambios desde el main loop. No programar callbacks ni reservar memoria por paquete para esa señal. Capturar la generación al programar callbacks propios y validar al ejecutarlos; aumentar generación en nueva captura efectiva, interrupción y cierre de canal. Conservar la lógica real de escucha/colas existente.
- [ ] Añadir el caso `test_audio_events_are_coalesced`: muchos paquetes entre dos ticks producen como máximo una actualización resumida y prolongan el plazo de 30000 ms. Verificar todos los casos de las tareas 1 y 4 y compilar caminos WebSocket/MQTT-UDP cuando el hook los afecte. Commit: `feat: drive avatar from conversation lifecycle`.

### Tarea 5: Selección raíz y entrega reproducible

**Files:** modificar `Makefile`, `tools/build_firmware_image.py`, `tools/tests/test_build_firmware_image.py`, `.github/workflows/ci.yml` y `.claude/skills/stackchan-build/SKILL.md` solo en su documentación de selección. La placa ya documenta sus opciones en el fork.

**Interfaces:** consume la placa `elena-stackchan`. Produce flags `--board` y `--variant` en el helper y variables Make `BOARD`/`BOARD_VARIANT` pasadas también a `firmware-image`. Defaults raíz para este proyecto: `elena-stackchan` en ambos; el CoreS3 genérico sigue seleccionable explícitamente.

- [ ] Escribir `test_selected_board_reaches_build_and_manifest`, `test_generic_cores3_override`, `test_invalid_board_variant_stops_before_build` y `test_image_still_uses_only_flasher_args_files`. Assertar que comando, sdkconfig seleccionado y manifiesto corresponden a la misma placa; la selección inválida no produce un paquete que aparente éxito.
- [ ] Ejecutar `python3 -m unittest discover -s tools/tests -p test_build_firmware_image.py -v`; confirmar el fallo de los nuevos casos. Implementar la selección sin cambiar la lectura privada de OTA, sin cambiar silenciosamente a la nube y sin incluir merged images/NVS.
- [ ] Ejecutar de nuevo las pruebas de herramientas y el dry-run de Make para ambas selecciones. Añadir Elena a la matriz de compilación de CI conservando CoreS3, versiones y digest IDF. Documentar el build de Elena y cómo elegir CoreS3 genérico usando `stackchan-build`. Verificar y documentar el tratamiento real de la identidad `elena-stackchan` por OTA y la migración inicial desde CoreS3; no permitir una vuelta silenciosa a binarios genéricos ni borrar NVS para cambiar de identidad. No construir imágenes con otra ruta de entrega.
- [ ] Publicar los commits de firmware en la rama `elena` del fork antes de registrar/publicar su puntero en la raíz; conservar hashes anteriores. Comprobar que el commit del puntero está disponible en el fork. Commit raíz con herramientas y puntero: `feat: build Elena avatar firmware variant`.

### Tarea 6: Verificación de software, imagen y validación física separada

**Files:** crear `docs/avatar-validation.md`; actualizar `docs/tested-versions.md` con lo efectivamente comprobado. Evidencia con datos privados y las imágenes quedan en las rutas ignoradas del proyecto.

**Interfaces:** consume el firmware y las herramientas de las tareas anteriores. Produce una imagen identificada por commit y un informe que distingue host, compilación y robot. No produce nuevas funcionalidades.

- [ ] Ejecutar `make test`; resultado exigido: éxito de todos los suites aplicables, conservando los dos skips upstream. Compilar Elena, CoreS3 genérico y un camino OLED/no-display representativo mediante el entorno/skills del proyecto. Elegir una variante concreta existente con `scripts/build.py --list-boards` y registrar su nombre en la evidencia; no improvisar pines o targets.
- [ ] Ejecutar `make check-locks` y comprobar tamaños de app/recursos en el mapa vigente. Verificar desde una clonación/worktree limpia sin la carpeta original del kit, incluyendo el ciclo incremental de recursos. Si una comprobación de locks falla por red/sandbox, resolver la restricción y repetirla; no etiquetarla como pasada.
- [ ] Con `stackchan-build`, generar la imagen final para el propósito `elena avatar conversation`; verificar hashes, `flasher_args.json`, placa/variante, commits y recursos. Registrar qué combinaciones de transporte y escucha se compilaron. No afirmar validación física.
- [ ] Preparar con `stackchan-flash` una operación revisable y solicitar la confirmación explícita necesaria antes de cualquier escritura. Si no está autorizada todavía, entregar imagen e informe con la parte física pendiente.
- [ ] Tras autorización, probar AC-01 a AC-16 aplicables, los 100 cambios para latencia, 500 cambios para memoria y 10 minutos de audio. Registrar SRAM/PSRAM y mayor bloque libre antes/después; comparar con el mismo hardware/modelos sin avatar. Exigir ≤150 ms, ≤1 MiB/64 KiB de incremento, ≤32 KiB de pérdida estabilizada y ausencia de cortes atribuibles al avatar. No detener otros servicios ni cambiar modelos para favorecer la medición.
- [ ] Revisar el diff completo, informar limitaciones y actualizar solo los resultados observados. Realizar la revisión final antes de integración. No marcar los pasos físicos completos por haber compilado. Commit de documentación de validación, manteniendo las evidencias privadas fuera de Git.

## Cobertura y punto de entrega

| Requisitos del spec | Tareas |
|---|---|
| FR-01, FR-02, FR-10, FR-11 | 3 y 4 |
| FR-03 a FR-08, FR-15, FR-16 | 1 y 4 |
| FR-09 y FR-12 | 2 y 3 |
| FR-13 y FR-14 | 2, 3 y 5 |
| NFR-01 a NFR-05 | 6, con prevención de reservas/reinicios en 1, 3 y 4 |
| NFR-06 y NFR-07 | 1, 2 y 4 |
| NFR-08 y entrega | 2, 3, 5 y 6 |
| AC-01 a AC-16 | Pruebas de host en 1 a 5 y comprobación física aplicable en 6 |

Este plan está escrito; ninguna casilla acredita una implementación realizada.
Antes de ejecutar se elige el método de ejecución. La autorización del spec
no sustituye la confirmación exigida para escribir físicamente al robot.
