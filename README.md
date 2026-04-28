# futbol-manager

Bot de WhatsApp que envía encuestas de asistencia al grupo de fútbol los lunes y viernes a las 14:00, registra los votos y publica la lista actualizada en tiempo real.

## Cómo funciona

```
[APScheduler L/V 14:00]
        ↓
[Bot Python :8000] ──── envía poll ────► [Evolution API Docker :8080] ──► WhatsApp grupo
        ↑                                           │
        └──────── webhook messages.update ──────────┘
        ↓
    [SQLite]
```

1. El scheduler dispara los lunes y viernes a las 14:00 (hora Argentina)
2. El bot pone el grupo en modo _announcement_ (solo admins escriben) y envía una poll nativa: "¿Jugás el miércoles/domingo?"
3. Los jugadores votan tocando SI o NO — los votos llegan al bot vía webhook `messages.update`
4. Por cada voto, el bot borra la lista anterior y publica una nueva con los confirmados actualizados
5. Al llegar a 12 confirmados (configurable), el grupo se reabre automáticamente

También existe un **MCP custom** (`mcp_futbol`) para disparar encuestas manualmente desde Claude.

---

## Requisitos

- Docker Desktop
- [uv](https://docs.astral.sh/uv/) — `brew install uv`
- Python 3.11+

---

## Setup local

### 1. Levantar Evolution API

```bash
cd evolution
docker compose up -d
```

Verificá que esté corriendo:

```bash
curl http://localhost:8080
```

### 2. Conectar WhatsApp

1. Abrí **http://localhost:8080/manager** en el navegador
2. API Key: `changeme_api_key`
3. Conectá la instancia `futbol-bot` y escaneá el QR con tu WhatsApp  
   _(WhatsApp → Dispositivos vinculados → Vincular dispositivo)_

### 3. Configurar el bot

```bash
cd bot_whatsapp
cp .env.example .env
```

Editá `.env` con tus valores:

| Variable | Descripción |
|---|---|
| `EVOLUTION_API_URL` | `http://localhost:8080` |
| `EVOLUTION_API_KEY` | `changeme_api_key` |
| `EVOLUTION_INSTANCE` | `futbol-bot` |
| `GROUP_JID` | JID del grupo (obtenelo desde el manager de Evolution API) |
| `REOPEN_MODE` | `players` o `timer` |
| `REOPEN_PLAYERS` | Cantidad de confirmados para reabrir el grupo (default: 12) |
| `REOPEN_TIMER_HOURS` | Horas para reabrir si usás modo `timer` (default: 4) |

### 4. Levantar el bot

```bash
cd bot_whatsapp
uv sync
uv run bot-whatsapp
```

El bot queda escuchando en `http://localhost:8000`.

### 5. Configurar el webhook en Evolution API

Desde el manager (`http://localhost:8080/manager`), configurá el webhook de la instancia `futbol-bot` apuntando a:

```
http://localhost:8000/webhook
```

Evento a habilitar: `messages.update`

### 6. (Opcional) MCP custom

```bash
cd mcp_futbol
cp .env.example .env
uv sync
```

Registrá el MCP en tu cliente de Claude agregando esto a la config:

```json
{
  "mcpServers": {
    "futbol": {
      "command": "uv",
      "args": ["run", "--directory", "/ruta/a/futbol-manager/mcp_futbol", "mcp-futbol"]
    }
  }
}
```

---

## Estructura

```
futbol-manager/
├── bot_whatsapp/               # Bot principal
│   ├── src/bot_whatsapp/
│   │   ├── __main__.py         # FastAPI app + entry point
│   │   ├── client.py           # Cliente Evolution API REST
│   │   ├── handlers.py         # Lógica de encuestas y votos
│   │   ├── scheduler.py        # APScheduler L/V 14:00
│   │   └── database.py         # SQLite (partidos + votos)
│   └── data/                   # futbol.db (generado al iniciar)
│
├── mcp_futbol/                 # MCP custom para Claude
│   └── src/mcp_futbol/
│       └── server.py           # Tools: crear_encuesta(), ver_partidos()
│
└── evolution/                  # Evolution API
    └── docker-compose.yml      # Evolution API + PostgreSQL
```

## Endpoints del bot

| Método | Path | Descripción |
|---|---|---|
| `POST` | `/webhook` | Recibe eventos de Evolution API |
| `POST` | `/encuesta` | Dispara una encuesta manualmente (`{"fecha": "2026-04-30"}`) |
| `GET` | `/partidos` | Lista partidos con confirmados |

## MCP tools

| Tool | Descripción |
|---|---|
| `crear_encuesta(dia)` | Envía una poll al grupo. `dia` puede ser `"proximo"` o una fecha `YYYY-MM-DD` |
| `ver_partidos()` | Lista los últimos partidos y sus confirmados |
