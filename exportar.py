"""Exporta bvc.db a un snapshot CIFRADO (datos.enc) para publicarlo en Vercel.

    .venv\\Scripts\\python exportar.py

La contraseña se lee de clave.txt (junto a este archivo, nunca se sube a git)
o de la variable de entorno BVC_CLAVE.

Formato de datos.enc:  b"BVC1" | sal (16) | iv (12) | AES-256-GCM( gzip(JSON) )
Clave: PBKDF2-HMAC-SHA256(contraseña, sal, 600 000 iteraciones), igual que en privado.js.
Sin la contraseña el archivo es ilegible: así los datos de la BVC no se redifunden.
"""
from __future__ import annotations

import gzip
import json
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

import bvc
import servidor as srv

BASE = Path(__file__).resolve().parent
SALIDA = BASE / "datos.enc"
ITERACIONES = 600_000


def clave() -> str:
    c = os.environ.get("BVC_CLAVE") or ""
    f = BASE / "clave.txt"
    if not c and f.exists():
        c = f.read_text(encoding="utf-8").strip()
    if len(c) < 10:
        sys.exit("Falta la contraseña (mínimo 10 caracteres) en clave.txt o en BVC_CLAVE.")
    return c


def snapshot() -> dict:
    q = srv.q
    simbolos = sorted({r["simbolo"] for r in q("SELECT DISTINCT simbolo FROM diario")} |
                      {r["simbolo"] for r in q("SELECT DISTINCT simbolo FROM libro")})
    diario = {}
    for s in simbolos:
        diario[s] = [[r["fecha"], r["apertura"], r["maximo"], r["minimo"], r["cierre"], r["volumen"], r["real"]]
                     for r in q("SELECT * FROM diario WHERE simbolo=? ORDER BY fecha", (s,))]
    indices = {}
    for r in q("SELECT fecha, indice, valor FROM indices ORDER BY fecha"):
        indices.setdefault(r["indice"], []).append([r["fecha"], r["valor"]])
    intradia = {}
    for s in simbolos:  # solo la última sesión de cada símbolo
        pts = q("SELECT momento, precio FROM ticks WHERE simbolo=? ORDER BY momento", (s,))
        if pts:
            ult = pts[-1]["momento"][:10]
            intradia[s] = [[r["momento"], r["precio"]] for r in pts if r["momento"][:10] == ult]
    for i in srv.INDICES:
        pts = q("SELECT momento, valor FROM indices_intradia WHERE indice=? ORDER BY momento", (i,))
        if pts:
            ult = pts[-1]["momento"][:10]
            intradia[i] = [[r["momento"], r["valor"]] for r in pts if r["momento"][:10] == ult]
    tasas = {f: [[r["fecha"], r["usd"]] for r in q("SELECT fecha, usd FROM tasas WHERE fuente=? ORDER BY fecha", (f,))]
             for f in ("bcv", "paralelo")}
    return {
        "version": 1,
        "generado": bvc.ahora().isoformat(timespec="seconds"),
        "mercado": srv.mercado(),
        "detalle": {s: srv.detalle(s) for s in simbolos},
        "diario": diario, "indices": indices, "intradia": intradia,
        "tasas": tasas, "fred": srv._fred(),
        "indices_nombres": srv.INDICES,
    }


def cifrar(datos: bytes, contrasena: str) -> bytes:
    sal, iv = os.urandom(16), os.urandom(12)
    k = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=sal, iterations=ITERACIONES).derive(contrasena.encode())
    return b"BVC1" + sal + iv + AESGCM(k).encrypt(iv, datos, None)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    c = clave()
    snap = snapshot()
    crudo = json.dumps(snap, ensure_ascii=False, separators=(",", ":"), default=str).encode()
    comprimido = gzip.compress(crudo, 9)
    SALIDA.write_bytes(cifrar(comprimido, c))
    print(f"Snapshot: {len(snap['mercado'])} acciones, {sum(len(v) for v in snap['diario'].values())} velas diarias, "
          f"generado {snap['generado']}")
    print(f"{len(crudo)/1e6:.1f} MB JSON -> {len(comprimido)/1e3:.0f} KB gzip -> {SALIDA.name} cifrado "
          f"({SALIDA.stat().st_size/1e3:.0f} KB)")


if __name__ == "__main__":
    main()
