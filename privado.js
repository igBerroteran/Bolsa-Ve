/* Acceso privado: descifra datos.enc (snapshot real de la BVC) con la contraseña del dueño.
   Formato: "BVC1" | sal(16) | iv(12) | AES-256-GCM(gzip(JSON)); clave PBKDF2-SHA256, 600 000 iteraciones.
   La contraseña solo vive en sessionStorage (se borra al cerrar la pestaña). */
(function () {
  const publico = !['127.0.0.1', 'localhost'].includes(location.hostname) || new URLSearchParams(location.search).has('demo');
  const CLAVE_SESION = 'bvc-acceso';
  const leerSesion = () => { try { return sessionStorage.getItem(CLAVE_SESION); } catch { return null; } };
  const guardarSesion = v => { try { v ? sessionStorage.setItem(CLAVE_SESION, v) : sessionStorage.removeItem(CLAVE_SESION); } catch {} };

  async function descifrar(contrasena) {
    const r = await fetch('datos.enc', { cache: 'no-store' });
    if (!r.ok) throw new Error('No hay datos privados publicados todavía.');
    const b = new Uint8Array(await r.arrayBuffer());
    if (new TextDecoder().decode(b.slice(0, 4)) !== 'BVC1') throw new Error('Archivo de datos no válido.');
    const sal = b.slice(4, 20), iv = b.slice(20, 32), cifrado = b.slice(32);
    const base = await crypto.subtle.importKey('raw', new TextEncoder().encode(contrasena), 'PBKDF2', false, ['deriveKey']);
    const k = await crypto.subtle.deriveKey({ name: 'PBKDF2', salt: sal, iterations: 600000, hash: 'SHA-256' },
                                            base, { name: 'AES-GCM', length: 256 }, false, ['decrypt']);
    let gz;
    try { gz = await crypto.subtle.decrypt({ name: 'AES-GCM', iv }, k, cifrado); }
    catch { throw new Error('Contraseña incorrecta.'); }
    const txt = await new Response(new Blob([gz]).stream().pipeThrough(new DecompressionStream('gzip'))).text();
    return JSON.parse(txt);
  }

  // usados por el aviso de la demo
  window.accederPrivado = async contrasena => { await descifrar(contrasena); guardarSesion(contrasena); location.reload(); };
  window.salirPrivado = () => { guardarSesion(null); location.reload(); };

  const pw = publico ? leerSesion() : null;
  window.PRIVADO = !!pw;
  if (!window.PRIVADO) return;

  let promesa = null;
  const snap = () => promesa ||= descifrar(pw).catch(e => { guardarSesion(null); alert(e.message); location.reload(); throw e; });

  // ---------- tasas por fecha ----------
  const bisect = (arr, f) => { let lo = 0, hi = arr.length; while (lo < hi) { const m = (lo + hi) >> 1; if (arr[m][0] <= f) lo = m + 1; else hi = m; } return lo - 1; };
  function tasaEn(S, fuente, fecha) {
    const a = S.tasas[fuente] || [], i = bisect(a, fecha.slice(0, 10));
    if (i >= 0) return a[i][1];
    return fuente === 'bcv' ? S.fred[fecha.slice(0, 7)] : null;
  }

  function agrupar(v, clave) { const out = []; for (const x of v) { const k = clave(x), u = out.at(-1);
    if (u && u._k === k) { u.high = Math.max(u.high, x.high); u.low = Math.min(u.low, x.low); u.close = x.close; u.volume = (u.volume || 0) + (x.volume || 0); }
    else out.push({ ...x, _k: k }); } out.forEach(x => delete x._k); return out; }
  const semana = f => { const d = new Date(f + 'T00:00:00Z'); d.setUTCDate(d.getUTCDate() - ((d.getUTCDay() + 6) % 7)); return d.toISOString().slice(0, 10); };

  function velas(S, simb, tf, moneda) {
    const convertir = (lista, fija) => moneda === 'bs' ? lista : lista.flatMap(v => { const k = tasaEn(S, moneda, fija || v.time);
      return k ? [{ ...v, open: v.open / k, high: v.high / k, low: v.low / k, close: v.close / k }] : []; });
    const desde = (S.tasas[moneda] || [])[0]?.[0] || null;
    if (tf.startsWith('S')) {
      const paso = +tf.slice(1) * 60, pts = (S.intradia[simb] || []).filter(p => p[0].slice(11, 13) >= '08'), out = [];
      const ses = pts.at(-1)?.[0].slice(0, 10) || null;
      for (const [m, p] of pts) { const t = Math.floor(Date.parse(m.slice(0, 19) + 'Z') / 1000 / paso) * paso, u = out.at(-1);
        if (u && u.time === t) { u.high = Math.max(u.high, p); u.low = Math.min(u.low, p); u.close = p; }
        else out.push({ time: t, open: p, high: p, low: p, close: p, volume: null }); }
      return { velas: convertir(out, ses), intradia: true, aproximadas: false, fecha_sesion: ses, moneda, tasa_desde: desde };
    }
    const filas = S.indices[simb] ? S.indices[simb].map(([f, v]) => [f, null, null, null, v, null, 0]) : (S.diario[simb] || []);
    let out = [], prev = null, aprox = false;
    for (const [f, a, mx, mn, c, vol, real] of filas) {
      if (c == null) continue;
      const o = real && a ? a : (prev ?? c), h = real && mx ? mx : Math.max(o, c), l = real && mn ? mn : Math.min(o, c);
      if (!real) aprox = true;
      out.push({ time: f, open: o, high: Math.max(h, o, c), low: Math.min(l, o, c), close: c, volume: vol });
      prev = c;
    }
    out = convertir(out);
    if (tf === 'W') out = agrupar(out, v => semana(v.time)); else if (tf === 'M') out = agrupar(out, v => v.time.slice(0, 7));
    return { velas: out, intradia: false, aproximadas: aprox, moneda, tasa_desde: desde };
  }

  // ---------- cartera simulada (navegador) ----------
  const CLAVE = 'privado:cartera', COMISION = 0.01;
  const leer = () => { try { return JSON.parse(localStorage.getItem(CLAVE)) || { capital: 100000, ordenes: [] }; } catch { return { capital: 100000, ordenes: [] }; } };
  const guardar = c => { try { localStorage.setItem(CLAVE, JSON.stringify(c)); } catch {} };
  let memoria = leer();
  function cartera(S) {
    const pos = {}; let ef = memoria.capital;
    for (const o of [...memoria.ordenes].reverse()) {
      const p = pos[o.simbolo] ||= { simbolo: o.simbolo, cantidad: 0, costo: 0 }, bruto = o.cantidad * o.precio;
      if (o.lado === 'compra') { ef -= bruto + o.comision; p.costo += bruto + o.comision; p.cantidad += o.cantidad; }
      else { const prom = p.cantidad ? p.costo / p.cantidad : 0; ef += bruto - o.comision; p.costo -= prom * o.cantidad; p.cantidad -= o.cantidad; }
    }
    const m = Object.fromEntries(S.mercado.map(x => [x.simbolo, x]));
    const posiciones = Object.values(pos).filter(p => p.cantidad > 0).map(p => { const pa = m[p.simbolo]?.bid || m[p.simbolo]?.precio || 0, val = p.cantidad * pa;
      return { ...p, precio_actual: pa, valor: val, ganancia: val - p.costo, ganancia_pct: p.costo ? (val - p.costo) / p.costo * 100 : 0 }; });
    const vp = posiciones.reduce((a, p) => a + p.valor, 0), total = ef + vp;
    return { efectivo: ef, valor_posiciones: vp, total, capital_inicial: memoria.capital, rendimiento_pct: (total - memoria.capital) / memoria.capital * 100,
             comision: COMISION, posiciones, ordenes: memoria.ordenes.slice(0, 50) };
  }
  function ordenar(S, { simbolo, lado, cantidad }) {
    cantidad = +cantidad;
    if (!['compra', 'venta'].includes(lado) || !(cantidad > 0) || cantidad !== Math.floor(cantidad)) return { error: 'Orden inválida: cantidad entera mayor que 0.' };
    const m = S.mercado.find(x => x.simbolo === simbolo); if (!m) return { error: 'Elige una acción (los índices no se compran).' };
    const precio = (lado === 'compra' ? m.ask : m.bid) || m.precio;
    if (!precio) return { error: `No hay precio para ${simbolo}.` };
    const comision = cantidad * precio * COMISION, c = cartera(S);
    if (lado === 'compra' && cantidad * precio + comision > c.efectivo) return { error: `Efectivo insuficiente: necesitas Bs ${(cantidad * precio + comision).toFixed(2)}.` };
    if (lado === 'venta') { const t = c.posiciones.find(p => p.simbolo === simbolo)?.cantidad || 0; if (cantidad > t) return { error: `Solo tienes ${t} acciones de ${simbolo}.` }; }
    memoria.ordenes.unshift({ momento: new Date().toISOString().slice(0, 19), simbolo, lado, cantidad, precio, comision }); guardar(memoria);
    return { ok: true, precio, comision };
  }

  window.privadoApi = async function (u, body) {
    const S = await snap(), url = new URL(u, location.href), a = Object.fromEntries(url.searchParams);
    const ult = f => { const t = S.tasas[f] || []; return t.length ? { fecha: t.at(-1)[0], usd: t.at(-1)[1] } : null; };
    switch (url.pathname) {
      case '/api/mercado': { const bcv = ult('bcv'), par = ult('paralelo');
        return { filas: S.mercado, abierto: false, estado: { ultima: S.generado }, tasa: { bcv, paralelo: par, usd: bcv?.usd, fecha: bcv?.fecha } }; }
      case '/api/velas': return velas(S, a.simb || '', a.tf || 'D', a.moneda || 'bs');
      case '/api/detalle': return S.detalle[a.simb] || {};
      case '/api/cartera': return cartera(S);
      case '/api/orden': return ordenar(S, body || {});
      case '/api/reiniciar': memoria = { capital: +(body?.capital) || 100000, ordenes: [] }; guardar(memoria); return { ok: true };
      case '/api/actualizar': return { ok: 'snapshot' };
      case '/api/indices': return Object.fromEntries(Object.entries(S.indices).map(([k, v]) => [k, v.map(([t, x]) => ({ t, v: x }))]));
      default: return { error: 'no existe' };
    }
  };
})();
