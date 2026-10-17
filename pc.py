#!/usr/bin/env python
"""
PupusasClaw — cliente de línea de comandos para el agente OpenClaw.
Usa SOLO la librería estándar (urllib). Habla con la API; nunca toca la BD directamente.

Lee PUPUCLAW_API_URL y PUPUCLAW_API_KEY de las variables de entorno o de un archivo
'pupuclaw.env' en la misma carpeta de este script.

Salida: JSON en stdout. Códigos de salida:
  0 = éxito
  1 = la API rechazó la petición (4xx/5xx)
  2 = la API no responde (error de conexión)
"""
import argparse
import io
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

# Forzar UTF-8 en Windows
if sys.platform == "win32":
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))

def _load_env_file():
    """Carga pupuclaw.env (formato CLAVE=VALOR) si existe, sin pisar variables ya definidas."""
    env_path = os.path.join(HERE, "pupuclaw.env")
    if os.path.exists(env_path):
        for line in open(env_path, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v

def _cfg():
    _load_env_file()
    url = os.environ.get("PUPUCLAW_API_URL", "http://127.0.0.1:8010").rstrip("/")
    key = os.environ.get("PUPUCLAW_API_KEY", "")
    return url, key

def request(method, path, body=None, params=None, actor="dashboard"):
    url, key = _cfg()
    full = url + path
    if params:
        qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        if qs:
            full += "?" + qs
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(full, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("X-API-Key", key)
    req.add_header("X-Actor", actor or "dashboard")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return 0, json.loads(raw)
            except json.JSONDecodeError:
                return 0, {"ok": True, "raw": raw}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"error": raw}
        return 1, {"http_status": e.code, "body": payload}
    except (urllib.error.URLError, OSError) as e:
        return 2, {"error": f"API no responde: {e}"}

def emit(code, obj):
    print(json.dumps(obj, ensure_ascii=False))
    sys.exit(code)

def fail(msg, code=1):
    emit(code, {"ok": False, "error": msg})

def main():
    ap = argparse.ArgumentParser(prog="pc.py", description="Cliente PupusasClaw")
    ap.add_argument("--actor", default="dashboard", help="actor para el evento (dashboard, orquestador, tomador_pedidos, inventario, duena)")
    ap.add_argument("--url", default=None, help="URL base de la API (sobreescribe PUPUCLAW_API_URL)")
    ap.add_argument("--key", default=None, help="API key (sobreescribe PUPUCLAW_API_KEY)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    # health
    sub.add_parser("health")

    # producto-crear
    p = sub.add_parser("producto-crear")
    p.add_argument("--nombre", required=True)
    p.add_argument("--categoria", required=True)
    p.add_argument("--precio", required=True, type=float)
    p.add_argument("--stock", required=True, type=int)
    p.add_argument("--stock-minimo", required=False, type=int, default=0)

    # producto-list
    p = sub.add_parser("producto-list")
    p.add_argument("--categoria", default=None)
    p.add_argument("--bajo-stock", action="store_true", default=None)
    p.add_argument("--q", default=None)

    # producto-get / patch / delete
    p = sub.add_parser("producto-get")
    p.add_argument("--id", required=True, type=int)
    p = sub.add_parser("producto-patch")
    p.add_argument("--id", required=True, type=int)
    p.add_argument("--nombre", default=None)
    p.add_argument("--categoria", default=None)
    p.add_argument("--precio", default=None, type=float)
    p.add_argument("--stock", default=None, type=int)
    p.add_argument("--stock-minimo", default=None, type=int)
    p = sub.add_parser("producto-delete")
    p.add_argument("--id", required=True, type=int)
    p.add_argument("--confirm", action="store_true")

    # encargo-crear
    p = sub.add_parser("encargo-crear")
    p.add_argument("--cliente", required=True)
    p.add_argument("--chat", required=True, dest="chat_id")
    p.add_argument("--hora", default=None, dest="hora_entrega")
    p.add_argument("--nota", default=None)
    p.add_argument("--item", action="append", required=True, help="producto:cantidad (repetible)")

    # encargo-list
    p = sub.add_parser("encargo-list")
    p.add_argument("--estado", default=None)
    p.add_argument("--chat", default=None, dest="chat_id")
    p.add_argument("--hoy", action="store_true", default=None)

    # encargo-get / estado
    p = sub.add_parser("encargo-get")
    p.add_argument("--id", required=True, type=int)
    p = sub.add_parser("encargo-estado")
    p.add_argument("--id", required=True, type=int)
    p.add_argument("--estado", required=True)

    # venta
    p = sub.add_parser("venta")
    p.add_argument("--producto", required=True)
    p.add_argument("--cantidad", required=True, type=int)

    # resumen / eventos
    sub.add_parser("resumen")
    p = sub.add_parser("eventos")
    p.add_argument("--limit", default=50, type=int)

    args = ap.parse_args()

    if args.url:
        os.environ["PUPUCLAW_API_URL"] = args.url
    if args.key:
        os.environ["PUPUCLAW_API_KEY"] = args.key

    actor = args.actor or "dashboard"
    cmd = args.cmd

    if cmd == "health":
        code, out = request("GET", "/api/health")
        return emit(code, out)

    if cmd == "producto-crear":
        body = {"nombre": args.nombre, "categoria": args.categoria, "precio": args.precio, "stock": args.stock, "stock_minimo": args.stock_minimo}
        code, out = request("POST", "/api/productos", body=body, actor=actor)
        return emit(code, out)

    if cmd == "producto-list":
        params = {}
        if args.categoria: params["categoria"] = args.categoria
        if args.bajo_stock is not None: params["bajo_stock"] = "true"
        if args.q: params["q"] = args.q
        code, out = request("GET", "/api/productos", params=params, actor=actor)
        return emit(code, out)

    if cmd == "producto-get":
        code, out = request("GET", f"/api/productos/{args.id}", actor=actor)
        return emit(code, out)

    if cmd == "producto-patch":
        body = {}
        for k in ("nombre", "categoria", "precio", "stock", "stock_minimo"):
            v = getattr(args, k)
            if v is not None:
                body[k] = v
        if not body:
            return fail("sin cambios que aplicar", 1)
        code, out = request("PATCH", f"/api/productos/{args.id}", body=body, actor=actor)
        return emit(code, out)

    if cmd == "producto-delete":
        params = {"confirm": "true"} if args.confirm else {}
        code, out = request("DELETE", f"/api/productos/{args.id}", params=params, actor=actor)
        return emit(code, out)

    if cmd == "encargo-crear":
        items = []
        for it in args.item:
            if ":" not in it:
                return fail(f"item inválido (esperado 'producto:cantidad'): {it}", 1)
            nombre, _, cant = it.rpartition(":")
            try:
                cantidad = int(cant)
            except ValueError:
                return fail(f"cantidad inválida en item '{it}'", 1)
            if cantidad <= 0:
                return fail(f"cantidad debe ser > 0 en item '{it}'", 1)
            items.append({"producto": nombre.strip(), "cantidad": cantidad})
        body = {"cliente": args.cliente, "chat_id": args.chat_id, "hora_entrega": args.hora_entrega, "items": items, "nota": args.nota}
        code, out = request("POST", "/api/encargos", body=body, actor=actor)
        return emit(code, out)

    if cmd == "encargo-list":
        params = {}
        if args.estado: params["estado"] = args.estado
        if args.chat_id: params["chat_id"] = args.chat_id
        if args.hoy is not None: params["hoy"] = "true"
        code, out = request("GET", "/api/encargos", params=params, actor=actor)
        return emit(code, out)

    if cmd == "encargo-get":
        code, out = request("GET", f"/api/encargos/{args.id}", actor=actor)
        return emit(code, out)

    if cmd == "encargo-estado":
        body = {"estado": args.estado}
        code, out = request("PATCH", f"/api/encargos/{args.id}", body=body, actor=actor)
        return emit(code, out)

    if cmd == "venta":
        body = {"producto": args.producto, "cantidad": args.cantidad}
        code, out = request("POST", "/api/ventas", body=body, actor=actor)
        return emit(code, out)

    if cmd == "resumen":
        code, out = request("GET", "/api/resumen", actor=actor)
        return emit(code, out)

    if cmd == "eventos":
        code, out = request("GET", "/api/eventos", params={"limit": args.limit}, actor=actor)
        return emit(code, out)

    return fail(f"comando desconocido: {cmd}", 1)

if __name__ == "__main__":
    main()
