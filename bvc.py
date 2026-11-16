"""Recolector de datos de la Bolsa de Valores de Caracas (BVC) + tasa BCV.

Fuentes (públicas, las mismas que usa la web de la BVC):
  admin-ajax.php?action=resumenMercadoRentaVariable  -> último precio, variación, volumen, monto
  admin-ajax.php?action=get_cotizaciones             -> mejor compra/venta (bid/ask)
  admin-ajax.php?action=chartsData                   -> índices IBC / Financiero / Industrial (1 año)
  bcv.org.ve                                         -> tasa oficial USD

USO PRIVADO: los términos de la BVC (cláusula 8) prohíben retransmitir o redifundir
su información. No publiques estos datos sin una licencia de la BVC.
"""
from __future__ import annotations

import json
import re
import sqlite3
import ssl
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
DB = BASE / "bvc.db"
AJAX = "https://www.bolsadecaracas.com/wp-admin/admin-ajax.php"
BCV = "https://www.bcv.org.ve/"
UA = "Mozilla/5.0 (uso personal; recolector BVC)"
CARACAS = timezone(timedelta(hours=-4))

ESQUEMA = """
CREATE TABLE IF NOT EXISTS precios (
  fecha TEXT, simbolo TEXT, nombre TEXT, precio REAL, var_abs REAL, var_rel REAL,
  volumen REAL, monto REAL, hora TEXT, capturado TEXT,
  UNIQUE(fecha, simbolo, hora, volumen));
CREATE TABLE IF NOT EXISTS libro (
  capturado TEXT, simbolo TEXT, nombre TEXT, bid REAL, bid_vol REAL, ask REAL, ask_vol REAL);
CREATE TABLE IF NOT EXISTS indices (
  fecha TEXT, indice TEXT, valor REAL, PRIMARY KEY(fecha, indice));
CREATE TABLE IF NOT EXISTS indices_intradia (
  momento TEXT, indice TEXT, valor REAL, PRIMARY KEY(momento, indice));
CREATE TABLE IF NOT EXISTS tasa_bcv (fecha TEXT PRIMARY KEY, usd REAL);
CREATE TABLE IF NOT EXISTS ordenes (
  id INTEGER PRIMARY KEY AUTOINCREMENT, momento TEXT, simbolo TEXT, lado TEXT,
  cantidad REAL, precio REAL, comision REAL);
CREATE TABLE IF NOT EXISTS config (clave TEXT PRIMARY KEY, valor TEXT);
CREATE TABLE IF NOT EXISTS diario (
  fecha TEXT, simbolo TEXT, apertura REAL, maximo REAL, minimo REAL, cierre REAL,
  volumen REAL, real INTEGER, PRIMARY KEY(fecha, simbolo));
CREATE TABLE IF NOT EXISTS ticks (
  momento TEXT, simbolo TEXT, precio REAL, PRIMARY KEY(momento, simbolo, precio));
CREATE TABLE IF NOT EXISTS detalle (simbolo TEXT PRIMARY KEY, json TEXT, capturado TEXT);
CREATE TABLE IF NOT EXISTS tasas (fecha TEXT, fuente TEXT, usd REAL, PRIMARY KEY(fecha, fuente));
CREATE INDEX IF NOT EXISTS i_precios ON precios(simbolo, fecha);
CREATE INDEX IF NOT EXISTS i_libro ON libro(simbolo, capturado);
"""


def conectar() -> sqlite3.Connection:
    con = sqlite3.connect(DB, timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript(ESQUEMA)
    con.execute("INSERT OR IGNORE INTO config VALUES ('capital_inicial', '100000')")
    con.execute("INSERT OR IGNORE INTO config VALUES ('comision', '0.01')")
    con.commit()
    return con


def ahora() -> datetime:
    return datetime.now(CARACAS)


def num(x) -> float | None:
    if x in (None, "", "null"):
        return None
    try:
        return float(str(x).replace(",", ""))
    except ValueError:
        return None


def _post(datos: dict) -> object:
    cuerpo = urllib.parse.urlencode(datos).encode()
    req = urllib.request.Request(AJAX, data=cuerpo, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.loads(r.read().decode("utf-8-sig"))


def fecha_mercado(con: sqlite3.Connection) -> str:
    """Día de la última sesión: hoy si el mercado está abierto; si no, el último día
    con datos intradía del IBC (cubre fines de semana y feriados)."""
    if mercado_abierto():
        return ahora().date().isoformat()
    r = con.execute("SELECT MAX(momento) FROM indices_intradia").fetchone()[0]
    if r:
        return r[:10]
    d = ahora().date()
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.isoformat()


def bajar_precios(con: sqlite3.Connection) -> int:
    filas = _post({"action": "resumenMercadoRentaVariable"})
    t = ahora()
    fecha = fecha_mercado(con)
    n = 0
    for f in filas or []:
        cur = con.execute(
            "INSERT OR IGNORE INTO precios VALUES (?,?,?,?,?,?,?,?,?,?)",
            (fecha, f["COD_SIMB"], f.get("DESC_SIMB"), num(f.get("PRECIO")),
             num(f.get("VAR_ABS")), num(f.get("VAR_REL")), num(f.get("VOLUMEN")),
             num(f.get("MONTO_EFECTIVO")), f.get("HORA"), t.isoformat(timespec="seconds")))
        n += cur.rowcount
    con.commit()
    return n


def bajar_libro(con: sqlite3.Connection) -> int:
    d = _post({"action": "get_cotizaciones"})
    filas = d.get("response", []) if isinstance(d, dict) else []
    t = ahora().isoformat(timespec="seconds")
    con.executemany("INSERT INTO libro VALUES (?,?,?,?,?,?,?)", [
        (t, f["COD_SIMB"], f.get("DESC_SIMB"), num(f.get("PRE_CMP_1")), num(f.get("VOL_CMP_1")),
         num(f.get("PRE_VTA_1")), num(f.get("VOL_VTA_1"))) for f in filas])
    con.commit()
    return len(filas)


def bajar_indices(con: sqlite3.Connection) -> int:
    d = _post({"action": "chartsData", "indice": "1", "variacion": "anual"})
    n = 0
    for clave, serie in (d or {}).items():
        m = re.match(r"CUR_(\w+?)(_ANUAL)?$", clave)
        if not m:
            continue
        indice, anual = m.group(1), bool(m.group(2))
        for p in serie:
            v = num(p.get("PRECIO"))
            if v is None:
                continue
            if anual:
                fecha = datetime.strptime(p["HORA"], "%d/%m/%Y").date().isoformat()
                con.execute("INSERT OR REPLACE INTO indices VALUES (?,?,?)", (fecha, indice, v))
            else:
                mom = datetime.strptime(p["HORA"], "%d/%m/%Y %H:%M:%S").isoformat()
                con.execute("INSERT OR REPLACE INTO indices_intradia VALUES (?,?,?)", (mom, indice, v))
            n += 1
    con.commit()
    return n


def bajar_tasa(con: sqlite3.Connection) -> float | None:
    # bcv.org.ve suele tener la cadena de certificados incompleta; solo leemos un número público.
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(BCV, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=45, context=ctx) as r:
        html = r.read().decode("utf-8", "ignore")
    m = re.search(r'id="dolar".*?<strong[^>]*>\s*([\d.,]+)\s*</strong>', html, re.S)
    if not m:
        return None
    usd = float(m.group(1).replace(".", "").replace(",", "."))
    con.execute("INSERT OR REPLACE INTO tasa_bcv VALUES (?,?)", (ahora().date().isoformat(), usd))
    con.execute("INSERT OR REPLACE INTO tasas VALUES (?,?,?)", (ahora().date().isoformat(), "bcv", usd))
    con.commit()
    return usd


DOLARAPI = "https://ve.dolarapi.com/v1"


def _get_json(url: str) -> object:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def bajar_tasas(con: sqlite3.Connection, historico: bool = True) -> int:
    """Dólar oficial BCV (diario desde 2023) y paralelo (diario desde feb-2026), vía DolarApi."""
    n = 0
    for fuente, ruta in (("bcv", "oficial"), ("paralelo", "paralelo")) if historico else ():
        for r in _get_json(f"{DOLARAPI}/historicos/dolares/{ruta}") or []:
            v = num(r.get("promedio"))
            if v and r.get("fecha"):
                con.execute("INSERT OR IGNORE INTO tasas VALUES (?,?,?)", (r["fecha"][:10], fuente, v))
                n += 1
    for r in _get_json(f"{DOLARAPI}/dolares") or []:  # valor de hoy (el paralelo cambia varias veces al día)
        fuente = {"oficial": "bcv", "paralelo": "paralelo"}.get(r.get("fuente"))
        v = num(r.get("promedio"))
        if fuente and v and r.get("fechaActualizacion"):
            f = r["fechaActualizacion"][:10] if fuente == "bcv" else ahora().date().isoformat()
            con.execute("INSERT OR REPLACE INTO tasas VALUES (?,?,?)", (f, fuente, v))
    con.commit()
    return n


def _fecha(txt: str) -> str:
    for f in ("%d/%m/%y", "%d/%m/%Y"):
        try:
            return datetime.strptime(txt, f).date().isoformat()
        except ValueError:
            pass
    raise ValueError(txt)


def bajar_detalle(con: sqlite3.Connection, simbolo: str) -> int:
    """Ficha de una acción: 1 año de cierres+volumen, ticks de la sesión, OHLC del día,
    libro de órdenes de 6 niveles, dividendos y capitalización."""
    req = urllib.request.Request(
        AJAX + "?action=getSimbolosDetalle",
        data=urllib.parse.urlencode({"simbolo": simbolo, "tipo": "rv"}).encode(),
        headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=45) as r:
        d = json.loads(r.read().decode("utf-8-sig")).get("response", {})
    vols = {_fecha(v["FEC"]): num(v.get("TOT_ACC_NEGOC")) for v in d.get("cur_grf_anual_vol_rv") or []}
    n = 0
    for p in d.get("cur_grf_anual_pre_rv") or []:
        f, c = _fecha(p["FEC"]), num(p.get("PRECIO_CIE"))
        if c is None:
            continue
        con.execute("INSERT OR IGNORE INTO diario VALUES (?,?,?,?,?,?,?,0)",
                    (f, simbolo, None, None, None, c, vols.get(f)))
        n += 1
    sesion = d.get("cur_grf_sesion_rv") or []
    for t in sesion:
        mom = datetime.strptime(t["FEC"], "%d/%m/%Y %H:%M").isoformat()
        if num(t.get("PRECIO")) is not None:
            con.execute("INSERT OR IGNORE INTO ticks VALUES (?,?,?)", (mom, simbolo, num(t["PRECIO"])))
    pv = (d.get("cur_precio_var_rv") or [{}])[0]
    if sesion and num(pv.get("PRECIO_ULT")) is not None:
        fecha = _fecha(sesion[-1]["FEC"][:10])
        vol_o = next((num(v.get("TOT_ACC_NEGOC")) for v in d.get("cur_det_vol_dia_rv") or []
                      if (v.get("TIPO_MERCADO") or "").strip() == "O"), None)
        con.execute("INSERT OR REPLACE INTO diario VALUES (?,?,?,?,?,?,?,1)",
                    (fecha, simbolo, num(pv.get("PRECIO_APERT")), num(pv.get("PRECIO_MAX")),
                     num(pv.get("PRECIO_MIN")), num(pv.get("PRECIO_ULT")),
                     vol_o if vol_o is not None else vols.get(fecha)))
    ficha = {k: d.get(k) for k in ("cur_encab_simb_rv", "cur_cap_simb_rv", "cur_con_lib_ord_rv",
                                   "cur_precio_var_rv", "cur_vol_trx_rv", "cur_vol_x_ano_rv",
                                   "cur_ult_beneficios_rv", "cur_ult_benef_otr_rv")}
    con.execute("INSERT OR REPLACE INTO detalle VALUES (?,?,?)",
                (simbolo, json.dumps(ficha, ensure_ascii=False), ahora().isoformat(timespec="seconds")))
    con.commit()
    return n


def bajar_detalles(con: sqlite3.Connection) -> int:
    simbolos = [r[0] for r in con.execute(
        "SELECT DISTINCT simbolo FROM libro UNION SELECT DISTINCT simbolo FROM precios")]
    ok = 0
    for s in simbolos:
        try:
            bajar_detalle(con, s)
            ok += 1
        except Exception:  # noqa: BLE001 - un símbolo con error no detiene el resto
            pass
        time.sleep(0.4)  # amable con el servidor de la BVC
    return ok


def actualizar(con: sqlite3.Connection, todo: bool = True) -> dict:
    res = {}
    pasos = [("indices", bajar_indices), ("tasas", bajar_tasas)] if todo else []
    pasos += [("tasas_hoy", lambda c: bajar_tasas(c, historico=False)), ("tasa_bcv", bajar_tasa), ("precios", bajar_precios), ("libro", bajar_libro),
              ("detalles", bajar_detalles)]
    for nombre, fn in pasos:
        try:
            res[nombre] = fn(con)
        except Exception as e:  # noqa: BLE001 - una fuente caída no detiene las demás
            res[nombre] = f"error: {type(e).__name__}: {e}"[:200]
    return res


def mercado_abierto(t: datetime | None = None) -> bool:
    t = t or ahora()
    return t.weekday() < 5 and (8, 25) <= (t.hour, t.minute) <= (13, 20)


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(actualizar(conectar()), ensure_ascii=False, indent=2))
