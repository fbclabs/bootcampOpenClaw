"""
PupusasClaw — API de control de stock y encargos de pupusas.
FastAPI + SQLite (pupuclaw.db). Esta API es la ÚNICA vía de acceso a la base de datos.
OpenClaw y el dashboard usan esta misma API; nadie toca la BD directamente.
"""
import json
import os
import re
import sqlite3
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Union, List

from fastapi import FastAPI, Header, HTTPException, Query, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, field_validator

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "pupuclaw.db"
DASHBOARD_PATH = BASE_DIR / "dashboard" / "index.html"

# ---------------- Carga de .env ----------------
def _load_env() -> None:
    env_file = BASE_DIR / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

_load_env()
API_KEY = os.environ.get("PUPUCLAW_API_KEY", "")

# ---------------- Constantes ----------------
CATEGORIAS = {"pupusa", "bebida", "extra"}
ESTADOS = {"pendiente", "confirmado", "listo", "entregado", "cancelado"}
ESTADOS_ACTIVOS = {"pendiente", "confirmado", "listo"}
TRANSICIONES = {
    "pendiente": {"confirmado", "cancelado"},
    "confirmado": {"listo", "cancelado"},
    "listo": {"entregado"},
    "entregado": set(),
    "cancelado": set(),
}
TZ_SV = timezone(timedelta(hours=-6))  # America/El_Salvador (sin DST)

SCHEMA = """
CREATE TABLE IF NOT EXISTS productos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL UNIQUE,
    categoria TEXT NOT NULL,
    precio REAL NOT NULL,
    stock INTEGER NOT NULL DEFAULT 0,
    stock_minimo INTEGER NOT NULL DEFAULT 0,
    creado TEXT NOT NULL,
    actualizado TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS encargos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cliente TEXT NOT NULL,
    chat_id TEXT,
    hora_entrega TEXT,
    estado TEXT NOT NULL DEFAULT 'pendiente',
    total REAL NOT NULL DEFAULT 0,
    nota TEXT,
    creado TEXT NOT NULL,
    actualizado TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS encargo_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    encargo_id INTEGER NOT NULL,
    producto_id INTEGER NOT NULL,
    cantidad INTEGER NOT NULL,
    precio_unitario REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS eventos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entidad TEXT,
    entidad_id INTEGER,
    accion TEXT NOT NULL,
    actor TEXT NOT NULL DEFAULT 'dashboard',
    detalle TEXT,
    fecha TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_encargo_items_encargo ON encargo_items(encargo_id);
CREATE INDEX IF NOT EXISTS idx_eventos_fecha ON eventos(fecha);
"""

# ---------------- Utilidades ----------------
def norm(s: str) -> str:
    """Normaliza: minúsculas y sin tildes."""
    s = unicodedata.normalize("NFD", str(s))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.lower().strip()

def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def sv_today_range() -> "tuple[str, str]":
    now_sv = datetime.now(TZ_SV)
    start_sv = now_sv.replace(hour=0, minute=0, second=0, microsecond=0)
    end_sv = start_sv + timedelta(days=1)
    return start_sv.astimezone(timezone.utc).isoformat(), end_sv.astimezone(timezone.utc).isoformat()

@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db() -> None:
    with db() as conn:
        conn.executescript(SCHEMA)

def log_evento(conn, entidad, entidad_id, accion, actor, detalle=None) -> None:
    det = json.dumps(detalle, ensure_ascii=False) if isinstance(detalle, (dict, list)) else detalle
    conn.execute(
        "INSERT INTO eventos (entidad, entidad_id, accion, actor, detalle, fecha) VALUES (?,?,?,?,?,?)",
        (entidad, entidad_id, accion, actor, det, utcnow_iso()),
    )

def find_producto(conn, ref):
    """Busca producto por id (int) o nombre (str, sin distinguir mayúsculas ni tildes)."""
    if isinstance(ref, int) or (isinstance(ref, str) and ref.strip().isdigit()):
        row = conn.execute("SELECT * FROM productos WHERE id = ?", (int(ref),)).fetchone()
        if row:
            return row
    n = norm(ref)
    for r in conn.execute("SELECT * FROM productos").fetchall():
        if norm(r["nombre"]) == n:
            return r
    return None

def http409(payload):
    return JSONResponse(status_code=409, content=payload)

# ---------------- Auth ----------------
def get_auth(
    x_api_key: Optional[str] = Header(default=None),
    x_actor: Optional[str] = Header(default=None),
):
    if not API_KEY:
        raise HTTPException(status_code=500, detail="PUPUCLAW_API_KEY no configurada en el servidor")
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="X-API-Key inválida o ausente")
    actor = (x_actor or "dashboard").strip() or "dashboard"
    return actor

app = FastAPI(title="PupusasClaw API", version="1.0.0")

@app.on_event("startup")
def _startup():
    init_db()

# ---------------- Modelos ----------------
class ProductoCreate(BaseModel):
    nombre: str = Field(min_length=1)
    categoria: str
    precio: float = Field(gt=0)
    stock: int = Field(default=0, ge=0)
    stock_minimo: int = Field(default=0, ge=0)

    @field_validator("categoria")
    @classmethod
    def _cat(cls, v):
        if v not in CATEGORIAS:
            raise ValueError(f"categoria inválida (esperado: {sorted(CATEGORIAS)})")
        return v

class ProductoPatch(BaseModel):
    nombre: Optional[str] = None
    categoria: Optional[str] = None
    precio: Optional[float] = None
    stock: Optional[int] = None
    stock_minimo: Optional[int] = None

    @field_validator("categoria")
    @classmethod
    def _cat(cls, v):
        if v is not None and v not in CATEGORIAS:
            raise ValueError(f"categoria inválida (esperado: {sorted(CATEGORIAS)})")
        return v
    @field_validator("precio")
    @classmethod
    def _precio(cls, v):
        if v is not None and v <= 0:
            raise ValueError("precio debe ser > 0")
        return v
    @field_validator("stock", "stock_minimo")
    @classmethod
    def _ge0(cls, v):
        if v is not None and v < 0:
            raise ValueError("no puede ser negativo")
        return v

class EncargoItemIn(BaseModel):
    producto: Union[str, int]
    cantidad: int = Field(gt=0)

class EncargoCreate(BaseModel):
    cliente: str = Field(min_length=1)
    chat_id: Optional[str] = None
    hora_entrega: Optional[str] = None
    items: List[EncargoItemIn] = Field(min_length=1)
    nota: Optional[str] = None

    @field_validator("hora_entrega")
    @classmethod
    def _hora(cls, v):
        if v is not None and not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", v):
            raise ValueError("hora_entrega debe tener formato HH:MM")
        return v

class EstadoPatch(BaseModel):
    estado: str

class VentaCreate(BaseModel):
    producto: Union[str, int]
    cantidad: int = Field(gt=0)

# ---------------- Raíz y health ----------------
@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def root():
    if DASHBOARD_PATH.exists():
        return HTMLResponse(DASHBOARD_PATH.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Dashboard no encontrado</h1>")

@app.get("/api/health")
def health():
    return {"status": "ok", "service": "pupuclaw", "time": utcnow_iso()}

# ---------------- Productos ----------------
@app.post("/api/productos", status_code=201)
def crear_producto(body: ProductoCreate, actor: str = Depends(get_auth)):
    with db() as conn:
        if find_producto(conn, body.nombre):
            return http409({"error": "ya existe un producto con ese nombre", "nombre": body.nombre})
        now = utcnow_iso()
        cur = conn.execute(
            "INSERT INTO productos (nombre, categoria, precio, stock, stock_minimo, creado, actualizado) VALUES (?,?,?,?,?,?,?)",
            (body.nombre.strip(), body.categoria, round(body.precio, 2), body.stock, body.stock_minimo, now, now),
        )
        pid = cur.lastrowid
        log_evento(conn, "producto", pid, "crear", actor, {"nombre": body.nombre, "categoria": body.categoria, "precio": round(body.precio, 2), "stock": body.stock})
    return {"id": pid, "nombre": body.nombre, "categoria": body.categoria, "precio": round(body.precio, 2), "stock": body.stock, "stock_minimo": body.stock_minimo}

@app.get("/api/productos")
def listar_productos(
    categoria: Optional[str] = Query(default=None),
    bajo_stock: Optional[bool] = Query(default=None),
    q: Optional[str] = Query(default=None),
    actor: str = Depends(get_auth),
):
    sql = "SELECT * FROM productos WHERE 1=1"
    args = []
    if categoria:
        sql += " AND categoria = ?"
        args.append(categoria)
    if bajo_stock:
        sql += " AND stock <= stock_minimo"
    sql += " ORDER BY categoria, nombre"
    with db() as conn:
        rows = conn.execute(sql, args).fetchall()
        out = [dict(r) for r in rows]
        if q:
            n = norm(q)
            out = [r for r in out if n in norm(r["nombre"]) or n in norm(r["categoria"])]
    return out

@app.get("/api/productos/{producto_id}")
def obtener_producto(producto_id: int, actor: str = Depends(get_auth)):
    with db() as conn:
        r = conn.execute("SELECT * FROM productos WHERE id = ?", (producto_id,)).fetchone()
        if not r:
            raise HTTPException(status_code=404, detail="Producto no encontrado")
    return dict(r)

@app.patch("/api/productos/{producto_id}")
def actualizar_producto(producto_id: int, body: ProductoPatch, actor: str = Depends(get_auth)):
    with db() as conn:
        r = conn.execute("SELECT * FROM productos WHERE id = ?", (producto_id,)).fetchone()
        if not r:
            raise HTTPException(status_code=404, detail="Producto no encontrado")
        cambios = {}
        data = body.model_dump(exclude_unset=True)
        if "nombre" in data:
            n = data["nombre"].strip()
            if find_producto(conn, n) and norm(n) != norm(r["nombre"]):
                return http409({"error": "ya existe un producto con ese nombre", "nombre": n})
            cambios["nombre"] = n
        for k in ("categoria", "precio", "stock", "stock_minimo"):
            if k in data and data[k] is not None:
                cambios[k] = data[k]
        if not cambios:
            return dict(r)
        cambios["actualizado"] = utcnow_iso()
        sets = ", ".join(f"{k} = ?" for k in cambios)
        vals = list(cambios.values()) + [producto_id]
        conn.execute(f"UPDATE productos SET {sets} WHERE id = ?", vals)
        log_evento(conn, "producto", producto_id, "actualizar", actor, {"antes": dict(r), "cambios": cambios})
        nuevo = conn.execute("SELECT * FROM productos WHERE id = ?", (producto_id,)).fetchone()
    return dict(nuevo)

@app.delete("/api/productos/{producto_id}")
def eliminar_producto(producto_id: int, confirm: bool = Query(default=False), actor: str = Depends(get_auth)):
    with db() as conn:
        r = conn.execute("SELECT * FROM productos WHERE id = ?", (producto_id,)).fetchone()
        if not r:
            raise HTTPException(status_code=404, detail="Producto no encontrado")
        if not confirm:
            return http409({"error": "se requiere ?confirm=true para eliminar", "producto": dict(r)})
        activos = conn.execute(
            "SELECT COUNT(*) c FROM encargo_items ei JOIN encargos e ON e.id = ei.encargo_id WHERE ei.producto_id = ? AND e.estado IN ('pendiente','confirmado','listo')",
            (producto_id,),
        ).fetchone()["c"]
        if activos > 0:
            return http409({"error": "el producto está en encargos activos y no se puede eliminar", "producto": dict(r)})
        conn.execute("DELETE FROM productos WHERE id = ?", (producto_id,))
        log_evento(conn, "producto", producto_id, "eliminar", actor, {"producto": dict(r)})
    return {"ok": True, "eliminado": dict(r)}

# ---------------- Encargos ----------------
@app.post("/api/encargos", status_code=201)
def crear_encargo(body: EncargoCreate, actor: str = Depends(get_auth)):
    with db() as conn:
        # Agregar cantidades por producto (mismo producto puede repetirse en items)
        agg: "dict[int, int]" = {}
        detalles: "list[dict]" = []
        for item in body.items:
            p = find_producto(conn, item.producto)
            if not p:
                raise HTTPException(status_code=404, detail=f"Producto no encontrado: {item.producto}")
            agg[p["id"]] = agg.get(p["id"], 0) + item.cantidad
            detalles.append({"producto": p["nombre"], "producto_id": p["id"], "cantidad": item.cantidad, "precio_unitario": p["precio"]})
        # Verificar stock de todos ANTES de descontar
        faltantes = []
        for pid, qty in agg.items():
            p = conn.execute("SELECT * FROM productos WHERE id = ?", (pid,)).fetchone()
            if p["stock"] < qty:
                faltantes.append({"producto": p["nombre"], "pedido": qty, "disponible": p["stock"]})
        if faltantes:
            return http409(faltantes)
        # Transacción atómica (el contextmanager db() hace commit/rollback): se descuentan todos o ninguno
        for pid, qty in agg.items():
            conn.execute("UPDATE productos SET stock = stock - ?, actualizado = ? WHERE id = ?", (qty, utcnow_iso(), pid))
        now = utcnow_iso()
        total = round(sum(d["precio_unitario"] * d["cantidad"] for d in detalles), 2)
        cur = conn.execute(
            "INSERT INTO encargos (cliente, chat_id, hora_entrega, estado, total, nota, creado, actualizado) VALUES (?,?,?,?,?,?,?,?)",
            (body.cliente.strip(), body.chat_id, body.hora_entrega, "pendiente", total, body.nota, now, now),
        )
        enc_id = cur.lastrowid
        for d in detalles:
            conn.execute(
                "INSERT INTO encargo_items (encargo_id, producto_id, cantidad, precio_unitario) VALUES (?,?,?,?)",
                (enc_id, d["producto_id"], d["cantidad"], d["precio_unitario"]),
            )
        log_evento(conn, "encargo", enc_id, "crear", actor, {"cliente": body.cliente, "chat_id": body.chat_id, "total": total, "items": detalles})
        enc = conn.execute("SELECT * FROM encargos WHERE id = ?", (enc_id,)).fetchone()
        items = conn.execute("SELECT * FROM encargo_items WHERE encargo_id = ?", (enc_id,)).fetchall()
    return {"encargo": dict(enc), "items": [dict(i) for i in items]}

def _encargo_detail(conn, enc_id):
    enc = conn.execute("SELECT * FROM encargos WHERE id = ?", (enc_id,)).fetchone()
    if not enc:
        return None
    items = conn.execute(
        "SELECT ei.id, ei.encargo_id, ei.producto_id, ei.cantidad, ei.precio_unitario, p.nombre AS producto "
        "FROM encargo_items ei LEFT JOIN productos p ON p.id = ei.producto_id WHERE ei.encargo_id = ?",
        (enc_id,),
    ).fetchall()
    eventos = conn.execute("SELECT * FROM eventos WHERE entidad = 'encargo' AND entidad_id = ? ORDER BY id", (enc_id,)).fetchall()
    return {"encargo": dict(enc), "items": [dict(i) for i in items], "eventos": [dict(e) for e in eventos]}

@app.get("/api/encargos")
def listar_encargos(
    estado: Optional[str] = Query(default=None),
    chat_id: Optional[str] = Query(default=None),
    hoy: Optional[bool] = Query(default=None),
    actor: str = Depends(get_auth),
):
    sql = "SELECT * FROM encargos WHERE 1=1"
    args = []
    if estado:
        sql += " AND estado = ?"
        args.append(estado)
    if chat_id:
        sql += " AND chat_id = ?"
        args.append(chat_id)
    if hoy:
        start, end = sv_today_range()
        sql += " AND creado >= ? AND creado < ?"
        args.extend([start, end])
    sql += " ORDER BY id DESC"
    with db() as conn:
        rows = conn.execute(sql, args).fetchall()
        out = []
        for r in rows:
            items = conn.execute("SELECT COUNT(*) c, SUM(cantidad) total FROM encargo_items WHERE encargo_id = ?", (r["id"],)).fetchone()
            d = dict(r)
            d["cantidad_items"] = items["total"] or 0
            out.append(d)
    return out

@app.get("/api/encargos/{encargo_id}")
def obtener_encargo(encargo_id: int, actor: str = Depends(get_auth)):
    with db() as conn:
        det = _encargo_detail(conn, encargo_id)
        if not det:
            raise HTTPException(status_code=404, detail="Encargo no encontrado")
    return det

@app.patch("/api/encargos/{encargo_id}")
def cambiar_estado(encargo_id: int, body: EstadoPatch, actor: str = Depends(get_auth)):
    nuevo = body.estado
    if nuevo not in ESTADOS:
        raise HTTPException(status_code=422, detail=f"estado inválido (esperado: {sorted(ESTADOS)})")
    with db() as conn:
        enc = conn.execute("SELECT * FROM encargos WHERE id = ?", (encargo_id,)).fetchone()
        if not enc:
            raise HTTPException(status_code=404, detail="Encargo no encontrado")
        actual = enc["estado"]
        if nuevo not in TRANSICIONES.get(actual, set()):
            return http409({"estado_actual": actual, "estados_permitidos": sorted(TRANSICIONES.get(actual, set()))})
        # Cancelar devuelve el stock de todos sus items
        if nuevo == "cancelado":
            items = conn.execute("SELECT * FROM encargo_items WHERE encargo_id = ?", (encargo_id,)).fetchall()
            for it in items:
                conn.execute("UPDATE productos SET stock = stock + ?, actualizado = ? WHERE id = ?", (it["cantidad"], utcnow_iso(), it["producto_id"]))
        conn.execute("UPDATE encargos SET estado = ?, actualizado = ? WHERE id = ?", (nuevo, utcnow_iso(), encargo_id))
        log_evento(conn, "encargo", encargo_id, f"estado_{nuevo}", actor, {"desde": actual, "hacia": nuevo})
        det = _encargo_detail(conn, encargo_id)
    return det

# ---------------- Ventas de mostrador ----------------
@app.post("/api/ventas", status_code=201)
def crear_venta(body: VentaCreate, actor: str = Depends(get_auth)):
    with db() as conn:
        p = find_producto(conn, body.producto)
        if not p:
            raise HTTPException(status_code=404, detail=f"Producto no encontrado: {body.producto}")
        if p["stock"] < body.cantidad:
            return http409({"producto": p["nombre"], "pedido": body.cantidad, "disponible": p["stock"]})
        conn.execute("UPDATE productos SET stock = stock - ?, actualizado = ? WHERE id = ?", (body.cantidad, utcnow_iso(), p["id"]))
        ingreso = round(p["precio"] * body.cantidad, 2)
        log_evento(conn, "venta", None, "venta", actor, {"producto": p["nombre"], "cantidad": body.cantidad, "precio_unitario": p["precio"], "ingreso": ingreso})
    return {"ok": True, "producto": p["nombre"], "cantidad": body.cantidad, "ingreso": ingreso}

# ---------------- Resumen ----------------
@app.get("/api/resumen")
def resumen(actor: str = Depends(get_auth)):
    with db() as conn:
        stock_bajo = [dict(r) for r in conn.execute("SELECT * FROM productos WHERE stock <= stock_minimo ORDER BY stock ASC").fetchall()]
        por_estado = {s: 0 for s in ESTADOS}
        for r in conn.execute("SELECT estado, COUNT(*) c FROM encargos GROUP BY estado").fetchall():
            por_estado[r["estado"]] = r["c"]
        apartados = {}
        rows = conn.execute(
            "SELECT p.nombre AS nombre, SUM(ei.cantidad) AS c "
            "FROM encargo_items ei JOIN encargos e ON e.id = ei.encargo_id JOIN productos p ON p.id = ei.producto_id "
            "WHERE e.estado IN ('pendiente','confirmado','listo') GROUP BY p.id"
        ).fetchall()
        apartados = {r["nombre"]: r["c"] for r in rows}
        start, end = sv_today_range()
        ventas_hoy = 0
        unidades_hoy = 0
        ingresos_hoy = 0.0
        for r in conn.execute("SELECT detalle FROM eventos WHERE accion = 'venta' AND fecha >= ? AND fecha < ?", (start, end)).fetchall():
            d = json.loads(r["detalle"]) if r["detalle"] else {}
            ventas_hoy += 1
            unidades_hoy += int(d.get("cantidad", 0))
            ingresos_hoy += float(d.get("ingreso", 0))
        inventario_valor = conn.execute("SELECT COALESCE(SUM(stock * precio), 0) v FROM productos").fetchone()["v"]
    return {
        "stock_bajo": stock_bajo,
        "encargos_por_estado": por_estado,
        "unidades_apartadas": apartados,
        "ventas_hoy": ventas_hoy,
        "unidades_vendidas_hoy": unidades_hoy,
        "ingresos_hoy": round(ingresos_hoy, 2),
        "valor_inventario": round(inventario_valor, 2),
    }

# ---------------- Eventos ----------------
@app.get("/api/eventos")
def listar_eventos(limit: int = Query(default=50, ge=1, le=500), actor: str = Depends(get_auth)):
    with db() as conn:
        rows = conn.execute("SELECT * FROM eventos ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]

if __name__ == "__main__":
    import uvicorn
    init_db()
    uvicorn.run(app, host="127.0.0.1", port=8010, log_level="info")
