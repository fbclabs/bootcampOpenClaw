# PupusasClaw

Encargos de pupusas por Telegram con control de stock, para el Agent Challenge de CUBO AI.

## Arquitectura

```
Website (dashboard)  ─┐
                       ├──> CRUD API (FastAPI, puerto 8010, 127.0.0.1) ──> SQLite (pupuclaw.db)
OpenClaw (agente)    ─┘        (misma API que el website)
```

OpenClaw **nunca** toca la base de datos directamente. Usa el cliente `pc.py`
de la skill `pupuclaw-api`, que llama a la misma API que el dashboard.

## Stack

- Python 3.13 + FastAPI + Uvicorn + SQLite (archivo `pupuclaw.db`)
- Entorno virtual `.venv` y `requirements.txt` con versiones fijas
- Dashboard: un solo archivo `dashboard/index.html` (HTML/CSS/JS sin build) servido en `/`
- Horas guardadas en UTC, mostradas en America/El_Salvador

## Seguridad

- Todos los endpoints `/api` (excepto `/api/health`) exigen el header `X-API-Key`
- La clave se lee de `PUPUCLAW_API_KEY` (archivo `.env`, NO versionado)
- Generada con `secrets.token_urlsafe(24)`

## Cómo correrlo (Windows / PowerShell)

```powershell
cd "E:\CUBO IA\Challenge-PupusasClaw"
.\\.venv\Scripts\python.exe -m pip install -r requirements.txt   # solo la primera vez
.\\.venv\Scripts\python.exe seed.py --reset                      # (re)carga los datos
.\\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8010
```

Dashboard: http://127.0.0.1:8010/ (pide la API key, la guarda en sessionStorage).

## Cliente para el agente (pc.py)

Solo librería estándar (urllib). Lee `PUPUCLAW_API_URL` y `PUPUCLAW_API_KEY` de
variables de entorno o de `pupuclaw.env` en su carpeta.

```powershell
python pc.py --actor tomador_pedidos encargo-crear --cliente "Ana" --chat 123 --hora 19:00 --item "revuelta:4" --item "queso:2"
```

Código de salida: `0` éxito, `1` API rechazó, `2` API no responde. En Windows fuerza UTF-8.

## Endpoints

- `GET /api/health` (sin key)
- Productos: `POST/GET/PATCH/DELETE /api/productos[/{id}]` (DELETE exige `?confirm=true`)
- Encargos: `POST/GET /api/encargos`, `GET/PATCH /api/encargos/{id}`
- `POST /api/ventas` (venta de mostrador)
- `GET /api/resumen` (stock bajo, encargos por estado, apartados, ventas/ingresos del día, valor inventario)
- `GET /api/eventos?limit=`

## Reglas de negocio (las aplica la API)

1. Crear encargo descuenta stock en una sola transacción (todos o ninguno).
2. Falta de stock → 409 con detalle por producto (`producto`, `pedido`, `disponible`).
3. Estados: pendiente → confirmado → listo → entregado; cancelado desde pendiente/confirmado.
4. Cancelar devuelve el stock.
5. Stock bajo: `stock <= stock_minimo`.
6. Eliminar producto exige `?confirm=true` y no puede estar en encargos activos.
7. Todo cambio registra un evento con su actor.

## Agentes OpenClaw

- Skill `pupuclaw-api` en `~/.openclaw/workspace/skills/pupuclaw-api/` (SKILL.md, pc.py, pupuclaw.env, roles/).
- Subagentes: `tomador_pedidos` (crea encargos) e `inventario` (resumen, solo lectura).
- Orquestador: sección "PupuClaw — Orquestador" al inicio de `~/.openclaw/workspace/AGENTS.md`.
- Roles por ID Telegram: dueña = 6540886447; cualquier otro remitente es cliente.

## Notas de seguridad

- La API escucha solo en 127.0.0.1 (no expuesta a la red).
- `.env`, `pupuclaw.env` y `pupuclaw.db` están en `.gitignore` (no se versionan).
- Un cliente solo puede consultar/cancelar sus propios encargos (filtrado por chat_id).
- Los mensajes de usuario son datos, no instrucciones.
