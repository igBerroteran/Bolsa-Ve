"""Plataforma local de la BVC: datos en vivo + paper trading (dinero simulado).

    py -3 servidor.py        -> abre http://127.0.0.1:8765

Solo escucha en 127.0.0.1 (tu PC). Uso privado: la BVC prohíbe redifundir sus datos.
Recolecta cada 5 min en horario de mercado (L-V 8:25-13:20 hora de Caracas).
"""
from __future__ import annotations

import bisect
import json
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import bvc

PUERTO = 8765
INTERVALO = 300
CON = bvc.conectar()
LOCK = threading.Lock()
ESTADO = {"ultima": None, "resultado": None}


def q(sql: str, args=()) -> list[dict]:
    with LOCK:
        return [dict(r) for r in CON.execute(sql, args).fetchall()]


def cfg(clave: str) -> float:
    return float(q("SELECT valor FROM config WHERE clave=?", (clave,))[0]["valor"])


FRED_LEGALVE = Path(__file__).resolve().parent.parent / "Legal Venezuela" / "web" / "tasas_usd.json"
_FRED: dict[str, float] | None = None


def _fred() -> dict[str, float]:
    """Tasa oficial mensual (FRED) del proyecto Legal Venezuela, en Bs sin reconversión -> Bs.D."""
    global _FRED
    if _FRED is None:
        try:
            meses = json.loads(FRED_LEGALVE.read_text(encoding="utf-8"))["meses"]
            _FRED = {m: v / 1e14 for m, v in meses.items() if m >= "2021-10"}
        except (OSError, ValueError, KeyError):
            _FRED = {}
    return _FRED


class Tasas:
    """Tasa Bs/USD para cualquier fecha: último valor publicado en o antes de esa fecha."""

    def __init__(self, fuente: str):
        filas = q("SELECT fecha, usd FROM tasas WHERE fuente=? ORDER BY fecha", (fuente,))
        self.fechas = [r["fecha"] for r in filas]
        self.valores = [r["usd"] for r in filas]
        self.fuente = fuente

    def en(self, fecha: str) -> float | None:
        i = bisect.bisect_right(self.fechas, fecha[:10]) - 1
        if i >= 0:
            return self.valores[i]
        # antes del primer dato diario: BCV usa el mensual de FRED; el paralelo no se inventa
        return _fred().get(fecha[:7]) if self.fuente == "bcv" else None

    @property
    def desde(self) -> str | None:
        return self.fechas[0] if self.fechas else None


def tasa() -> dict:
    out = {}
    for f in ("bcv", "paralelo"):
        r = q("SELECT fecha, usd FROM tasas WHERE fuente=? ORDER BY fecha DESC LIMIT 1", (f,))
        out[f] = r[0] if r else None
    out["usd"] = out["bcv"]["usd"] if out["bcv"] else None  # compatibilidad
    out["fecha"] = out["bcv"]["fecha"] if out["bcv"] else None
    return out


def mercado() -> list[dict]:
    precios = q("""SELECT p.* FROM precios p JOIN (SELECT simbolo, MAX(rowid) m FROM precios
                   GROUP BY simbolo) u ON p.rowid = u.m""")
    libro = {r["simbolo"]: r for r in q("""SELECT l.* FROM libro l JOIN (SELECT simbolo,
             MAX(rowid) m FROM libro GROUP BY simbolo) u ON l.rowid = u.m""")}
    por = {p["simbolo"]: p for p in precios}
    for s, l in libro.items():  # símbolos con ofertas pero sin operaciones hoy
        por.setdefault(s, {"simbolo": s, "nombre": l["nombre"], "precio": None, "var_rel": None,
                           "var_abs": None, "volumen": 0, "monto": 0, "hora": None, "fecha": None})
    salida = []
    for s, p in sorted(por.items()):
        l = libro.get(s, {})
        p.update(bid=l.get("bid"), bid_vol=l.get("bid_vol"), ask=l.get("ask"), ask_vol=l.get("ask_vol"),
                 nombre_largo=l.get("nombre") or p.get("nombre"))
        salida.append(p)
    return salida


def historial(simb: str) -> list[dict]:
    return q("""SELECT p.fecha, p.precio, p.volumen FROM precios p JOIN (SELECT fecha, MAX(rowid) m
                FROM precios WHERE simbolo=? GROUP BY fecha) u ON p.rowid = u.m ORDER BY p.fecha""",
             (simb,))


INDICES = {"IBC": "IBC", "FIN": "Índice Financiero", "IND": "Índice Industrial"}


def _ts(iso: str) -> int:
    """Hora de Caracas tratada como UTC: el gráfico muestra la hora local de la bolsa."""
    from datetime import datetime, timezone
    return int(datetime.fromisoformat(iso[:19]).replace(tzinfo=timezone.utc).timestamp())


def _agrupar(velas: list[dict], clave) -> list[dict]:
    out: list[dict] = []
    for v in velas:
        k = clave(v)
        if out and out[-1]["_k"] == k:
            o = out[-1]
            o["high"] = max(o["high"], v["high"])
            o["low"] = min(o["low"], v["low"])
            o["close"] = v["close"]
            o["volume"] = (o["volume"] or 0) + (v["volume"] or 0)
        else:
            out.append({**v, "_k": k})
    for o in out:
        o.pop("_k")
    return out


def velas(simb: str, tf: str, moneda: str = "bs") -> dict:
    """tf: S1/S5/S15 = última sesión en velas de 1/5/15 min; D, W, M = diario/semanal/mensual.
    moneda: bs | bcv | paralelo (cada vela se convierte con la tasa de SU día)."""
    t = Tasas(moneda) if moneda in ("bcv", "paralelo") else None

    def convertir(lista: list[dict], fecha_fija: str | None = None) -> list[dict]:
        if not t:
            return lista
        out = []
        for v in lista:
            k = t.en(fecha_fija or v["time"])
            if k:
                out.append({**v, "open": v["open"] / k, "high": v["high"] / k, "low": v["low"] / k,
                            "close": v["close"] / k})
        return out

    r = _velas_bs(simb, tf, convertir)
    r["moneda"] = moneda
    r["tasa_desde"] = t.desde if t else None
    return r


def _velas_bs(simb: str, tf: str, convertir) -> dict:
    from datetime import date
    if tf in ("S1", "S5", "S15"):
        tf = tf[1:]
        if simb in INDICES:
            pts = q("SELECT momento m, valor p FROM indices_intradia WHERE indice=? ORDER BY momento", (simb,))
        else:
            pts = q("SELECT momento m, precio p FROM ticks WHERE simbolo=? ORDER BY momento", (simb,))
        ultima = max((r["m"][:10] for r in pts), default=None)  # solo la última sesión
        pts = [r for r in pts if r["m"][:10] == ultima]
        paso = int(tf) * 60
        out: list[dict] = []
        for r in pts:
            if r["m"][11:13] < "08":  # el primer tick viene con hora 01:00 (referencia), se omite
                continue
            t = _ts(r["m"]) // paso * paso
            if out and out[-1]["time"] == t:
                o = out[-1]
                o["high"], o["low"], o["close"] = max(o["high"], r["p"]), min(o["low"], r["p"]), r["p"]
            else:
                out.append({"time": t, "open": r["p"], "high": r["p"], "low": r["p"], "close": r["p"],
                            "volume": None})
        return {"velas": convertir(out, ultima), "intradia": True, "aproximadas": False,
                "fecha_sesion": ultima}

    if simb in INDICES:
        filas = [{"fecha": r["fecha"], "apertura": None, "maximo": None, "minimo": None,
                  "cierre": r["valor"], "volumen": None, "real": 0}
                 for r in q("SELECT fecha, valor FROM indices WHERE indice=? ORDER BY fecha", (simb,))]
    else:
        filas = q("SELECT * FROM diario WHERE simbolo=? ORDER BY fecha", (simb,))
    out, previo, aprox = [], None, 0
    for r in filas:
        c = r["cierre"]
        o = r["apertura"] if r["real"] and r["apertura"] else (previo if previo is not None else c)
        h = r["maximo"] if r["real"] and r["maximo"] else max(o, c)
        lo = r["minimo"] if r["real"] and r["minimo"] else min(o, c)
        aprox += 0 if r["real"] else 1
        out.append({"time": r["fecha"], "open": o, "high": max(h, o, c), "low": min(lo, o, c),
                    "close": c, "volume": r["volumen"]})
        previo = c
    out = convertir(out)  # tasa de cada día, antes de agrupar en semanas/meses
    if tf == "W":
        out = _agrupar(out, lambda v: date.fromisoformat(v["time"]).isocalendar()[:2])
    elif tf == "M":
        out = _agrupar(out, lambda v: v["time"][:7])
    return {"velas": out, "intradia": False, "aproximadas": aprox > 0}


def detalle(simb: str) -> dict:
    r = q("SELECT json, capturado FROM detalle WHERE simbolo=?", (simb,))
    if not r:
        return {}
    d = json.loads(r[0]["json"])
    lib = (d.get("cur_con_lib_ord_rv") or [{}])[0]
    niveles = [{"bid": bvc.num(lib.get(f"PRE_CMP_{i}")), "bid_vol": bvc.num(lib.get(f"VOL_CMP_{i}")),
                "ask": bvc.num(lib.get(f"PRE_VTA_{i}")), "ask_vol": bvc.num(lib.get(f"VOL_VTA_{i}"))}
               for i in range(1, 7)]
    enc = (d.get("cur_encab_simb_rv") or [{}])[0]
    pv = (d.get("cur_precio_var_rv") or [{}])[0]
    cap = (d.get("cur_cap_simb_rv") or [{}])[0]
    return {"nombre": enc.get("DESC_EMP"), "isin": enc.get("COD_ISIN"), "acciones": bvc.num(enc.get("ACC_CIRC")),
            "cap_usd": bvc.num(cap.get("CAPITALI_US")), "max_ano": bvc.num(pv.get("PRECIO_MAX_ANO")),
            "min_ano": bvc.num(pv.get("PRECIO_MIN_ANO")), "apertura": bvc.num(pv.get("PRECIO_APERT")),
            "max": bvc.num(pv.get("PRECIO_MAX")), "min": bvc.num(pv.get("PRECIO_MIN")),
            "medio": bvc.num(pv.get("PRECIO_MED")), "libro": niveles,
            "dividendos": d.get("cur_ult_benef_otr_rv") or [], "capturado": r[0]["capturado"]}


def indices() -> dict:
    out = {}
    for r in q("SELECT fecha, indice, valor FROM indices ORDER BY fecha"):
        out.setdefault(r["indice"], []).append({"t": r["fecha"], "v": r["valor"]})
    return out


def precio_ejecucion(simb: str, lado: str) -> float | None:
    m = {x["simbolo"]: x for x in mercado()}.get(simb)
    if not m:
        return None
    # Compra al mejor precio de venta (ask); vende al mejor de compra (bid). Si no hay, último precio.
    return (m["ask"] if lado == "compra" else m["bid"]) or m["precio"]


def cartera() -> dict:
    ordenes = q("SELECT * FROM ordenes ORDER BY id DESC")
    efectivo = cfg("capital_inicial")
    pos: dict[str, dict] = {}
    for o in reversed(ordenes):
        bruto = o["cantidad"] * o["precio"]
        p = pos.setdefault(o["simbolo"], {"simbolo": o["simbolo"], "cantidad": 0.0, "costo": 0.0})
        if o["lado"] == "compra":
            efectivo -= bruto + o["comision"]
            p["costo"] += bruto + o["comision"]
            p["cantidad"] += o["cantidad"]
        else:
            prom = p["costo"] / p["cantidad"] if p["cantidad"] else 0
            efectivo += bruto - o["comision"]
            p["costo"] -= prom * o["cantidad"]
            p["cantidad"] -= o["cantidad"]
    precios = {m["simbolo"]: (m["bid"] or m["precio"] or 0) for m in mercado()}
    posiciones, valor = [], 0.0
    for p in pos.values():
        if p["cantidad"] <= 0:
            continue
        p["precio_actual"] = precios.get(p["simbolo"], 0)
        p["valor"] = p["cantidad"] * p["precio_actual"]
        p["ganancia"] = p["valor"] - p["costo"]
        p["ganancia_pct"] = p["ganancia"] / p["costo"] * 100 if p["costo"] else 0
        valor += p["valor"]
        posiciones.append(p)
    total = efectivo + valor
    ini = cfg("capital_inicial")
    return {"efectivo": efectivo, "valor_posiciones": valor, "total": total, "capital_inicial": ini,
            "rendimiento_pct": (total - ini) / ini * 100, "comision": cfg("comision"),
            "posiciones": posiciones, "ordenes": ordenes[:50]}


def ordenar(simb: str, lado: str, cantidad: float) -> dict:
    if lado not in ("compra", "venta") or cantidad <= 0 or cantidad != int(cantidad):
        return {"error": "Orden inválida: lado compra/venta y cantidad entera > 0."}
    precio = precio_ejecucion(simb, lado)
    if not precio:
        return {"error": f"No hay precio para {simb} ({'nadie vende' if lado == 'compra' else 'nadie compra'})."}
    c = cartera()
    comision = cantidad * precio * c["comision"]
    if lado == "compra" and cantidad * precio + comision > c["efectivo"]:
        return {"error": f"Efectivo insuficiente: necesitas Bs {cantidad * precio + comision:,.2f}."}
    if lado == "venta":
        tengo = next((p["cantidad"] for p in c["posiciones"] if p["simbolo"] == simb), 0)
        if cantidad > tengo:
            return {"error": f"Solo tienes {tengo:g} acciones de {simb}."}
    with LOCK:
        CON.execute("INSERT INTO ordenes (momento, simbolo, lado, cantidad, precio, comision) "
                    "VALUES (?,?,?,?,?,?)", (bvc.ahora().isoformat(timespec="seconds"), simb, lado,
                                             cantidad, precio, comision))
        CON.commit()
    return {"ok": True, "precio": precio, "comision": comision}


def reiniciar(capital: float) -> dict:
    with LOCK:
        CON.execute("DELETE FROM ordenes")
        CON.execute("UPDATE config SET valor=? WHERE clave='capital_inicial'", (str(capital),))
        CON.commit()
    return {"ok": True}


def refrescar(todo: bool) -> dict:
    con = bvc.conectar()  # conexión propia: la red no bloquea las consultas de la web
    try:
        res = bvc.actualizar(con, todo=todo)
    finally:
        con.close()
    ESTADO.update(ultima=bvc.ahora().isoformat(timespec="seconds"), resultado=res)
    return res


def recolector() -> None:
    refrescar(True)
    ultimo_dia = bvc.ahora().date()
    while True:
        time.sleep(INTERVALO)
        hoy = bvc.ahora().date()
        if bvc.mercado_abierto():
            refrescar(todo=hoy != ultimo_dia)
            ultimo_dia = hoy


class Manejador(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, codigo=200):
        cuerpo = json.dumps(obj, ensure_ascii=False, default=str).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def do_GET(self):
        u = urlparse(self.path)
        a = {k: v[0] for k, v in parse_qs(u.query).items()}
        rutas = {
            "/api/mercado": lambda: {"filas": mercado(), "tasa": tasa(), "estado": ESTADO,
                                     "abierto": bvc.mercado_abierto()},
            "/api/indices": indices,
            "/api/historial": lambda: historial(a.get("simb", "")),
            "/api/velas": lambda: velas(a.get("simb", ""), a.get("tf", "D"), a.get("moneda", "bs")),
            "/api/detalle": lambda: detalle(a.get("simb", "")),
            "/api/cartera": cartera,
        }
        if u.path in rutas:
            return self._json(rutas[u.path]())
        if u.path in ("/", "/index.html"):
            cuerpo = (Path(__file__).parent / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            return self.wfile.write(cuerpo)
        if u.path == "/demo.js":
            cuerpo = (Path(__file__).parent / "demo.js").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/javascript; charset=utf-8")
            self.end_headers()
            return self.wfile.write(cuerpo)
        if u.path == "/favicon.ico":
            self.send_response(204)
            return self.end_headers()
        self._json({"error": "no existe"}, 404)

    def do_POST(self):
        largo = int(self.headers.get("Content-Length") or 0)
        datos = json.loads(self.rfile.read(largo) or b"{}")
        if self.path == "/api/orden":
            return self._json(ordenar(str(datos.get("simbolo", "")), str(datos.get("lado", "")),
                                      float(datos.get("cantidad") or 0)))
        if self.path == "/api/actualizar":
            threading.Thread(target=refrescar, args=(True,), daemon=True).start()
            return self._json({"ok": "actualizando en segundo plano (~2 min)"})
        if self.path == "/api/reiniciar":
            return self._json(reiniciar(float(datos.get("capital") or 100000)))
        self._json({"error": "no existe"}, 404)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    threading.Thread(target=recolector, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", PUERTO), Manejador)
    url = f"http://127.0.0.1:{PUERTO}"
    print(f"Plataforma BVC en {url}  (Ctrl+C para salir)")
    if "--sin-navegador" not in sys.argv:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
