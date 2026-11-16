/* Modo DEMO: datos 100 % simulados de empresas ficticias.
   Se activa fuera de 127.0.0.1/localhost (por ejemplo en Vercel) o con ?demo en la URL.
   No usa ni muestra datos de la Bolsa de Valores de Caracas. */
(function () {
  const DEMO = (!['127.0.0.1', 'localhost'].includes(location.hostname) || new URLSearchParams(location.search).has('demo')) && !window.PRIVADO;
  window.DEMO = DEMO;
  if (!DEMO) return;

  const EMPRESAS = [
    ['ORI', 'Banco Orinoco', 'BANCO ORINOCO, C.A. (FICTICIO)', 2400, 0.0016],
    ['AVI', 'Cementos Ávila', 'CEMENTOS ÁVILA, S.A. (FICTICIO)', 860, 0.0012],
    ['LLA', 'Agro Llanera', 'AGROINDUSTRIAS LLANERA, C.A. (FICTICIO)', 145, 0.0009],
    ['CAT', 'Catatumbo Energía', 'CATATUMBO ENERGÍA, S.A. (FICTICIO)', 5200, 0.0019],
    ['ROR', 'Roraima Telecom', 'RORAIMA TELECOM, C.A. (FICTICIO)', 1180, 0.0014],
    ['MAR', 'Margarita Turismo', 'MARGARITA TURISMO, C.A. (FICTICIO)', 62, 0.0006],
    ['TEP', 'Tepuy Seguros', 'TEPUY SEGUROS, S.A. (FICTICIO)', 3400, 0.0013],
    ['CAR', 'Caribe Alimentos', 'CARIBE ALIMENTOS, C.A. (FICTICIO)', 920, 0.0015],
    ['AND', 'Andes Inmobiliaria', 'ANDES INMOBILIARIA, C.A. (FICTICIO)', 410, 0.0010],
    ['ARA', 'Araguaney Textil', 'ARAGUANEY TEXTIL, C.A. (FICTICIO)', 0.85, 0.0008],
  ];
  const INDICES = { IAV: 'Índice Ávila', FIN: 'Financiero', IND: 'Industrial' };
  const GRUPO = { FIN: ['ORI', 'TEP'], IND: ['AVI', 'LLA', 'CAT', 'CAR', 'ARA'] };
  window.DEMO_INDICES = INDICES;
  window.DEMO_SIMBOLOS = EMPRESAS.map(e => e[0]);

  // ---------- azar reproducible ----------
  function rng(seed) { let a = seed >>> 0; return () => { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a);
    t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
  const semilla = s => [...s].reduce((h, c) => Math.imul(h ^ c.charCodeAt(0), 16777619), 2166136261);
  const gauss = r => Math.sqrt(-2 * Math.log(r() || 1e-9)) * Math.cos(2 * Math.PI * r());
  const iso = d => d.toISOString().slice(0, 10);

  // días hábiles del último año hasta el último día hábil
  const dias = [];
  { const d = new Date(); d.setUTCHours(0, 0, 0, 0); while ([0, 6].includes(d.getUTCDay())) d.setUTCDate(d.getUTCDate() - 1);
    const fin = new Date(d); d.setUTCFullYear(d.getUTCFullYear() - 1);
    for (; d <= fin; d.setUTCDate(d.getUTCDate() + 1)) if (![0, 6].includes(d.getUTCDay())) dias.push(iso(d)); }
  const ULT = dias.at(-1);

  // tasas: oficial con devaluación continua; paralelo con brecha variable
  const TASAS = { bcv: {}, paralelo: {} };
  { const r = rng(42); let b = 240, brecha = 0.12;
    dias.forEach(f => { b *= 1 + 0.0056 + gauss(r) * 0.002; brecha = Math.min(0.4, Math.max(0.04, brecha + gauss(r) * 0.006));
      TASAS.bcv[f] = b; TASAS.paralelo[f] = b * (1 + brecha); }); }

  // velas diarias por empresa (con mechas reales)
  const DIARIO = {};
  for (const [s, , , precioFinal, deriva] of EMPRESAS) {
    const r = rng(semilla(s)); let p = 1; const v = [];
    for (const f of dias) {
      const o = p, c = o * Math.exp(deriva + 0.0056 * .6 + gauss(r) * 0.022);
      const h = Math.max(o, c) * (1 + Math.abs(gauss(r)) * 0.008), l = Math.min(o, c) * (1 - Math.abs(gauss(r)) * 0.008);
      v.push({ time: f, open: o, high: h, low: l, close: c, volume: Math.round(500 + r() * 20000 * (1 + Math.abs(c / o - 1) * 40)) });
      p = c;
    }
    const k = precioFinal / v.at(-1).close; // escala para terminar en un precio redondo
    v.forEach(x => { x.open *= k; x.high *= k; x.low *= k; x.close *= k; });
    DIARIO[s] = v;
  }
  // índices: promedio geométrico normalizado de sus componentes
  for (const [k, base] of [['IAV', 1700], ['FIN', 3500], ['IND', 460]]) {
    const comp = k === 'IAV' ? EMPRESAS.map(e => e[0]) : GRUPO[k];
    DIARIO[k] = dias.map((f, i) => { const val = t => base * Math.exp(comp.reduce((a, s) => a + Math.log(DIARIO[s][i][t] / DIARIO[s][0].open), 0) / comp.length);
      return { time: f, open: val('open'), high: val('high'), low: val('low'), close: val('close'), volume: null }; });
  }

  // sesión intradía (última sesión, 09:00-13:00, minuto a minuto)
  const SESION = {};
  for (const s of Object.keys(DIARIO)) {
    const d = DIARIO[s].at(-1), r = rng(semilla(s + 'ses')), n = 240, pts = [];
    let p = d.open;
    for (let i = 0; i <= n; i++) {
      const objetivo = d.open + (d.close - d.open) * (i / n);
      p = p + (objetivo - p) * 0.08 + gauss(r) * (d.high - d.low) * 0.03;
      p = Math.min(d.high, Math.max(d.low, p));
      pts.push({ t: Date.parse(ULT + 'T09:00:00Z') / 1000 + i * 60, p: i === n ? d.close : p });
    }
    SESION[s] = pts;
  }

  // ---------- utilidades ----------
  const tasaEn = (fuente, f) => { if (TASAS[fuente][f]) return TASAS[fuente][f]; const prev = dias.filter(x => x <= f).at(-1); return TASAS[fuente][prev || dias[0]]; };
  function agrupar(v, clave) { const out = []; for (const x of v) { const k = clave(x), u = out.at(-1);
    if (u && u._k === k) { u.high = Math.max(u.high, x.high); u.low = Math.min(u.low, x.low); u.close = x.close; u.volume = (u.volume || 0) + (x.volume || 0); }
    else out.push({ ...x, _k: k }); } out.forEach(x => delete x._k); return out; }
  const semana = f => { const d = new Date(f + 'T00:00:00Z'); d.setUTCDate(d.getUTCDate() - ((d.getUTCDay() + 6) % 7)); return iso(d); };

  function velas(simb, tf, moneda) {
    const k = f => moneda === 'bs' ? 1 : tasaEn(moneda, f);
    if (tf.startsWith('S')) {
      const paso = +tf.slice(1) * 60, out = [];
      for (const { t, p } of SESION[simb] || []) { const b = Math.floor(t / paso) * paso, u = out.at(-1), q = p / k(ULT);
        if (u && u.time === b) { u.high = Math.max(u.high, q); u.low = Math.min(u.low, q); u.close = q; }
        else out.push({ time: b, open: q, high: q, low: q, close: q, volume: null }); }
      return { velas: out, intradia: true, aproximadas: false, fecha_sesion: ULT, moneda, tasa_desde: dias[0] };
    }
    let v = (DIARIO[simb] || []).map(x => { const q = k(x.time); return { ...x, open: x.open / q, high: x.high / q, low: x.low / q, close: x.close / q }; });
    if (tf === 'W') v = agrupar(v, x => semana(x.time)); else if (tf === 'M') v = agrupar(v, x => x.time.slice(0, 7));
    return { velas: v, intradia: false, aproximadas: false, moneda, tasa_desde: dias[0] };
  }

  function libro(s) {
    const u = DIARIO[s].at(-1).close, r = rng(semilla(s + 'lib')), paso = u * 0.0015, n = [];
    for (let i = 0; i < 6; i++) n.push({ bid: u - paso * (i + r()), bid_vol: Math.round(10 + r() * 3000 * (i + 1)),
                                         ask: u + paso * (i + 0.2 + r()), ask_vol: Math.round(10 + r() * 3000 * (i + 1)) });
    return n;
  }

  function mercado() {
    return EMPRESAS.map(([s, nombre, largo]) => {
      const v = DIARIO[s], u = v.at(-1), a = v.at(-2), l = libro(s)[0];
      return { simbolo: s, nombre, nombre_largo: largo, precio: u.close, var_abs: u.close - a.close, var_rel: (u.close / a.close - 1) * 100,
               volumen: u.volume, monto: u.volume * u.close, hora: '12:59:00', fecha: ULT, bid: l.bid, bid_vol: l.bid_vol, ask: l.ask, ask_vol: l.ask_vol };
    });
  }

  // ---------- cartera simulada (en el navegador) ----------
  const CLAVE = 'demo:cartera';
  const leer = () => { try { return JSON.parse(localStorage.getItem(CLAVE)) || { capital: 100000, ordenes: [] }; } catch { return { capital: 100000, ordenes: [] }; } };
  const guardar = c => { try { localStorage.setItem(CLAVE, JSON.stringify(c)); } catch {} };
  let memoria = leer();
  const COMISION = 0.01;

  function cartera() {
    const c = memoria, pos = {}; let ef = c.capital;
    for (const o of [...c.ordenes].reverse()) {
      const p = pos[o.simbolo] ||= { simbolo: o.simbolo, cantidad: 0, costo: 0 }, bruto = o.cantidad * o.precio;
      if (o.lado === 'compra') { ef -= bruto + o.comision; p.costo += bruto + o.comision; p.cantidad += o.cantidad; }
      else { const prom = p.cantidad ? p.costo / p.cantidad : 0; ef += bruto - o.comision; p.costo -= prom * o.cantidad; p.cantidad -= o.cantidad; }
    }
    const m = Object.fromEntries(mercado().map(x => [x.simbolo, x]));
    const posiciones = Object.values(pos).filter(p => p.cantidad > 0).map(p => { const pa = m[p.simbolo].bid, val = p.cantidad * pa;
      return { ...p, precio_actual: pa, valor: val, ganancia: val - p.costo, ganancia_pct: (val - p.costo) / p.costo * 100 }; });
    const vp = posiciones.reduce((a, p) => a + p.valor, 0), total = ef + vp;
    return { efectivo: ef, valor_posiciones: vp, total, capital_inicial: c.capital, rendimiento_pct: (total - c.capital) / c.capital * 100,
             comision: COMISION, posiciones, ordenes: c.ordenes.slice(0, 50) };
  }

  function ordenar({ simbolo, lado, cantidad }) {
    cantidad = +cantidad;
    if (!['compra', 'venta'].includes(lado) || !(cantidad > 0) || cantidad !== Math.floor(cantidad)) return { error: 'Orden inválida: cantidad entera mayor que 0.' };
    const m = mercado().find(x => x.simbolo === simbolo); if (!m) return { error: 'Elige una acción (los índices no se compran).' };
    const precio = lado === 'compra' ? m.ask : m.bid, comision = cantidad * precio * COMISION, c = cartera();
    if (lado === 'compra' && cantidad * precio + comision > c.efectivo) return { error: `Efectivo insuficiente: necesitas Bs ${(cantidad * precio + comision).toFixed(2)}.` };
    if (lado === 'venta') { const t = c.posiciones.find(p => p.simbolo === simbolo)?.cantidad || 0; if (cantidad > t) return { error: `Solo tienes ${t} acciones de ${simbolo}.` }; }
    memoria.ordenes.unshift({ momento: new Date().toISOString().slice(0, 19), simbolo, lado, cantidad, precio, comision }); guardar(memoria);
    return { ok: true, precio, comision };
  }

  // ---------- API simulada ----------
  window.demoApi = async function (u, body) {
    const url = new URL(u, location.href), a = Object.fromEntries(url.searchParams);
    switch (url.pathname) {
      case '/api/mercado': return { filas: mercado(), abierto: false, estado: { ultima: ULT + 'T13:00:00' },
        tasa: { bcv: { fecha: ULT, usd: TASAS.bcv[ULT] }, paralelo: { fecha: ULT, usd: TASAS.paralelo[ULT] }, usd: TASAS.bcv[ULT], fecha: ULT } };
      case '/api/velas': return velas(a.simb, a.tf || 'D', a.moneda || 'bs');
      case '/api/detalle': { const e = EMPRESAS.find(x => x[0] === a.simb); if (!e) return {}; const v = DIARIO[a.simb], u = v.at(-1);
        return { nombre: e[2], isin: 'DEMO' + a.simb, acciones: 1e6 * (3 + e[0].charCodeAt(0) % 50), cap_usd: u.close / TASAS.bcv[ULT] * 1e6 * (3 + e[0].charCodeAt(0) % 50),
                 max_ano: Math.max(...v.map(x => x.high)), min_ano: Math.min(...v.map(x => x.low)), apertura: u.open, max: u.high, min: u.low,
                 medio: (u.high + u.low) / 2, libro: libro(a.simb), dividendos: [], capturado: ULT }; }
      case '/api/cartera': return cartera();
      case '/api/orden': return ordenar(body || {});
      case '/api/reiniciar': memoria = { capital: +(body?.capital) || 100000, ordenes: [] }; guardar(memoria); return { ok: true };
      case '/api/actualizar': return { ok: 'demo' };
      case '/api/indices': return Object.fromEntries(Object.keys(INDICES).map(k => [k, DIARIO[k].map(x => ({ t: x.time, v: x.close }))]));
      default: return { error: 'no existe' };
    }
  };
})();
