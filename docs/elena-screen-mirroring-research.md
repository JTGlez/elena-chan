# Ver la pantalla de Elena desde la PC

Investigación del 2026-11-02. Código revisado: repositorio `573d0d1`, firmware
`fe366d4b3dde0bb8358d9afc80a1e66dbdaf1efc`, servidor
`966200ebc9e0f83fedddda23d63fda1ae7a2aea2`. Es un informe de viabilidad;
no se implementó un visor ni se midió la captura en el robot.

## Qué existe hoy

El firmware tiene la herramienta MCP `self.screen.snapshot`, registrada en
[`mcp_server.cc`](../firmware/main/mcp_server.cc). Recibe `url` y `quality`
(1–100, por defecto 80), genera un JPEG y lo envía a esa URL mediante HTTP POST
multipart, campo `file`, nombre `screenshot.jpg`. Espera una respuesta HTTP 200;
la respuesta MCP confirma el resultado, sin devolver la imagen.

[`LvglDisplay::SnapshotToJpeg`](../firmware/main/display/lvgl_display/lvgl_display.cc)
captura el árbol de la pantalla activa en RGB565 y lo comprime como JPEG.
`ElenaAvatarDisplay → SpiLcdDisplay → LcdDisplay → LvglDisplay` hereda esa ruta.
Esto incluye el avatar y los indicadores que sean hijos de esa pantalla;
no es una foto tomada por la cámara del robot.

Se verificó en la entrega local documentada como flasheada,
`20261102-041708-elena-stronger-reds-fe366d4`:

- Su `sdkconfig` contiene `CONFIG_LV_USE_SNAPSHOT=y`.
- Su `xiaozhi.bin` contiene el nombre `self.screen.snapshot`.

Son pruebas de que la función se incluyó en la imagen, no de que una captura
haya funcionado en el dispositivo. Los tests de pantalla del host deshabilitan
`CONFIG_LV_USE_SNAPSHOT`; sus resultados no validan esta función.

## Qué falta para usarla desde la PC

La herramienta está marcada como de uso del operador. El firmware la incluye
en `tools/list` con `params.withUserTools=true`. El cliente MCP del servidor
actual no pide esa opción y `call_mcp_tool` rechaza nombres no descubiertos.
También habría que conservar la opción en las peticiones paginadas.

El servidor tiene transporte MCP por la conexión del robot, pero no ofrece un
endpoint HTTP para solicitar esta captura desde una ventana o desde un agente.
Su servidor HTTP actual expone OTA y explicación de imágenes. Una segunda
conexión WebSocket desde la PC crea otra sesión; no permite enviar órdenes a la
sesión del robot por sí sola.

Por tanto, el primer prototipo puede aprovechar el firmware actual, añadiendo
en el servidor o en un arnés de desarrollo un acceso a la conexión activa y un
receptor HTTP de imágenes. No parece necesario volver a flashear para probar
una captura. Esa conclusión procede de la inspección del código y del binario;
queda pendiente comprobarla en ejecución.

## Alternativas

| Alternativa | Utilidad | Coste y límite |
|---|---|---|
| Captura MCP bajo demanda y refresco lento | Ver el estado real de la interfaz, guardar evidencia, entregar una imagen al agente | Reutiliza la imagen actual; requiere puente y receptor en la PC. La frecuencia segura debe medirse |
| Transmisión dedicada de frames o regiones | Seguir las animaciones y registrar transiciones | Requiere cambios de firmware, memoria acotada y envío separado de las tareas de audio y pantalla |
| Simulador con LVGL real en la PC | Iterar diseño, assets y secuencias sin flashear cada cambio | Requiere un ejecutable nuevo. Los adaptadores LVGL de los tests actuales no son un renderizador. No reproduce el robot en ejecución |

La primera alternativa es la recomendada para empezar. Una ventana web podría
mostrar la última captura, su hora y su antigüedad, permitir solicitar otra y
activar temporalmente el refresco. Un archivo estable o endpoint de imagen
permitiría que el agente obtuviera el mismo frame sin depender de mirar la
ventana. Un agente con visión analizaría capturas solicitadas o seleccionadas;
no se asume que observe automáticamente cada frame de un vídeo continuo.

Un ensayo inicial podría comenzar con una captura aislada en reposo y después
una cada dos segundos. Esa cadencia es una propuesta conservadora para medir,
no una frecuencia ya validada. La captura debe detenerse cuando no se usa,
mantener una sola petición pendiente y mostrar fallos o imágenes antiguas.

## Por qué no repetir la captura actual a alta frecuencia

La implementación mantiene `DisplayLockGuard` durante la captura, el intercambio
de bytes y toda la compresión JPEG. Además, `McpServer::DoToolCall` ejecuta la
herramienta en la tarea principal de la aplicación: también el POST y la espera
de su respuesta ocupan esa tarea. Podría retrasar animaciones, procesamiento de
eventos y envío de audio. No se midió todavía cuánto.

El LCD usa refresco parcial con una franja DMA de 320×20 píxeles. No hay un
framebuffer completo de pantalla listo para enviarse sin trabajo adicional.
Un frame completo RGB565 de 320×240 requiere al menos 153 600 bytes, 150 KiB,
más estructuras y compresión. Dos buffers requieren al menos 300 KiB. A diez
frames por segundo, transmitir RGB565 completo supone 1,536 MB/s o 12,288 Mbit/s
de payload, antes del protocolo; no es una medida de rendimiento alcanzable.

Para una transmisión dedicada convendría evaluar dos rutas: reutilizar un
buffer de snapshot y comprimir fuera del bloqueo, o copiar las regiones que
LVGL envía al LCD y reconstruirlas en la PC. La segunda evita volver a dibujar
para cada captura, pero exige manejar límites de frame, rotación, orden de
bytes, sincronización inicial y resincronización. En ambas, la red debe tener
una cola acotada y descartar trabajo cuando el consumidor se atrasa, sin frenar
el LCD. El buffer DMA pequeño actual debe conservarse.

LVGL 9.5, la versión fijada en `dependencies.lock`, documenta buffers de snapshot
reutilizables. ESP-IDF v6.1 ofrece servidor HTTP con WebSocket. Son piezas
compatibles para investigar esta segunda etapa, no un espejo ya integrado.
Fuentes: [snapshot de LVGL 9.5](https://lvgl.io/docs/open/9.5/main-modules/draw/snapshot.html),
[servidor HTTP de ESP-IDF v6.1](https://docs.espressif.com/projects/esp-idf/en/v6.1/esp32s3/api-reference/protocols/esp_http_server.html).

LVGL también ofrece un [driver SDL para ejecutar interfaces en la PC](https://lvgl.io/docs/open/9.5/integration/pc/sdl).
Sería útil como herramienta complementaria, reutilizando la vista de producción
y el decodificador, en vez de mantener una reproducción independiente del avatar.

## Qué permitiría validar

Las capturas ayudarían a revisar expresiones, posiciones, recortes, indicadores
y avisos, y a registrar estados observados durante una conversación. Para
comparaciones exactas de píxeles convendría una exportación sin pérdida; JPEG
introduce cambios de color y compresión incluso con calidad alta.

Una captura digital representa lo renderizado por software. No comprueba la
gamma del panel, retroiluminación, cableado, transferencia SPI ni el aspecto
óptico final. Una cámara externa mirando el robot sigue siendo útil para esas
comprobaciones y para contrastar la imagen digital con la pantalla física.

La prueba inicial pendiente es recibir un JPEG del robot, decodificarlo y
comprobar tamaño y contenido. Después habría que medir tiempo de captura,
duración del bloqueo, memoria libre y mayor bloque disponible, bytes por frame,
errores y frescura del visor. Para usarlo mientras habla, comparar sesiones con
y sin captura: pérdidas de audio, avisos del watchdog y latencia de conversación.
La referencia de audio debe medirse en la misma imagen y condiciones.

El puente debe limitarse a las operaciones de pantalla necesarias para el
operador, sin publicar el catálogo completo de herramientas ni incorporarlo al
LLM conversacional. Las direcciones y credenciales de esta máquina permanecerían
en configuración local sin seguimiento.
