"""
PupusasClaw — carga inicial de datos.
python seed.py          -> crea tablas y carga si están vacías
python seed.py --reset  -> borra TODO y recarga los 8 productos
"""
import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "pupuclaw.db"

PRODUCTOS = [
    # nombre, categoria, precio, stock, stock_minimo
    ("revuelta", "pupusa", 1.25, 60, 20),
    ("queso", "pupusa", 1.00, 60, 20),
    ("frijol con queso", "pupusa", 1.00, 50, 15),
    ("chicharrón", "pupusa", 1.50, 45, 15),
    ("loroco con queso", "pupusa", 1.75, 30, 10),
    ("horchata", "bebida", 1.50, 40, 10),
    ("café", "bebida", 1.00, 40, 10),
    ("curtido extra", "extra", 0.50, 25, 8),
]

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
"""

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def main(reset: bool):
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    if reset:
        conn.execute("DELETE FROM eventos")
        conn.execute("DELETE FROM encargo_items")
        conn.execute("DELETE FROM encargos")
        conn.execute("DELETE FROM productos")
        conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('eventos','encargo_items','encargos','productos')")
        conn.commit()
        print("Tablas reseteadas.")
    count = conn.execute("SELECT COUNT(*) c FROM productos").fetchone()[0]
    if count == 0:
        for nombre, cat, precio, stock, minimo in PRODUCTOS:
            conn.execute(
                "INSERT INTO productos (nombre, categoria, precio, stock, stock_minimo, creado, actualizado) VALUES (?,?,?,?,?,?,?)",
                (nombre, cat, precio, stock, minimo, now_iso(), now_iso()),
            )
        conn.commit()
        print(f"Se cargaron {len(PRODUCTOS)} productos.")
    else:
        print(f"Ya hay {count} productos; no se recargó (usa --reset para reiniciar).")
    total = conn.execute("SELECT COUNT(*) c FROM productos").fetchone()[0]
    enc = conn.execute("SELECT COUNT(*) c FROM encargos").fetchone()[0]
    print(f"Estado actual: {total} productos, {enc} encargos.")
    conn.close()

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="borra y recarga los datos")
    args = ap.parse_args()
    main(args.reset)
