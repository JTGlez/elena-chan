# Elena: perfil controlado de Hermes (fase 1)

Estado: **pendiente de revisión del usuario**. Fecha: 2026-11-01.

Este documento especifica el perfil base y la fase 1. No autoriza dar acceso a la
PC ni las fases 2 a 5, y no requiere ninguna escritura al robot.

## 1. Objetivo

Sustituir el LLM directo del server por un perfil de Hermes versionado en este
repo, de modo que Elena tenga personalidad y memoria propias, usando el modelo
local, **sin poder actuar sobre la PC**.

El objetivo a largo plazo es un agente capaz de interactuar con la PC. Se llega por
fases, y cada fase que amplía el acceso tiene su propio spec.

### Decisiones tomadas

| Tema | Decisión |
|---|---|
| Alcance de este spec | Perfil base y fase 1; el resto queda como hoja de ruta |
| Integración | Hermes ocupa el lugar del LLM en el server |
| Aislamiento | Perfil dedicado con lista mínima de herramientas, sin contenedor |
| Modelo | Qwen Flash a través de `model-gate` (proveedor `basalt`) |
| STT y TTS | Siguen en MiniMax durante esta fase |
| Firmware | Sin cambios; ya apunta al server local |

### Alternativas descartadas por ahora

- **Contenedor:** añade un límite real del sistema operativo, pero hoy protegería
  contra herramientas que no estarán activas. Debe evaluarse antes de la fase 2.
- **Hermes como herramienta de un LLM rápido:** reduciría la latencia de la charla,
  pero cambia el flujo del server y deja la charla sin la memoria de Hermes. Se
  retoma solo si las mediciones muestran que hace falta.

## 2. Modelo de seguridad

Un perfil de Hermes **no es un sandbox**. Separa configuración, memoria y sesiones,
pero con el backend `local` el proceso tiene los permisos del usuario. `SOUL.md`
orienta al modelo y no impone límites.

Por defecto, la plataforma `api_server` expone el conjunto completo de herramientas,
incluida la terminal. Sus comandos peligrosos se niegan por defecto
(`approvals.unattended_mode: deny`), pero esa detección no es una lista de lo
permitido y no se considera una barrera suficiente.

En esta fase el control se apoya en:

1. **Lista mínima:** solo el toolset de memoria está permitido, y
   `agent.disabled_toolsets` quita de forma explícita los demás.
2. **Comprobación que falla cerrado:** antes de atender, se consulta
   `GET /v1/toolsets`. El gateway se detiene si aparece una herramienta fuera de la
   lista, si la respuesta es inválida o si no se puede consultar.
3. **Red local mínima:** el gateway solo escucha en `127.0.0.1:8650`, con clave propia.
   El robot nunca le habla directamente.
4. **Sin MCP, cron ni delegación:** ninguno se configura en el perfil.

Riesgo residual aceptado: el límite depende de la configuración de Hermes, porque
el proceso corre con el usuario de la PC. Un fallo de Hermes que ignore la lista
daría más acceso del previsto. Por eso la comprobación corre en cada arranque y el
contenedor es requisito de decisión para la fase 2.

## 3. Componentes

```text
Repo                                     Máquina (no versionado)
brain/profile/                           ~/.hermes/profiles/elena/
├── distribution.yaml   ──instalar──→    ├── SOUL.md, config.yaml
├── config.yaml                          ├── .env (claves)
└── SOUL.md                              └── memoria, sesiones, logs

config/server.hermes.yaml.template ──→   data/.config.yaml del server
tools/ + Makefile                        (instalar, arrancar, comprobar)
```

| Pieza | Responsabilidad |
|---|---|
| `brain/profile/config.yaml` | Modelo, herramientas permitidas, API local y negación de comandos peligrosos |
| `brain/profile/SOUL.md` | Personalidad y reglas de voz: frases breves, sin listas ni símbolos |
| `brain/profile/distribution.yaml` | Nombre, versión y variables requeridas, sin valores |
| `config/server.hermes.yaml.template` | MiniMax para STT y TTS; Hermes como LLM |
| `tools/check_hermes_tools.py` | Compara las herramientas activas contra la lista permitida |
| `make brain-install` | Instala o actualiza el perfil, sin tocar memoria ni `.env` |
| `make brain-run` | Comprueba las herramientas y arranca el gateway del perfil; lo detiene si la comprobación falla |
| `make brain-check` | Ejecuta la comprobación contra un gateway en marcha |
| `make server-config PROFILE=hermes` | Genera la configuración del server con este perfil |

### Claves

- `API_SERVER_KEY` y `BASALT_API_KEY` viven en `~/.hermes/profiles/elena/.env`.
- `make brain-install` genera `API_SERVER_KEY` si no existe y copia
  `BASALT_API_KEY` desde `.localworker/qwen-flash-worker/.env`, sin mostrarlas.
- `make server-config PROFILE=hermes` lee `API_SERVER_KEY` del perfil y
  `MINIMAX_API_KEY` de `config/minimax.env`. El generador se amplía para resolver más
  de una clave.

Ninguna clave entra en archivos versionados. `brain/profile/` no incluye `.env`.

### Convivencia

- El perfil `default`, su modelo y su gateway no se modifican.
- El puerto `8650` evita el `8642` por defecto.
- Hermes queda registrado en `docs/tested-versions.md` en la versión `0.22.5`.
  `distribution.yaml` exige esa versión o una superior.

### Fork del server

No se prevén cambios: el adaptador `openai` ya habla el formato de la API de Hermes.
Si la validación muestra que hacen falta, se discuten antes de tocarlo.

## 4. Flujo de un turno

```text
1. Robot → server: audio
2. Server: VAD → STT (MiniMax) → texto
3. Server → Hermes :8650: prompt + historial + texto, con streaming
4. Hermes: SOUL.md + memoria → Qwen vía model-gate
5. Hermes → server: texto por partes
6. Server: frases → TTS (MiniMax) → robot
```

- **Historial:** lo lleva el server, porque `/v1/chat/completions` no conserva estado
  y recibe la conversación completa.
- **Memoria de largo plazo:** vive en el perfil de Hermes.
- **Personalidad:** vive en `SOUL.md`. El prompt del server queda en una instrucción
  mínima y se conserva `language: español`.

## 5. Errores

El server registra los fallos del LLM y termina el turno
(`core/connection.py:1145`). Se espera que el robot quede en silencio; la validación
debe confirmarlo.

| Falla | Comportamiento | Tratamiento en esta fase |
|---|---|---|
| Gateway apagado | Falla la conexión | Se documenta arrancar `make brain-run` antes que el server |
| Qwen sin cargar | `model-gate` lo arranca en la primera petición | Se mide el arranque en frío y se calienta antes de conversar |
| Otro modelo en la GPU | `model-gate` espera hasta 5 min a que termine y hasta 3 min a cargar | Riesgo aceptado y documentado; ver abajo |
| Herramienta inesperada | `make brain-run` se detiene | Se corrige la configuración antes de atender |
| Hermes sin responder | Silencio | Timeout de lectura de 30 s en el LLM del server, ajustable tras medir |

### GPU compartida

`model-gate` atiende un backend a la vez, y otros procesos pueden cambiarlo. Una
conversación podría quedar en silencio varios minutos, o quitarle el modelo a otra
tarea. En esta fase no se cambia `model-gate`: las pruebas de voz se hacen con Qwen
cargado y sin otro trabajo en la GPU.

## 6. Pruebas

### Automáticas, sin GPU y en CI

- `brain/profile/` no contiene claves, IPs ni MACs.
- La configuración usa `127.0.0.1` y solo permite la memoria.
- `check_hermes_tools.py`, con respuestas simuladas: la lista correcta pasa; una
  herramienta extra, una respuesta inválida o un gateway caído fallan.
- El generador resuelve las dos claves del perfil `hermes` y falla si falta alguna.

### Locales, con Hermes y Qwen, sin robot

1. Instalar el perfil y confirmar que `default` conserva su modelo y su gateway.
2. Pedir por la API usar la terminal, leer un archivo y buscar en la web. Las tres
   deben fallar, y el archivo de prueba no debe aparecer en la respuesta.
3. Decirle un dato, iniciar otra conversación y comprobar que lo recuerda.
4. Medir el arranque en frío y, con el modelo caliente, el primer token y el total,
   comparando contra Qwen directo.

### Con el robot

5. Conversar varios turnos y comparar con la base de MiniMax (3–4 s, sin contar la
   detección de fin de voz ni la reproducción).
6. Reiniciar el server y confirmar que la memoria sigue funcionando.

### Puntos por comprobar

La documentación no los resuelve, y el plan debe tratarlos primero:

- **Doble prompt:** Hermes agrega el prompt del server sobre el suyo, y la plantilla
  del server añade reglas de personalidad e idioma. Hay que confirmar que no se
  contradigan con `SOUL.md`. Si chocan, primero se ajusta `SOUL.md` y el prompt del
  server; cambiar la plantilla base se discute aparte.
- **Sesiones y memoria:** sin un ID explícito, Hermes deriva la sesión del prompt y
  del primer mensaje. Hay que probar si eso fragmenta sesiones o afecta a la memoria.
- **Lista de herramientas:** hay que confirmar los nombres exactos de los toolsets,
  y que quitarlos deja solo la memoria según `GET /v1/toolsets`.
- **Instalación local:** hay que confirmar que `hermes profile install` funciona con
  una subcarpeta de este repo y no solo con la raíz de un repositorio. Si no, se
  copian los archivos con un script equivalente, sin tocar datos de usuario.

## 7. Criterios de éxito

- [ ] El perfil se instala desde el repo con un solo comando, y `default` queda intacto.
- [ ] Solo la memoria aparece activa, y el arranque falla si aparece algo más.
- [ ] El gateway solo escucha en `127.0.0.1`.
- [ ] Elena conserva un dato entre conversaciones y responde de forma breve y en español.
- [ ] La latencia queda medida y registrada en `docs/`. Esta fase no exige un número.
      Si no se puede conversar cómodamente, se discute antes de seguir.

## 8. Fuera de alcance

Terminal, archivos, web, MCP, herramientas del robot, tareas largas, contenedor,
STT y TTS locales, cambios en `model-gate`, avisos hablados de espera o error, y
arranque o reconexión automáticos.

## 9. Hoja de ruta

Estas fases **no quedan aprobadas por este spec**. Cada una requiere el suyo.

| Fase | Alcance | Requisito para empezar |
|---|---|---|
| 2. Lectura limitada de la PC | Consultar archivos y estado en rutas elegidas | Spec propio y decisión sobre el contenedor |
| 3. Herramientas del robot | Expresiones y movimientos por MCP | Firmware con servos y un puente en el server |
| 4. Acciones reversibles | Lista permitida de acciones | Definir qué cuenta como reversible |
| 5. Acciones sensibles | Requieren confirmación fuera de la voz | Canal de aprobación probado |
| Transversal | Tareas largas con aviso hablado y voz de error | Datos de latencia de la fase 1 |
