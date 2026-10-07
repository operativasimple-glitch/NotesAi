# Bot de trading para Kalshi

Bot en Python que opera en [Kalshi](https://kalshi.com) (mercados de predicción regulados en EE. UU.)
usando la API oficial v2. Está pensado para empezar con seguridad:

- **Arranca en simulación**: no envía ninguna orden hasta que uses `--live`.
- **Usa el entorno demo por defecto** (dinero ficticio) hasta que cambies a `prod`.
- **Límites de riesgo** obligatorios: tamaño de orden, posición por mercado, dinero comprometido,
  pérdida máxima de la sesión y freno de emergencia.

> **Importante.** Operar implica riesgo de perder dinero y ningún bot garantiza ganancias: la ventaja
> la pones tú (tu modelo, tus probabilidades). Necesitas una cuenta de Kalshi verificada y que Kalshi
> esté disponible donde vives. Prueba siempre en demo antes de usar dinero real.

## Qué incluye

| Pieza | Qué hace |
| --- | --- |
| Cliente de la API | Firma RSA-PSS (o Ed25519), endpoints de órdenes V2 con precios en dólares, reintentos y límite de peticiones por segundo. |
| Motor | Cada pocos segundos lee el libro de cada mercado, pregunta a la estrategia qué órdenes quiere y reconcilia: deja las que sirven, cancela las que sobran y crea las que faltan. |
| Riesgo | Límites de tamaño, posición y exposición; no opera cerca del cierre ni a precios extremos; corta todo si pierdes demasiado o hay errores seguidos. |
| Estrategias | `fair_value` (tú das la probabilidad) y `market_maker` (cotiza a ambos lados). Puedes escribir la tuya. |
| Comandos | `check`, `events`, `markets`, `book`, `positions`, `orders`, `run`, `cancel-all`. |
| Registro | `logs/bot.log` (lo que pasa) y `logs/journal.jsonl` (cada orden, cancelación y llenado). |

## Kalshi en 30 segundos

- Cada mercado es una pregunta de sí o no. Un contrato paga **$1** si acierta y **$0** si no, así que
  el precio (entre 1¢ y 99¢) es la probabilidad que le da el mercado.
- Comprar YES a 45¢: ganas 55¢ si sale YES y pierdes 45¢ si sale NO.
- Comprar NO a 55¢ es lo mismo que **vender YES a 45¢**. Por eso el bot lo ve todo desde YES:
  *bid* = comprar YES, *ask* = vender YES.
- Comisión por operación ≈ `0.07 × contratos × P × (1 − P)`, redondeada hacia arriba al centavo
  (como mucho ~1.75¢ por contrato a 50¢). El bot la descuenta al calcular si una operación compensa.

## Instalación

Necesitas Python 3.10 o superior.

```bash
cd kalshi-bot
python3 -m venv .venv
source .venv/bin/activate          # en Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp config.example.toml config.toml
cp .env.example .env
```

`config.toml` controla el comportamiento del bot y `.env` guarda tus credenciales. Ambos (y tus
archivos `.pem`) están en `.gitignore` para que nunca se suban a GitHub.

## Paso 1: explorar mercados (sin cuenta)

Los datos de mercado son públicos, así que puedes mirar antes de crear nada:

```bash
python -m kalshi_bot events --series KXHIGHNY      # eventos abiertos de una serie
python -m kalshi_bot markets --series KXHIGHNY     # mercados con bid/ask, volumen y cierre
python -m kalshi_bot book KXHIGHNY-26OCT08-B67.5   # libro de órdenes de un mercado
```

`KXHIGHNY` (temperatura máxima en Nueva York) es solo un ejemplo. El ticker de una serie aparece en la
URL de kalshi.com (`kalshi.com/markets/kxhighny/...` → `KXHIGHNY`). Los tickers de los mercados
cambian cada día, así que búscalos con `markets`.

Con `KALSHI_ENV=demo` verás los mercados de demo; para ver los precios reales pon `KALSHI_ENV=prod`
en `.env`. Sin API key el bot solo puede leer, nunca operar.

## Paso 2: API key en demo

1. Crea una cuenta en [demo.kalshi.co](https://demo.kalshi.co) (dinero ficticio).
2. En tu cuenta, busca la sección **API Keys** y crea una.
3. Copia el **Key ID** y descarga la **clave privada** (`.pem`). Kalshi solo la muestra una vez.
   Guárdala en la carpeta `kalshi-bot`, por ejemplo como `kalshi-key.pem`.
4. Rellena `.env`:

   ```ini
   KALSHI_ENV=demo
   KALSHI_API_KEY_ID=tu-key-id
   KALSHI_PRIVATE_KEY_PATH=./kalshi-key.pem
   ```

5. Comprueba que todo funciona:

   ```bash
   python -m kalshi_bot check
   ```

Las keys de demo solo funcionan en demo y las de producción solo en producción.

## Paso 3: elegir estrategia y simular

### Valor justo (`fair_value`, la de por defecto)

Tú estimas la probabilidad de que un mercado resuelva YES y el bot opera cuando el precio se aleja de
tu estimación lo suficiente para cubrir comisiones y dejar margen (`min_edge`).

Ejemplo: crees que mañana la máxima en Nueva York tiene un 30% de superar cierto valor. Copia
`fair_values.example.csv` como `fair_values.csv` y añade:

```csv
KXHIGHNY-26OCT08-B67.5,0.30
```

Con `min_edge = 0.04`, el bot **compra YES** si alguien lo vende lo bastante por debajo de 30¢
(unos 24¢ o menos tras comisiones) y **vende YES / compra NO** si alguien paga lo bastante por encima
(unos 36¢ o más). El archivo se recarga solo cada vez que lo guardas, así que un script tuyo puede
actualizarlo con tu modelo (pronósticos, encuestas, cuotas…).

- `mode = "taker"`: cuando hay ventaja, cruza el spread con órdenes IOC (se ejecutan al momento o se cancelan).
- `mode = "maker"`: deja órdenes en el libro a precios con ventaja y espera a que alguien las tome.

### Creador de mercado (`market_maker`)

Cotiza compra y venta alrededor del precio medio para ganar el spread, y ajusta precios según el
inventario. Es una plantilla educativa: quien sabe más que el bot le llenará justo antes de que el
precio se mueva en su contra. En `config.toml`:

```toml
[markets]
series = ["KXHIGHNY"]

[strategy]
name = "market_maker"

[strategy.params]
half_spread = 0.02
quote_size = 2
max_position = 10
skew_per_contract = 0.002
min_book_spread = 0.03
```

### Simular

```bash
python -m kalshi_bot run            # simulación: muestra lo que haría, sin enviar órdenes
python -m kalshi_bot run --once     # una sola vuelta, útil para probar la configuración
```

Verás líneas como `[SIMULACIÓN] COMPRA YES 2.00 @ 0.2400 [IOC] ...`. Detén el bot con `Ctrl+C`.

## Paso 4: operar en demo

Con `KALSHI_ENV=demo` y tu key de demo:

```bash
python -m kalshi_bot run --live
python -m kalshi_bot orders        # en otra terminal: órdenes en reposo
python -m kalshi_bot positions     # posiciones abiertas
```

El libro de demo tiene poca liquidez; sirve para comprobar que órdenes, cancelaciones y límites
funcionan, no para medir si la estrategia gana dinero.

## Paso 5: dinero real

1. Crea una API key en [kalshi.com](https://kalshi.com) y pon `KALSHI_ENV=prod` y la nueva key en `.env`.
2. Empieza con límites pequeños en `[risk]` (por ejemplo `max_total_exposure_dollars = 20`).
3. `python -m kalshi_bot run --live` muestra un aviso y espera 10 segundos antes de empezar
   (`Ctrl+C` para abortar).

Para parar:

- `Ctrl+C`: termina la vuelta actual y cancela las órdenes del bot.
- `python -m kalshi_bot cancel-all`: cancela las órdenes del bot desde otra terminal.
- `python -m kalshi_bot cancel-all --everything`: cancela **todas** las órdenes de la cuenta.

## Límites de riesgo (`[risk]` y `[bot]`)

| Parámetro | Por defecto | Qué hace |
| --- | --- | --- |
| `max_order_contracts` | 5 | Contratos máximos por orden. |
| `max_position_per_market` | 20 | Contratos máximos por mercado, YES o NO. |
| `max_total_exposure_dollars` | 50 | Dinero máximo comprometido: todas las posiciones de la cuenta más las órdenes del bot. Las órdenes que cierran posiciones siempre se permiten. |
| `max_session_loss_dollars` | 20 | Si tu patrimonio cae esto desde que arrancó el bot, cancela todo y se detiene. |
| `min_price` / `max_price` | 0.03 / 0.97 | No opera a precios extremos. |
| `min_minutes_to_close` | 15 | Cancela y no opera en los últimos minutos antes del cierre. |
| `max_consecutive_errors` | 10 | Freno de emergencia si la API falla muchas vueltas seguidas. |
| `order_ttl_seconds` | 600 | Las órdenes en reposo caducan solas si el bot se cae. |
| `cancel_on_exit` | true | Al detener el bot, cancela sus órdenes. |

El bot solo toca órdenes cuyo `client_order_id` empieza por su prefijo (`kb-`), así que las órdenes
que pongas a mano desde la web no se cancelan. Si corres varios bots en la misma cuenta, dale a cada
uno un `order_prefix` distinto.

## Crea tu propia estrategia

Crea un archivo en la carpeta `kalshi-bot`, por ejemplo `mi_estrategia.py`:

```python
from kalshi_bot.models import IOC
from kalshi_bot.strategies.base import Strategy


class CompraBarata(Strategy):
    """Compra YES cuando alguien vende por debajo de un precio fijo."""

    name = "compra_barata"

    def on_market(self, ctx):
        precio_max = self.dec("precio_max", "0.20")
        ask = ctx.book_ex_own.best_ask
        if ask is not None and ask <= precio_max and ctx.position < 5:
            return [ctx.buy_yes(precio_max, 1, tif=IOC, reason=f"ask {ask}")]
        return []
```

Y en `config.toml`:

```toml
[markets]
tickers = ["TICKER-DEL-MERCADO"]

[strategy]
name = "mi_estrategia:CompraBarata"

[strategy.params]
precio_max = 0.20
```

Qué recibe `on_market(ctx)` en cada vuelta y para cada mercado:

- `ctx.market`: datos del mercado (ticker, título, cierre, ticks de precio).
- `ctx.book` y `ctx.book_ex_own`: libro completo y libro sin tus propias órdenes
  (`best_bid`, `best_ask`, `mid`, `spread`, `bids`, `asks`).
- `ctx.position`: contratos YES netos (negativo = contratos NO).
- `ctx.own_orders`, `ctx.cash`, `ctx.now`.
- Para crear órdenes: `ctx.buy_yes(precio, cantidad)`, `ctx.sell_yes(...)` y `ctx.buy_no(precio_no, cantidad)`.
  Los precios se ajustan solos al tick válido del mercado.

Devuelve la lista de órdenes que **quieres tener**. Las órdenes GTC (por defecto) se mantienen
mientras las sigas devolviendo; si dejas de devolverlas, el bot las cancela. Las IOC se envían una vez
y, tras enviarlas, el mercado entra en enfriamiento (`taker_cooldown_seconds`). Todo pasa por los
límites de riesgo antes de llegar a Kalshi.

## Dejarlo corriendo

El bot tiene que estar en marcha para operar, así que necesitas un ordenador encendido o un servidor
barato (VPS). En Linux, por ejemplo:

```bash
tmux new -s kalshi
source .venv/bin/activate
python -m kalshi_bot run --live
# Ctrl+B y luego D para dejarlo en segundo plano; `tmux attach -t kalshi` para volver
```

En un servidor puedes pasar la clave como variable de entorno (`KALSHI_PRIVATE_KEY`) en lugar de un
archivo.

## Comandos

| Comando | Qué hace |
| --- | --- |
| `check` | Verifica conexión, credenciales, saldo y nivel de API. |
| `events [--series S]` | Lista eventos abiertos. |
| `markets --series S \| --event E` | Lista mercados con precios, volumen y tiempo hasta el cierre. |
| `book TICKER` | Muestra el libro de órdenes. |
| `positions` / `orders` | Tus posiciones y órdenes en reposo. |
| `run [--live] [--once]` | Ejecuta el bot (simulación salvo `--live`). |
| `cancel-all [--everything]` | Cancela las órdenes del bot (o todas). |

Opciones globales: `-c otra-config.toml` y `-v` para ver más detalle.

## Estructura

```
kalshi-bot/
├── kalshi_bot/
│   ├── auth.py          firma de peticiones
│   ├── client.py        cliente REST de la API v2
│   ├── models.py        mercados, libro, órdenes, ticks de precio
│   ├── fees.py          estimación de comisiones
│   ├── risk.py          límites de riesgo
│   ├── engine.py        bucle del bot, ejecución real y simulada
│   ├── config.py        carga de config.toml y .env
│   ├── cli.py           comandos
│   └── strategies/      fair_value, market_maker y la clase base
├── tests/               pruebas (no necesitan conexión)
├── config.example.toml
├── .env.example
└── fair_values.example.csv
```

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

Los tests simulan la API de Kalshi, así que no necesitan conexión ni credenciales.

## Notas técnicas

- Se construyó siguiendo el SDK oficial de Kalshi (v3.32): órdenes por `POST /portfolio/events/orders`
  (el `POST /portfolio/orders` antiguo está en retirada desde mayo de 2026), con `side` = `bid`/`ask`,
  precios en dólares (`"0.5600"`) y cantidades en punto fijo (`"10.00"`).
- URLs: producción `https://external-api.kalshi.com/trade-api/v2` y demo
  `https://external-api.demo.kalshi.co/trade-api/v2`. Puedes forzar otra con `KALSHI_BASE_URL`
  (por ejemplo `https://api.elections.kalshi.com/trade-api/v2`).
- Respeta los ticks de cada mercado (`price_ranges`), incluidos los de subcentavo.
- Opera contratos enteros y consulta la API por REST cada `poll_interval_seconds` (sin WebSockets).
- Las comisiones son estimaciones; ajusta `taker_fee_rate` y `maker_fee_rate` en `[strategy.params]`
  si operas series con tarifas especiales.
- Proyecto independiente, sin relación con Kalshi.

## Solución de problemas

- **HTTP 401/403**: el Key ID no corresponde al `.pem`, la key es de otro entorno (demo vs prod) o el
  reloj de tu ordenador va desfasado.
- **"No hay mercados que seguir"**: pon `tickers`, `series` o `events` en `[markets]`, o valores justos
  en la estrategia `fair_value`.
- **HTTP 429**: demasiadas peticiones; baja `reads_per_second` / `writes_per_second` en `[http]` o
  sube `poll_interval_seconds`.
- **Órdenes rechazadas**: revisa `logs/bot.log`; Kalshi explica el motivo (saldo, tick inválido,
  mercado cerrado…).
