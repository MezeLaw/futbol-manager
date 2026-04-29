# futbol-manager

Bot de WhatsApp que gestiona la asistencia al grupo de fútbol. Envía encuestas automáticas los lunes y viernes a las 14:00, registra los votos con polls nativas de WhatsApp y publica la lista actualizada en tiempo real. Incluye un MCP custom para disparar encuestas desde Claude.

---

## Arquitectura

```
[APScheduler L/V 14:00]
        │
        ▼
[Bot Python :8000] ──── send_poll / send_text ────► [Evolution API :8080] ──► WhatsApp grupo
        ▲                                                      │
        └──────────── webhook (messages.upsert) ──────────────┘
        │
        ▼
    [SQLite data/futbol.db]
        ▲
        │
[MCP futbol :stdio] ──── POST /encuesta ────► [Bot Python :8000]
```

**Componentes:**

| Componente | Puerto | Tecnología |
|---|---|---|
| Bot WhatsApp | 8000 | Python / FastAPI / APScheduler |
| Evolution API | 8080 | Docker (Node.js + Baileys) |
| PostgreSQL | 5432 | Docker (usado por Evolution API) |
| Redis | 6379 | Docker (caché de Evolution API) |
| MCP | stdio | Python / FastMCP |
| Base de datos del bot | — | SQLite (`data/futbol.db`) |

---

## Cómo funciona

### Flujo automático (Lunes y Viernes 14:00)

1. **APScheduler** detecta la hora y dispara `send_encuesta()`
2. El bot llama a Evolution API para **poner el grupo en modo announcement** (solo admins pueden escribir — los votos de poll igual pasan)
3. El bot envía una **poll nativa** vía Evolution API:
   - Lunes → "¿Jugás el miércoles a las 21hs en La Masia?"
   - Viernes → "¿Jugás el domingo a las 20hs en La Masia?"
4. Evolution API retorna el `message_id` y el `messageSecret` de la poll → se guardan en SQLite
5. Los jugadores tocan **SI** o **NO** en la poll
6. Cada voto llega al bot como `messages.upsert` con `messageType: pollUpdateMessage` (encriptado con AES-256-GCM)
7. El bot desencripta el voto usando el `messageSecret` guardado
8. El bot **borra la lista anterior** del grupo y publica una nueva con confirmados actualizados
9. Al llegar a N confirmados (`REOPEN_PLAYERS`, default 12), el bot **reabre el grupo** (`not_announcement`)

### Desencriptación de votos (Evolution API v2.3.7)

En v2.3.7 los votos llegan encriptados en `messages.upsert`. El algoritmo (tomado de Baileys):

```
key0    = HMAC-SHA256(key=zeros(32),   data=messageSecret)
sign    = pollMsgId + pollCreatorJid + voterJid + "Poll Vote" + 0x01
dec_key = HMAC-SHA256(key=key0,        data=sign)
aad     = pollMsgId + "\0" + voterJid
voto    = AES-256-GCM.decrypt(encPayload, dec_key, encIv, aad)
```

El resultado es un protobuf con los hashes SHA-256 de las opciones seleccionadas.

### Comandos del grupo (solo desde el número del bot)

El bot detecta mensajes propios (`fromMe: true`) en el grupo que empiecen con `!` y los borra automáticamente:

| Comando | Descripción |
|---|---|
| `!votacion` | Envía una encuesta para hoy |
| `!votacion Pregunta custom \| 14` | Encuesta con pregunta y titular custom (separados por `\|`) |
| `!lista` | Muestra la lista actual de confirmados |
| `!cerrar` | Pone el grupo en modo announcement manualmente |
| `!abrir` | Reabre el grupo (modo not_announcement) |

---

## Estructura del proyecto

```
futbol-manager/
├── bot_whatsapp/
│   ├── pyproject.toml              # Dependencias (uv)
│   ├── .env.example                # Variables de entorno de ejemplo
│   ├── src/bot_whatsapp/
│   │   ├── __main__.py             # FastAPI app, entry point, lifespan
│   │   ├── client.py               # Cliente REST de Evolution API
│   │   ├── handlers.py             # Lógica: encuestas, votos, lista, comandos
│   │   ├── scheduler.py            # APScheduler (L/V 14:00)
│   │   └── database.py             # SQLite: partidos + votos
│   └── data/
│       └── futbol.db               # Generado automáticamente al iniciar
│
├── mcp_futbol/
│   ├── pyproject.toml
│   ├── .env.example
│   └── src/mcp_futbol/
│       ├── __main__.py             # Entry point MCP
│       └── server.py               # FastMCP tools: crear_encuesta, ver_partidos
│
├── evolution/
│   └── docker-compose.yml          # Evolution API + PostgreSQL + Redis
│
├── collection/
│   └── FutBOT/                     # Colección Bruno para testear manualmente
│       ├── Crear webhook.bru
│       ├── Enviar encuesta.bru
│       └── ver votos de poll.bru
│
└── data/
    └── futbol.db                   # SQLite compartido
```

### Descripción de cada módulo

**`client.py`** — wrapper sobre la API REST de Evolution API:
- `send_poll()` → envía la poll y retorna `(message_id, message_secret)`
- `send_text()` → envía texto al grupo, retorna `message_id`
- `delete_message()` → borra un mensaje previo
- `set_group_announcement()` → cambia permisos del grupo

**`handlers.py`** — lógica central:
- `handle_webhook()` → router: despacha `messages.upsert` y `messages.update`
- `_handle_poll_vote()` → desencripta el voto y lo guarda en SQLite
- `_handle_command()` → interpreta `!votacion`, `!lista`, etc.
- `send_encuesta()` → cierra el grupo, envía poll, guarda en DB
- `_schedule_refresh()` → timer de 10–20 s para actualizar la lista (debounce)
- `_refresh_lista()` → borra lista anterior, publica nueva, reabre grupo si llena

**`database.py`** — SQLite con dos tablas:
- `partidos` — una fila por encuesta (fecha, poll_id, message_secret, titulares, pregunta, lista_message_id)
- `votos` — una fila por jugador×partido (player_jid, player_name, respuesta SI/NO, timestamps)

**`scheduler.py`** — APScheduler con dos jobs cron:
- Lunes 14:00 → encuesta para el miércoles a las 21hs
- Viernes 14:00 → encuesta para el domingo a las 20hs

**`server.py` (MCP)** — expone dos tools a Claude:
- `crear_encuesta(dia)` → `POST /encuesta` al bot
- `ver_partidos()` → `GET /partidos` al bot

---

## Setup desde cero

### Prerequisitos

- [Docker Desktop](https://www.docker.com/products/docker-desktop/)
- [uv](https://docs.astral.sh/uv/) → `brew install uv`
- Python 3.11+
- El número de WhatsApp del bot debe ser **admin del grupo**

### 1. Clonar el repo

```bash
git clone git@github.com:MezeLaw/futbol-manager.git
cd futbol-manager
```

### 2. Levantar Evolution API

```bash
cd evolution
docker compose up -d
```

Verificar que esté corriendo (puede tardar ~30 s en iniciar):

```bash
curl http://localhost:8080
```

> El `docker-compose.yml` levanta tres contenedores: `evolution-api` (puerto 8080), `postgres` (5432) y `redis` (6379). Los datos persisten en volúmenes Docker.

### 3. Conectar WhatsApp

1. Abrir **http://localhost:8080/manager** en el navegador
2. API Key: `changeme_api_key` (definida en `docker-compose.yml` → `AUTHENTICATION_API_KEY`)
3. Crear una instancia llamada **`futbol-bot`**
4. Hacer clic en "Connect" y escanear el QR con WhatsApp:
   - WhatsApp → ⋮ → Dispositivos vinculados → Vincular dispositivo

### 4. Obtener el Group JID

En el manager de Evolution API (http://localhost:8080/manager), buscar el grupo en los chats de la instancia. El JID tiene el formato `120363XXXXXXXXXX@g.us`.

Alternativamente, enviar cualquier mensaje al grupo y verlo en los logs del bot (aparece en `remoteJid`).

### 5. Configurar el bot

```bash
cd bot_whatsapp
cp .env.example .env
```

Editar `.env`:

```env
EVOLUTION_API_URL=http://localhost:8080
EVOLUTION_API_KEY=changeme_api_key
EVOLUTION_INSTANCE=futbol-bot

# JID del grupo (obtenido en el paso anterior)
GROUP_JID=120363XXXXXXXXXX@g.us

# Condición para reabrir el grupo después de la encuesta:
# "players" = al llegar a N confirmados | "timer" = después de N horas (no implementado aún)
REOPEN_MODE=players
REOPEN_PLAYERS=12

TZ=America/Argentina/Buenos_Aires
```

### 6. Levantar el bot

```bash
cd bot_whatsapp
uv sync
uv run bot-whatsapp
```

El bot queda escuchando en `http://localhost:8000`. Se ve en los logs:
```
INFO  Scheduler iniciado (L/V 14:00 America/Argentina/Buenos_Aires)
INFO  Application startup complete.
```

### 7. Configurar el webhook en Evolution API

El webhook conecta Evolution API con el bot. Hay dos opciones:

**Opción A — Desde el manager (interfaz web):**
- Ir a http://localhost:8080/manager → instancia `futbol-bot` → Webhook
- URL: `http://host.docker.internal:8000/webhook` _(desde Docker Desktop en Mac/Windows usar `host.docker.internal` en lugar de `localhost`)_
- Activar eventos: `MESSAGES_UPSERT` y `MESSAGES_UPDATE`

**Opción B — Con curl:**
```bash
curl -X POST http://localhost:8080/webhook/set/futbol-bot \
  -H "apikey: changeme_api_key" \
  -H "Content-Type: application/json" \
  -d '{
    "webhook": {
      "url": "http://host.docker.internal:8000/webhook",
      "enabled": true,
      "events": ["MESSAGES_UPSERT", "MESSAGES_UPDATE"]
    }
  }'
```

**Opción C — Colección Bruno** (carpeta `collection/FutBOT/`):
- Abrir con [Bruno](https://www.usebruno.com/) y ejecutar "Crear webhook"

> **Nota importante:** Los dos eventos son necesarios. `MESSAGES_UPSERT` trae los votos encriptados de la poll. `MESSAGES_UPDATE` es un fallback para otros escenarios de actualización.

### 8. Verificar el flujo completo

Enviar `!votacion` al grupo desde el número del bot. Debería:
1. Borrar el mensaje del comando
2. Cerrar el grupo (modo announcement)
3. Publicar la poll en el grupo

Al votar en la poll, debería aparecer la lista actualizada en el grupo.

### 9. (Opcional) MCP custom para Claude

```bash
cd mcp_futbol
cp .env.example .env
uv sync
```

El `.env` del MCP solo necesita:
```env
BOT_API_URL=http://localhost:8000
```

Registrar el MCP en Claude Desktop (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "futbol": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/ruta/absoluta/a/futbol-manager/mcp_futbol",
        "mcp-futbol"
      ]
    }
  }
}
```

Reiniciar Claude Desktop. Desde Claude se puede pedir: _"Enviá la encuesta para el próximo partido"_.

---

## Variables de entorno

### `bot_whatsapp/.env`

| Variable | Descripción | Ejemplo |
|---|---|---|
| `EVOLUTION_API_URL` | URL base de Evolution API | `http://localhost:8080` |
| `EVOLUTION_API_KEY` | API key (debe coincidir con `AUTHENTICATION_API_KEY` del docker-compose) | `changeme_api_key` |
| `EVOLUTION_INSTANCE` | Nombre de la instancia creada en el manager | `futbol-bot` |
| `GROUP_JID` | JID del grupo de WhatsApp | `120363XXXXXXXXXX@g.us` |
| `REOPEN_MODE` | Condición de reapertura del grupo | `players` o `timer` |
| `REOPEN_PLAYERS` | Nro. de confirmados para reabrir (modo `players`) | `12` |
| `REOPEN_TIMER_HOURS` | Horas para reabrir (modo `timer`, reservado) | `4` |
| `TZ` | Timezone del scheduler | `America/Argentina/Buenos_Aires` |

### `mcp_futbol/.env`

| Variable | Descripción | Ejemplo |
|---|---|---|
| `BOT_API_URL` | URL del bot FastAPI | `http://localhost:8000` |

---

## Endpoints del bot

| Método | Path | Descripción |
|---|---|---|
| `POST` | `/webhook` | Recibe eventos de Evolution API |
| `POST` | `/encuesta` | Dispara una encuesta. Body: `{"fecha": "2026-05-07"}` (opcional) |
| `GET` | `/partidos` | Lista los últimos 10 partidos con confirmados y suplentes |

---

## MCP tools

| Tool | Argumento | Descripción |
|---|---|---|
| `crear_encuesta` | `dia`: `"proximo"` o `"YYYY-MM-DD"` | Envía una poll al grupo para la fecha indicada |
| `ver_partidos` | — | Lista los últimos partidos con cantidad de confirmados |

---

## Base de datos

SQLite en `data/futbol.db`. Se crea automáticamente al iniciar el bot.

**Tabla `partidos`**

| Columna | Tipo | Descripción |
|---|---|---|
| `id` | INTEGER PK | ID autoincremental |
| `fecha` | DATE | Fecha del partido |
| `poll_message_id` | TEXT | ID del mensaje de la poll en WhatsApp |
| `lista_message_id` | TEXT | ID del último mensaje de lista publicado |
| `message_secret` | BLOB | Clave para desencriptar los votos (AES-256-GCM) |
| `titulares` | INTEGER | Cuántos jugadores entran como titulares |
| `question` | TEXT | Pregunta de la poll |
| `created_at` | TIMESTAMP | Timestamp de creación |

**Tabla `votos`**

| Columna | Tipo | Descripción |
|---|---|---|
| `id` | INTEGER PK | ID autoincremental |
| `partido_id` | INTEGER FK | Referencia a `partidos.id` |
| `player_jid` | TEXT | JID del jugador en WhatsApp |
| `player_name` | TEXT | Nombre push del jugador |
| `respuesta` | TEXT | `SI` o `NO` |
| `last_si_at` | TIMESTAMP | Última vez que votó SI (define orden en la lista) |
| `updated_at` | TIMESTAMP | Última actualización del voto |

> Un jugador puede cambiar su voto: el registro se actualiza con `UPSERT`. El orden en la lista se determina por `last_si_at` (quien confirmó primero queda en los primeros puestos).

---

## Deployment en producción

### Oracle Cloud Free Tier (recomendado, $0/mes permanente)

- VM ARM Ampere A1: 1 OCPU, 6 GB RAM, 50 GB disco — gratuita sin vencimiento

**Pasos:**
1. Crear cuenta en Oracle Cloud y provisionar una VM Ubuntu ARM
2. Abrir puertos en el firewall: 8080 (Evolution API), 8000 (bot, solo si se accede externamente)
3. Instalar Docker: `curl -fsSL https://get.docker.com | sh`
4. Instalar uv: `curl -LsSf https://astral.sh/uv/install.sh | sh`
5. Clonar el repo y seguir los pasos de setup
6. En el webhook, usar la IP pública de la VM en lugar de `localhost`
7. Para mantener el bot corriendo: usar `systemd` o `screen`/`tmux`

**Systemd para el bot:**
```ini
# /etc/systemd/system/futbol-bot.service
[Unit]
Description=Futbol Bot
After=network.target

[Service]
WorkingDirectory=/home/ubuntu/futbol-manager/bot_whatsapp
ExecStart=/home/ubuntu/.local/bin/uv run bot-whatsapp
Restart=always
User=ubuntu

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable futbol-bot
sudo systemctl start futbol-bot
```

### Alternativa: AWS Free Tier

- EC2 t2.micro gratis por 12 meses, luego ~$9/mes (migrar a Oracle tras el primer año)

---

## Troubleshooting

**Los votos no se registran**
- Verificar que el webhook esté configurado con los eventos `MESSAGES_UPSERT` (no solo `MESSAGES_UPDATE`)
- Verificar que el `messageSecret` se guardó correctamente al enviar la poll (ver logs: `Encuesta enviada. partido_id=...`)
- La URL del webhook desde Docker Desktop debe ser `host.docker.internal`, no `localhost`

**El bot no responde a `!votacion`**
- Los comandos solo funcionan si el mensaje es `fromMe: true` (enviado desde el mismo número vinculado a la instancia) y en un grupo (`@g.us`)
- Verificar que `GROUP_JID` en `.env` coincide con el JID real del grupo

**La poll aparece pero la lista no se actualiza**
- Ver logs del bot: `Voto registrado: ...` y `Refresh programado en ...`
- El refresh tiene un debounce de 10–20 s después del último voto

**Error de conexión a Evolution API**
- Verificar que los contenedores Docker están corriendo: `docker compose ps`
- Verificar que `EVOLUTION_API_KEY` en `.env` coincide con `AUTHENTICATION_API_KEY` en `docker-compose.yml`
- Verificar que `EVOLUTION_INSTANCE` coincide con el nombre de la instancia creada en el manager
