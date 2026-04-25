---
Plan: Sistema de Votación de Fútbol por WhatsApp
1. Objetivos del Proyecto
Objetivo A: Crear un MCP custom para experimentar
Objetivo B: Bot de WhatsApp que envíe encuestas los lunes y viernes a las 14:00 al grupo
Objetivo C: Los jugadores votan con polls nativas y reciben la lista actualizada
Objetivo D: El usuario puede crear votaciones custom via MCP
---
2. Stack Tecnológico
Componente | Lenguaje | Detalle
MCP | Python 3.11+ | FastMCP
WhatsApp | Python | Evolution API (self-hosted) + SDK evolutionapi
DB | SQLite | persistencia local
Scheduler | Python | APScheduler
Deployment | Oracle Cloud Free Tier | VM ARM gratuita permanente (o AWS Free Tier)
---
3. Estructura del Proyecto
futbol-manager/
├── mcp_futbol/                    # Tu MCP custom
│   ├── pyproject.toml           # uv + dependencias
│   ├── src/mcp_futbol/
│   │   ├── __init__.py
│   │   ├── __main__.py      # Entry point (uv run)
│   │   └── server.py      # Definición de tools
│   └── .env.example        # Variables de entorno
│
├── bot_whatsapp/               # Bot de WhatsApp
│   ├── pyproject.toml
│   ├── src/bot_whatsapp/
│   │   ├── __init__.py
│   │   ├── __main__.py       # Entry point
│   │   ├── client.py       # Cliente Evolution API
│   │   ├── handlers.py   # Webhook handling + votes
│   │   ├── scheduler.py   # Lógica schedule (L/V 14:00)
│   │   └── database.py   # SQLite + modelos
│   ├── .env.example
│   └── data/                # SQLite DB
│
├── evolution/                  # Evolution API (Docker Compose)
│   └── docker-compose.yml
│
└── README.md
---
4. Flujos del Sistema
Flujo A: Scheduler Automático (Lunes/Viernes 14:00)
1. APScheduler detecta que es L/V + 14:00
2. Bot setea grupo → announcement (solo admins/bot pueden escribir)
3. Bot envía poll nativa al grupo vía Evolution API
   - Question: "¿Juegas el [miércoles/domingo]?"
   - Options: ["SI", "NO"]
   - multiSelect: false
4. Poll se muestra en el grupo
5. Jugadores votan tocando opción (los votos no son mensajes, pasan igual)
6. Webhook recibe evento message_update con los votos
7. Bot parsea voters del evento
8. Bot calcula lista actualizada
9. Bot elimina mensaje anterior + reenvía con lista actualizada
10. Condición de reapertura (a definir en Fase 4):
    - Opción A: X horas después → bot setea grupo → not_announcement
    - Opción B: al llegar a 12 confirmados → bot setea grupo → not_announcement

Flujo B: Interacción vía MCP
1. Vos ejecutás el MCP o lo activás desde tu cliente
2. Llamás tool: crear_encuesta(dia, pregunta)
3. Bot setea grupo → announcement
4. Bot envía poll al grupo inmediatamente
5. El resto del flujo igual que A

Flujo C: Votación del Jugador
1. Jugador ve poll en el grupo
2. Toca "SI" o "NO"
3. Evolution API envía webhook message_update
4. Bot procesa voto (actualiza DB)
5. Bot elimina poll anterior + envia lista actualizada
6. Si se cumple condición de reapertura → bot setea grupo → not_announcement
---
5. Herramientas del MCP
Tool | Descripción
crear_encuesta(dia, pregunta) | Envía poll al grupo para el día indicado
ver_partidos() | Lista partidos y confirmados
---
6. Configuración de Evolution API
1. Cloná el repo: git clone https://github.com/EvolutionAPI/evolution-api
2. Levantá con Docker Compose en tu servidor
3. Accedé al dashboard y creá una instancia
4. Escaneá QR con tu número de WhatsApp
5. Obtené API Key del dashboard
6. Obtené el Group JID de tu grupo
7. Configurá webhook hacia tu bot (http://localhost:8000/webhook)

API Endpoint base:
POST http://{tu-servidor}:8080/message/sendPoll/{instancia}
Headers: apikey: {API_KEY}

Enviar poll:
{
  "number": "GRUPO_JID@g.us",
  "pollMessage": {
    "name": "¿Juegas el miércoles?",
    "selectableCount": 1,
    "values": ["SI", "NO"]
  }
}

Webhook message_update (evento de voto):
{
  "event": "messages.update",
  "data": {
    "key": { "remoteJid": "GRUPO_JID@g.us", "id": "MSG_ID" },
    "update": {
      "pollUpdates": [
        {
          "pollUpdateMessageKey": { "participant": "5491112345678@s.whatsapp.net" },
          "vote": { "selectedOptions": ["SI"] }
        }
      ]
    }
  }
}

Nota: Evolution API no emite un evento poll.results dedicado — los votos llegan
en messages.update con el campo pollUpdates. El handler debe parsear este campo.

Eliminar mensaje:
DELETE http://{tu-servidor}:8080/chat/deleteMessageForEveryone/{instancia}
{
  "id": "MSG_ID",
  "remoteJid": "GRUPO_JID@g.us",
  "fromMe": true
}
---
7. Conversión de Teléfono a JID
Formato phone: +5491112345678 (internacional)
Formato JID: 5491112345678@s.whatsapp.net

Función needed:
def phone_to_jid(phone: str) -> str:
    # +5491112345678 -> 5491112345678@s.whatsapp.net
    return phone.replace("+", "").replace(" ", "").replace("-", "") + "@s.whatsapp.net"
---
8. Consideraciones Importantes
WhatsApp no permite edición de mensajes:
- Solución: Delete mensaje anterior + re-send nuevo
- Guardar message_id para saber cuál borrar

Polls nativas:
- Evolution API soporta polls vía sendPoll endpoint
- Los votos llegan en evento messages.update (campo pollUpdates)
- Se puede tracking quién votó qué

Permisos de grupo:
- El número del bot debe ser admin del grupo
- Antes de enviar la poll: PUT grupo en announcement (solo admins escriben)
- Después: reabrir con not_announcement según condición (a definir Fase 4)
  - Opción A: timer X horas (APScheduler)
  - Opción B: trigger al llegar a N confirmados (N=12 por ahora)
- Los votos de poll no cuentan como mensajes → funcionan igual en modo announcement

Scheduler:
- APScheduler corre en el bot Python, Evolution API no tiene scheduler propio
- Cron: lunes y viernes a las 14:00 hora local

Privacidad:
- Al self-hostear, ningún tercero ve los números ni los votos del grupo
- Todos los datos quedan en tu infraestructura

SDK Python:
- pip install evolutionapi (v0.1.2, oficial, Mayo 2025)
- Soporta WebSocket para recibir eventos en tiempo real
---
9. Deployment: Oracle Cloud Free Tier (Recomendado)

Oracle Cloud ofrece VMs ARM gratuitas permanentes (sin vencimiento):
- 1 OCPU (ARM Ampere A1)
- 6 GB RAM
- 50 GB disco
- Costo: $0/mes para siempre

Alternativa: AWS Free Tier
- EC2 t2.micro gratis 12 meses
- Luego ~$9/mes (o migrar a Oracle)

Arquitectura en el servidor:
[Bot Python :8000] ← webhook ← [Evolution API Docker :8080] ← WhatsApp
       ↓
    [SQLite]

Docker Compose para Evolution API:
version: "3.7"
services:
  evolution-api:
    image: atendai/evolution-api:latest
    ports:
      - "8080:8080"
    environment:
      - SERVER_URL=http://localhost:8080
      - AUTHENTICATION_API_KEY=tu_api_key_aqui
    restart: always
---
10. Pasos para Ejecutar
Fase 1: Setup Local (Día 1)
- [ ] Crear estructura de directorios
- [ ] Configurar pyproject.toml con uv
- [ ] Instalar dependencias (pip install evolutionapi fastapi uvicorn)
- [ ] Levantar Evolution API con Docker Compose local
- [ ] Escanear QR y verificar conexión
- [ ] Obtener Group JID
- [ ] Implementar database.py

Fase 2: Bot WhatsApp (Día 2)
- [ ] Implementar client.py (Evolution API REST)
- [ ] Implementar handlers.py (webhook messages.update + pollUpdates)
- [ ] Implementar scheduler.py
- [ ] Testear sending poll al grupo

Fase 3: MCP Custom (Día 3)
- [ ] Crear server.py con FastMCP
- [ ] Definir tool crear_encuesta()
- [ ] Testear MCP → Bot

Fase 4: Prueba Integral (Día 4)
- [ ] Testear scheduler local
- [ ] Testear voting flow completo
- [ ] Testear permisos de grupo (announcement / not_announcement)
- [ ] Definir y testear condición de reapertura (timer vs N jugadores)
- [ ] Testear MCP tools

Fase 5: Deployment en Oracle Cloud / AWS
- [ ] Crear VM en Oracle Cloud Free Tier (ARM A1)
- [ ] Instalar Docker en la VM
- [ ] Levantar Evolution API con Docker Compose
- [ ] Deploy bot Python (proceso systemd o Docker)
- [ ] Configurar webhook de Evolution API → bot
- [ ] Configurar firewall (abrir puerto 8080 Evolution, 8000 bot)
---
11. Costo Estimado
Servicio | Costo
Evolution API (self-hosted) | $0
Oracle Cloud Free Tier (VM) | $0/mes (permanente)
AWS Free Tier (alternativa) | $0/12 meses, luego ~$9/mes
Total | $0/mes
---
12. Resumen
Item | Valor
Stack | Python + FastMCP + Evolution API
Arquitectura | MCP + Bot + SQLite + Evolution API Docker
Scheduler | L/V 14:00
WhatsApp | Grupo con polls nativas
MCP Tools | crear_encuesta(), ver_partidos()
Deployment | Oracle Cloud Free Tier (ARM, $0 permanente)
Costo | $0/mes
Privacidad | Total — datos solo en tu infraestructura
