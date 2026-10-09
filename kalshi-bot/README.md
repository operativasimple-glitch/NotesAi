# Bot de trading para Kalshi

Bot en Python que opera en [Kalshi](https://kalshi.com) (mercados de predicción) con la API oficial v2.
Se maneja desde un **panel web pensado para el móvil** o desde la terminal.

- **Estrategia basada en datos:** compra el lado favorito como *maker*. Con datos reales de Kalshi dio
  **+3,9 % tras comisiones en 878 partidos** y **+4,1 % en 470 días de temperatura máxima** de 7
  ciudades, en los dos casos con un margen de error por encima de cero. Por qué, con fuentes, en
  [INVESTIGACION.md](INVESTIGACION.md).
- **Seguro por defecto:**
  - simula hasta que le pides operar;
  - usa el entorno demo (dinero ficticio) hasta que cambias a real;
  - tiene límites de riesgo y un freno de emergencia.
- **Herramientas para buscar ventaja:**
  - un barrido que compara las series activas y te dice dónde están ganando los favoritos;
  - un escáner de oportunidades;
  - una investigación que mide con datos reales quién gana a cada precio.

> **Importante.** Operar implica riesgo de perder dinero y ningún bot garantiza ganancias. Necesitas una
> cuenta de Kalshi verificada y que Kalshi esté permitido donde vives. **En España, la DGOJ ordenó bloquear
> Kalshi en 2026:** no lo uses desde allí. Prueba siempre en demo antes de usar dinero real.

## Qué incluye

| Pieza | Qué hace |
| --- | --- |
| Panel web (PWA) | Arrancar y parar el bot, freno de emergencia, saldo, posiciones, órdenes y actividad en directo. También mercados con libro y orden manual, escáner, investigación y ajustes. Se instala en la pantalla de inicio del móvil. |
| Estrategias | `favorites` (favoritos 88–97¢ como maker, la recomendada), `fair_value` (tus probabilidades) y `market_maker` (plantilla). Puedes escribir la tuya. |
| Riesgo | Límites de tamaño, posición y exposición; no opera cerca del cierre ni a precios extremos; corta todo si pierdes demasiado o hay errores seguidos; las órdenes caducan solas si el bot se cae. |
| API | Firma RSA-PSS o Ed25519, órdenes V2 con precios en dólares, ticks de subcentavo, reintentos y límite de peticiones. |
| Registro | `logs/bot.log` y `logs/journal.jsonl` (cada orden, cancelación y llenado). |

## Kalshi en 30 segundos

- Cada mercado es una pregunta de sí o no. Un contrato paga **1 $** si acierta y **0 $** si no, así que
  el precio (1¢–99¢) es la probabilidad que le da el mercado.
- Comprar SÍ a 45¢: ganas 55¢ si sale SÍ y pierdes 45¢ si sale NO. Comprar NO a 55¢ es lo mismo que
  vender SÍ a 45¢.
- Comisión ≈ `0,07 × contratos × P × (1 − P)` para quien cruza el spread (*taker*) y la cuarta parte para
  quien espera en el libro (*maker*). Es mínima cerca de 0¢ y de 100¢.

## Usarlo desde el móvil (panel web)

El bot tiene que estar en marcha en algún sitio para operar, aunque cierres el móvil. Lo más sencillo es
**Railway** (unos 5 $/mes; todo se hace desde el navegador del móvil).

### Opción A: Railway

1. Entra en [railway.com](https://railway.com) con tu cuenta de GitHub y activa el plan **Hobby**.
2. **New Project → Deploy from GitHub repo** y elige este repositorio.
3. En el servicio, **Settings → Source**:
   - **Root Directory**: `/kalshi-bot`. Railway encontrará el `Dockerfile` solo.
   - **Branch**: la rama donde esté el bot, si no es la principal.
4. **Settings → Deploy → Regions**: una región de **EE. UU.** (mejor US East, Virginia). Kalshi bloquea
   conexiones desde algunos países.
5. En **Variables** añade `DASHBOARD_PASSWORD`: la contraseña del panel. Que sea larga (12 caracteres o
   más).
6. Clic derecho en el servicio → **Attach volume**, montado en `/data`. Así se guardan tus ajustes,
   credenciales y registros entre despliegues.
7. **Settings → Networking → Generate Domain**. Abre esa dirección en el móvil, entra con tu contraseña y
   añádela a la pantalla de inicio:
   - iPhone: Compartir → Añadir a pantalla de inicio;
   - Android: menú → Instalar app.

Opcional: en **Settings → Config-as-code** pon `/kalshi-bot/railway.json` para que Railway use el
chequeo de salud (`/healthz`) y reinicie el bot si se cae.

### Puesta en marcha (en el panel)

1. **Ajustes → Avanzado → Configurar API key**: pega el Key ID, pulsa **Cargar la clave desde un archivo** y
   elige el archivo de la clave privada que te dio Kalshi (o pega su texto). Pulsa **Guardar y probar**.
2. El panel comprueba cada paso: conexión, key, saldo, cartera, mercados y libro de órdenes. Si te dice que
   la key es de Real, pulsa **Cambiar a Real y volver a probar**.
3. **Probar también una orden**: envía 1 contrato a 1¢ (no se llena) y lo cancela al instante. Si todo
   sale en verde, el bot puede operar con tu cuenta.
4. **Análisis → ¿Dónde gana más el bot?**: mira en qué series han ganado los favoritos y pulsa
   **Usar** en la mejor. El botón solo aparece si el rendimiento fue positivo.
5. **Ajustes → Modo: Simulación** y activa el bot durante al menos un día. En **Inicio → Actividad** verás
   las órdenes que habría puesto.
6. Cuando estés convencido, **Modo: Real** y activa el bot (pide escribir REAL). Empieza con los límites por
   defecto: 50 $ comprometidos como máximo y freno si pierdes 20 $.

### Antes de pagar nada: ¿gana la estrategia?

El repositorio trae un trabajo de GitHub Actions (`.github/workflows/kalshi-research.yml`) que descarga
mercados ya liquidados de Kalshi y simula la estrategia del bot: comprar a 88–97¢ como maker, sin los
últimos 15 minutos antes del cierre, con comisiones. Da el rendimiento por serie con su margen de error y
solo dice "gana" si no puede ser casualidad. Mide los partidos (NFL, MLB, NBA, NHL y fútbol americano
universitario), la temperatura máxima de 7 ciudades y la bolsa (Nasdaq-100 y S&P 500). No usa tu clave ni envía
órdenes, y en repositorios públicos es gratis. El resultado sale en el resumen de cada ejecución, en la
pestaña **Actions**.

Si prefieres no guardar la clave en el panel, ponla como variables del servidor (`KALSHI_API_KEY_ID`,
`KALSHI_PRIVATE_KEY` con el PEM y `KALSHI_ENV`). Las variables tienen prioridad sobre el panel.

### Opción B: tu ordenador o un servidor propio

```bash
cd kalshi-bot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.toml config.toml
DASHBOARD_PASSWORD='una-contraseña-larga' python -m kalshi_bot web
```

Abre `http://IP-DEL-ORDENADOR:8000` desde el móvil (en la misma wifi). Para entrar desde fuera de casa usa
[Tailscale](https://tailscale.com) en lugar de abrir puertos del router. Con Docker:
`docker build -t kalshi-bot . && docker run -p 8000:8000 -e DASHBOARD_PASSWORD=... -v kalshi-data:/data kalshi-bot`.

### Qué hay en el panel

- **Inicio:**
  - arriba, en grande, el **saldo ahora** (disponible y en juego) y lo **ganado hoy** por el bot (con lo de
    ayer). El saldo se actualiza en cada vuelta del bot; tócalo para refrescarlo todo;
  - el estado del bot y lo **ganado por el bot** (últimos 90 días), con su curva mercado a mercado;
  - **Acierto**: cuántos mercados acierta frente al umbral que necesita (su precio medio de entrada, con
    comisiones). Si la barra queda por debajo de la marca, está perdiendo;
  - **Muestra**: mercados cerrados de 100; antes de 100 no se sabe si es ventaja o racha;
  - el último mercado cerrado (tócalo para ver el detalle);
  - lo que lleva la sesión, junto al límite del freno;
  - las apuestas abiertas con nombre legible ("Chicago · 78° a 79°") y la probabilidad que les da ahora el
    mercado; las órdenes esperando (con cancelar);
  - la actividad en directo, en frases: qué compró, qué canceló y qué frenó el riesgo.
- **Resultados:**
  - ganado, acierto y precio medio de entrada en 7, 30 o 90 días;
  - los mercados cerrados día a día. Toca uno para ver su detalle: resultado, riesgo frente a premio
    (cuántos aciertos borra una pérdida así) y el acierto que hacía falta;
  - en **Más datos**: rendimiento frente a lo esperado, un gráfico por día (oliva, ganado; rosa, perdido;
    con los mismos datos en una tabla) y el reparto por tipo de mercado;
  - **Solo el bot** (por defecto) cuenta lo que compró el bot; **Toda la cuenta** suma también lo que
    compres a mano. Dentro de un mismo mercado se separan: cada contrato cuenta para quien lo compró, aunque
    lo cierre una orden del otro (en Kalshi, comprar SÍ teniendo NO vende esos NO). Las órdenes del bot se
    reconocen por su diario, que se guarda en `/data`. En simulación no hay resultados.
- **Mercados:**
  - busca por serie o evento;
  - toca un mercado para ver su libro;
  - compra SÍ o NO con orden limitada (con coste, ganancia máxima y comisión estimada);
  - "Seguir con el bot".
- **Análisis** (antes "Oportunidades"):
  - "¿Dónde gana más el bot?": ordena las series por lo que ganaron los favoritos y, con un toque,
    centra el bot en la mejor;
  - escáner: favoritos, spreads amplios y arbitraje en eventos;
  - investigación: rendimiento por tramo de precio, takers frente a makers, con mercados ya liquidados.
- **Ajustes:**
  - el interruptor del bot y el modo: **Real** (envía órdenes) o **Simulación** (no envía nada);
  - contratos por operación, precio máximo de entrada y pérdida máxima. Con el bot en marcha, los cambios
    se aplican al reiniciarlo: el panel ofrece **Reiniciar ahora**;
  - **Cobrar al máximo en el clima**: vende a 99¢ las apuestas de temperatura ya casi ganadas para
    recuperar el dinero horas antes (ver [Cobrar antes de tiempo](#cobrar-antes-de-tiempo));
  - **Avisos** al móvil: cuando el bot compra o vende y cuando se cierra un mercado, con lo ganado o
    perdido (ver abajo);
  - **Freno de emergencia**: cancela primero y luego para;
  - **Avanzado**: cuenta y entorno (Demo o Real), **Probar conexión** y una orden de prueba de 1¢; estrategia y
    sus parámetros, valores justos, qué mercados seguir y límites de riesgo.

Avisos al móvil:

- En el iPhone (iOS 16.4 o posterior) solo funcionan con el panel instalado: abre el panel en Safari,
  **Compartir → Añadir a pantalla de inicio**, ábrelo desde ese icono y activa los avisos en **Ajustes**.
  En Android funcionan también desde Chrome.
- Los avisos van cifrados de punta a punta: el panel los cifra para tu móvil y los entrega el servicio de
  avisos del propio navegador (Apple o Google), que no puede leerlos.
- La clave del panel para los avisos (`push_vapid.pem`) y las suscripciones se guardan en `/data`. Si borras
  el volumen, vuelve a activar los avisos.

Seguridad del panel:

- La sesión es una cookie HttpOnly firmada.
- El navegador bloquea las peticiones que intenten hacer otras webs.
- El login se bloquea un minuto tras 5 intentos fallidos.
- Las cabeceras CSP son estrictas.
- Operar con dinero real exige escribir `REAL`.
- La clave privada nunca se devuelve al navegador.

## Crear la API key de Kalshi

1. En [kalshi.com](https://kalshi.com) (o en [demo.kalshi.co](https://demo.kalshi.co) para dinero
   ficticio), ve a tu cuenta, sección **API Keys**, y crea una key.
2. Copia el **Key ID** y guarda la **clave privada** (el archivo que se descarga o su texto). Kalshi solo
   la muestra una vez: si la pierdes, borra esa key y crea otra.
3. Las keys de demo solo funcionan en Demo y las de kalshi.com solo en Real. El panel detecta si la has
   puesto en el entorno equivocado.
4. Nunca compartas la clave privada: con ella se puede operar con tu dinero. El Key ID solo no basta.

## La estrategia recomendada: favoritos

Resumen de [INVESTIGACION.md](INVESTIGACION.md):

- **Lo que dice el estudio** (más de 300.000 contratos de Kalshi):
  - quien compra por debajo de 10¢ pierde más del 60 %;
  - los contratos de más de 50¢ dan rendimientos pequeños y positivos;
  - los takers pierden ~32 % y los makers ~10 %.
- **Qué hace la estrategia `favorites`:**
  - deja órdenes de compra *post-only* (nunca paga comisión de taker) en el lado favorito, SÍ o NO;
  - solo entre 88¢ y 97¢ y sin cruzar el spread;
  - 10 contratos por orden;
  - en los partidos (MLB, NFL, NHL, NBA y universitario) que terminan en las próximas 6 h, uno por
    partido, hasta 15 min antes del final previsto;
  - y en la temperatura máxima diaria de 7 ciudades (mira hasta 4 tramos por día y apuesta en uno). Si tu estado bloquea los
    contratos deportivos, quita los partidos en **Ajustes → Mercados**: el clima sigue funcionando.
- **No cambia de bando**: si en un mercado ya tiene NO y el favorito pasa a ser el SÍ (o al revés), no compra el
  nuevo favorito. En Kalshi eso vendería primero lo que tiene, a lo poco que vale ya, y con una previsión que
  sube y baja puede perder dos veces en el mismo mercado. Se queda con lo que tenía hasta el final, y así
  tampoco deshace lo que compres tú a mano.
- **La ventaja es pequeña** (unos céntimos por contrato) y **un fallo a 95¢ borra 19 aciertos**. Por
  eso importan los límites de riesgo y la diversificación.
- **Antes de arriesgar dinero**, usa **Investigación** en el panel (o `python -m kalshi_bot research`)
  para comprobar que el sesgo sigue existiendo en los mercados que vas a operar.

### Cobrar antes de tiempo

**Cobrar al máximo en el clima** (interruptor en **Ajustes**): vende las apuestas de temperatura máxima
(series `KXHIGH`) en cuanto se pueden cobrar a 99¢, el máximo antes del pago. Esos mercados cierran a
medianoche (hora del Este) y Kalshi paga a la mañana siguiente, así que una apuesta decidida por la tarde
deja el dinero parado muchas horas; vendiendo a 99¢ vuelve enseguida y el bot puede usarlo en otras
apuestas. Cuesta 1¢ por contrato y una comisión de taker de 1–2¢ por venta. Con datos reales, en el clima
apenas cambia el resultado (−0,16 %); en los partidos costaría un 1 %, así que ahí espera al final. Con el
bot en marcha, pulsa **Reiniciar ahora** después de activarlo.

Las opciones completas están en **Ajustes → Avanzado → Estrategia** (las dos primeras, a 0, están
desactivadas):

- **Vender si el favorito cae a**: si el partido o el día se tuercen y el favorito baja hasta ese
  precio, el bot vende enseguida lo que tenga en ese mercado.
- **Cobrar antes si ya se puede vender a** (98¢ o 99¢): vende un favorito casi ganado para tener el
  dinero antes de la liquidación.
- **Cobrar antes solo en estas series**: dónde se aplica lo anterior; `KXHIGH` por defecto, vacío = en
  todas.

Solo tocan posiciones compradas como favorito (a 83¢ o más) en las series que sigue el bot, no lo que
compres tú a mano a otros precios. Con alguna activada, el bot vigila esas posiciones hasta que el
mercado cierra, también en los últimos 15 minutos. Las ventas salen en Actividad como **Vendido** y, con
los avisos activados, llega «Venta del bot». Kalshi deja operar fracciones de contrato: si al otro lado solo
compran una parte, quedan restos como 0,85 contratos, y el bot los vende también en las vueltas siguientes.

Con datos reales, **ninguna mejora el resultado por contrato y cortar pérdidas lo empeora**: la mayoría
de los favoritos que caen se recuperan. Por eso vienen desactivadas; cobrar a 99¢ en el clima es la
excepción que merece la pena cuando el saldo es poco y lo que falta es dinero libre. Detalle en
[INVESTIGACION.md](INVESTIGACION.md#cobrar-antes-de-tiempo).

Las otras estrategias:

- `fair_value`: tú escribes tu probabilidad para cada mercado (en el panel o en `fair_values.csv`) y el
  bot opera cuando el precio se aleja lo suficiente.
- `market_maker`: cotiza a ambos lados. Es educativa y compite con creadores de mercado profesionales.

## Límites de riesgo

| Parámetro | Por defecto | Qué hace |
| --- | --- | --- |
| `max_order_contracts` | 10 | Contratos máximos por orden. |
| `max_position_per_market` | 20 | Contratos máximos por mercado, SÍ o NO. |
| `max_total_exposure_dollars` | 50 | Dinero máximo comprometido: posiciones de la cuenta más órdenes del bot. Cerrar posiciones siempre se permite. |
| `max_session_loss_dollars` | 20 | Si tu patrimonio cae esto desde que arrancó el bot, cancela todo y se detiene. Cuenta también lo que compres a mano. |
| `max_positions_per_event` | 1 | Mercados con dinero a la vez en un mismo partido o día de clima: los tramos vecinos son casi la misma apuesta. Cuenta también lo tuyo. |
| `min_price` / `max_price` | 0.03 / 0.97 | No compra a precios extremos (vender para cerrar una posición sí se permite). |
| `min_minutes_to_close` | 15 | Cancela y no compra en los últimos minutos antes del cierre (si activas las salidas, sigue vigilando para vender). |
| `max_consecutive_errors` / `min_error_minutes` | 10 / 3 | Freno de emergencia si la API falla 10 vueltas seguidas durante al menos 3 minutos. Mientras tanto espera cada vez más entre intentos (hasta 1 minuto), así que un corte breve no lo para. |
| `order_ttl_seconds` | 600 | Las órdenes en reposo caducan solas si el bot se cae. |

El bot solo toca órdenes cuyo `client_order_id` empieza por su prefijo (`kb-`). Las órdenes manuales del
panel (`man-`) y las de la web de Kalshi no las cancela. Si corres varios bots, dale a cada uno un
`order_prefix` distinto.

## Uso desde la terminal

```bash
cp config.example.toml config.toml && cp .env.example .env   # y rellena .env con tu key
python -m kalshi_bot check                         # conexión, credenciales y saldo
python -m kalshi_bot scan                          # oportunidades ahora mismo
python -m kalshi_bot research --sweep              # ¿en qué series ganan los favoritos?
python -m kalshi_bot research --series KXHIGHNY    # ¿quién gana a cada precio en esa serie?
python -m kalshi_bot run                           # simulación
python -m kalshi_bot run --live                    # órdenes de verdad (demo o real según KALSHI_ENV)
python -m kalshi_bot cancel-all                    # cancela las órdenes del bot
```

| Comando | Qué hace |
| --- | --- |
| `web [--host H] [--port P]` | Panel web. Necesita `DASHBOARD_PASSWORD`. |
| `check` | Conexión, credenciales, saldo y nivel de API. |
| `series [--category Financials]` | Las series más negociadas, todas o de una categoría. |
| `events [--series S]` / `markets --series S \| --event E` / `book TICKER` | Explorar mercados. |
| `scan [--hours 48] [--series S ...]` | Favoritos, spreads amplios y arbitraje en eventos. |
| `research [--series S,T] [--markets 150]` | Rendimiento por tramo de precio (taker frente a maker) y de la estrategia del bot, con margen de error, en mercados liquidados. |
| `research --category Financials [--top 8]` | Lo mismo con las series más negociadas de una categoría (p. ej. bolsa). |
| `research --sweep [--series-count 12]` | Compara las series activas: en cuáles ganan los favoritos y en cuáles no. |
| `research --series S,T --exits --pages 3` | Compara esperar a la liquidación con vender antes (cortar pérdidas a 70/50/30¢, cobrar a 98/99¢), con las mismas compras. |
| `positions` / `orders` | Tus posiciones y órdenes en reposo. |
| `run [--live] [--once]` | Ejecuta el bot (simulación salvo `--live`). |
| `cancel-all [--everything]` | Cancela las órdenes del bot (o todas). |

## Crea tu propia estrategia

```python
# mi_estrategia.py, en la carpeta kalshi-bot
from kalshi_bot.models import IOC
from kalshi_bot.strategies.base import Strategy


class CompraBarata(Strategy):
    """Compra SÍ cuando alguien vende por debajo de un precio fijo."""

    name = "compra_barata"

    def on_market(self, ctx):
        precio_max = self.dec("precio_max", "0.20")
        ask = ctx.book_ex_own.best_ask
        if ask is not None and ask <= precio_max and ctx.position < 5:
            return [ctx.buy_yes(precio_max, 1, tif=IOC, reason=f"ask {ask}")]
        return []
```

En `config.toml`: `[strategy] name = "mi_estrategia:CompraBarata"` y sus parámetros en
`[strategy.params]`.

Qué tienes en cada vuelta:

- `ctx` trae el mercado, el libro (`book` y `book_ex_own`, este sin tus órdenes), tu posición
  (positiva SÍ, negativa NO), tus órdenes y el saldo.
- Para crear órdenes: `ctx.buy_yes`, `ctx.sell_yes` o `ctx.buy_no`. El precio se ajusta solo a un
  tick válido.
- Devuelve las órdenes que **quieres tener**:
  - las GTC se mantienen mientras las sigas devolviendo;
  - las IOC se envían una vez.

## Estructura

```
kalshi-bot/
├── kalshi_bot/
│   ├── client.py, auth.py, models.py, fees.py   API de Kalshi
│   ├── engine.py, risk.py, discovery.py         motor, riesgo y búsqueda de mercados
│   ├── strategies/                              favorites, fair_value, market_maker
│   ├── scanner.py, research.py                  escáner e investigación
│   ├── controller.py, web/                      panel web (servidor + PWA)
│   ├── push.py, names.py                        avisos al móvil (Web Push) y nombres legibles
│   └── config.py, cli.py
├── tests/                                       pruebas sin conexión (pytest)
├── INVESTIGACION.md                             dónde está la ventaja, con fuentes
├── Dockerfile, railway.json                     despliegue
└── config.example.toml, .env.example
```

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

Simulan la API de Kalshi y levantan el panel en local, así que no necesitan conexión ni credenciales.

## Notas técnicas

- Se construyó siguiendo el SDK oficial de Kalshi (v3.32): órdenes por `POST /portfolio/events/orders`,
  con `side` = `bid`/`ask`, precios en dólares (`"0.5600"`) y cantidades en punto fijo (`"10.00"`).
- URLs: producción `https://external-api.kalshi.com/trade-api/v2` y demo
  `https://external-api.demo.kalshi.co/trade-api/v2`. Si no responden, el bot pasa solo a las
  alternativas que el SDK también da por buenas (`api.elections.kalshi.com` y `demo-api.kalshi.co`).
  Puedes forzar otra con `KALSHI_BASE_URL`.
- El bot opera contratos enteros y consulta la API por REST cada `poll_interval_seconds`.
- Las comisiones del bot son estimaciones conservadoras.
- El panel sirve sus propias fuentes (Bricolage Grotesque y DM Mono, licencia SIL OFL 1.1: ver
  `kalshi_bot/web/static/fonts/OFL.txt`); no carga nada de fuera.
- Proyecto independiente, sin relación con Kalshi.

## Solución de problemas

Lo primero: **Ajustes → Probar conexión**. Dice qué paso falla y qué hacer.

- **HTTP 401/403**: el Key ID no corresponde a la clave, la key es de otro entorno (demo frente a real) o
  el reloj del servidor va desfasado.
- **No conecta con Kalshi**: el servidor debe estar en una región de EE. UU.
- **"No hay mercados que seguir"**: por defecto el bot sigue los partidos que terminan en las próximas
  6 h y la temperatura máxima de 7 ciudades. Revisa **Ajustes → Mercados** (o `[markets]`).
- **"Kalshi pide ir más despacio" (HTTP 429)**: demasiadas peticiones seguidas. No pasa nada: el bot espera,
  repite la petición y baja solo su ritmo (a la mitad, sin bajar de 1 por segundo); luego lo va recuperando,
  un 25 % por cada minuto sin avisos. El bot y el panel comparten ese límite. Con API key, los datos de
  mercado también van firmados, para que Kalshi los cuente contra tu cuenta y no contra la IP del servidor,
  que en Railway comparten muchos usuarios. Si aun así pasa a menudo, baja `reads_per_second`.
- **No puedo entrar al panel**: comprueba `DASHBOARD_PASSWORD`. Tras 5 fallos, espera un minuto.
- **No llegan los avisos**: en el iPhone el panel tiene que estar instalado en la pantalla de inicio. Mira que
  la app tenga permiso en **Ajustes del móvil → Notificaciones** y pulsa **Enviar un aviso de prueba**.
- **¿Por qué un mercado salió en pérdidas?**: en **Resultados**, toca el mercado. En **Operaciones** sale cada
  compra y venta en orden, quién la hizo (el bot o tú), por qué y cuánto ganó o perdió cada venta («vende 5 NO
  tuyos» si una orden del bot cerró contratos que compraste tú). Ojo: en Kalshi,
  comprar SÍ teniendo NO vende primero esos NO (y al revés), y un mercado solo cuenta en Resultados cuando ya no
  queda nada en él: una pérdida de por la mañana puede aparecer cuando, por la tarde, se cobra el resto a 99¢.
- **«Otra parte del exchange» o `insufficient_shard_balance`**: desde agosto de 2026 Kalshi reparte los mercados en
  varias partes (shards), cada una con su propio saldo. La app y la web mueven el dinero solas entre ellas; por la
  API no, así que una orden en un mercado que está en otra parte falla. El bot lo dice una vez y deja de operar esa
  serie (por ejemplo, la NBA) hasta que se reinicie; el resto sigue igual. Para operarla habría que pasar saldo a
  esa parte desde Kalshi.
- **El bot está activo pero no compra**: bajo «Bot activo», en Inicio, sale lo que vio en su última vuelta, p. ej.
  «Ahora no compra: 14 ya casi decididos · 5 sin favorito claro · 2 a punto de cerrar». Es normal por la noche:
  el clima de hoy ya está decidido (a 98–99¢ no queda nada que ganar), el de mañana aún no tiene un favorito
  claro y quedan pocos partidos.
- **«Cancelada» en Actividad**: el bot retiró una de sus órdenes de compra que esperaban en el libro; no cuesta
  nada. Al lado va el motivo: «la mueve a 92¢» (alguien se puso delante o el precio cambió), «ya no cumple las
  condiciones para comprar» (el precio salió de la banda o el spread se abrió), «cierra en 10 min», «ya hay
  dinero en otro mercado del mismo evento», «para vender la posición» o «el bot se detiene». Si alguien le vende
  solo una fracción de contrato, la orden se queda en la cola en vez de rehacerse.
- **Cancelar una orden del bot desde el panel**: el bot deja de comprar en ese mercado hasta que lo reinicies (si
  no, la volvería a poner en la siguiente vuelta). Sigue pudiendo vender lo que tenga ahí.
- **La barra de abajo se queda a media pantalla (iPhone)**: es un fallo de iOS 26 con apps instaladas al
  cerrar el teclado. El panel lo corrige solo; si aun así pasa, desliza hacia arriba o cierra y abre la app.
