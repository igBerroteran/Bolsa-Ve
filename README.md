# BVC Terminal — Bolsa de Valores de Caracas (uso privado)

Doble clic en `iniciar.bat` → se abre http://127.0.0.1:8765

- **Gráfico estilo TradingView** (Lightweight Charts de TradingView): velas, Heikin Ashi, barras, línea, área;
  temporalidades: **Sesión** 1m/5m/15m (última sesión, minuto a minuto) y **D/S/M** (1 año); rangos por fecha 1S, 1M, 3M, 6M, YTD, 1A, Todo; volumen, SMA 20/50, EMA 20/200, Bollinger, RSI, MACD;
  escala log; líneas horizontales y de tendencia (se guardan por símbolo); flechas de tus compras/ventas; rangos 1S…Todo.
- **Ficha por acción:** libro de órdenes de 6 niveles, apertura/máx/mín, rango 52 semanas, capitalización USD.
- **Renta variable:** último precio, variación, mejor compra/venta (bid/ask), volumen, monto. Ordenable.
- **Índices:** IBC, Financiero, Industrial (último año).
- **Moneda:** Bs, USD BCV oficial o USD paralelo. El gráfico convierte cada vela con la tasa **de su día** (DolarApi: oficial diario desde 2023, paralelo desde feb-2026; respaldo mensual FRED de Legal Venezuela). Los paneles usan la tasa de hoy.
- **Paper trading:** compra al precio de venta (ask), vende al de compra (bid), comisión 1 % (se cambia en la tabla `config` de `bvc.db`). Cartera con G/P.
- **Recolección:** cada 5 min en horario de mercado (L-V 8:25–13:20, hora de Caracas) mientras el programa está abierto. Todo se guarda en `bvc.db` (SQLite). El historial por acción crece cada día que recolecta.

Recolectar sin abrir la web: `py -3 bvc.py` (una vez).

## Fuentes
`bolsadecaracas.com/wp-admin/admin-ajax.php` (`resumenMercadoRentaVariable`, `get_cotizaciones`, `chartsData`, `getSimbolosDetalle`) y `bcv.org.ve`.

## Legal
- Términos de la BVC, cláusula 8: prohibido retransmitir o redifundir su información. **Solo uso personal.** Para publicar la plataforma hace falta una licencia de datos de la BVC.
- Ejecutar órdenes reales con dinero de terceros requiere ser casa de bolsa autorizada por la SUNAVAL.
- Banco de Venezuela (BVL) es estatal: personas de EE. UU. tienen prohibido operar con él (sanciones OFAC).

## Límite de los datos
La BVC solo publica **cierres diarios** del último año (sin máx/mín). Esas velas se aproximan (apertura = cierre anterior).
Cada sesión que el programa recolecta guarda la vela **real** (apertura, máx, mín, cierre) y los ticks intradía.

## Modo demo (versión pública)
Fuera de `127.0.0.1`/`localhost` (por ejemplo en Vercel), o con `?demo` en la URL, la app usa `demo.js`:
datos **100 % simulados** de empresas ficticias (Banco Orinoco, Cementos Ávila...), sin servidor y sin datos
de la BVC. La cartera simulada se guarda en el navegador. Así se puede mostrar públicamente sin
redifundir información de la bolsa.
