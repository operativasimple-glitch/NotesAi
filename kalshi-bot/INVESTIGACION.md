# Investigación: ¿dónde está la ventaja en Kalshi? (octubre 2026)

## Resumen

- **No hay un truco gratis.** La mayoría de la gente pierde en Kalshi. El estudio académico más grande
  (más de 300.000 contratos) calcula un rendimiento medio de **−20 %**: quienes toman liquidez (*takers*)
  pierden alrededor de **−32 %** y quienes dejan órdenes en el libro (*makers*) alrededor de **−10 %**.
- **Hay un patrón sólido y documentado: el sesgo favorito–longshot.** Quien compra contratos de menos
  de 10¢ pierde **más del 60 %** de lo que invierte. Los contratos de más de 50¢ dan rendimientos
  **pequeños pero positivos**.
- **Lo más parecido a "el truco"** es ponerse en el lado contrario de quienes compran longshots:
  - comprar el lado **favorito** (88–97¢);
  - con **órdenes limitadas que esperan en el libro** (maker), que pagan la cuarta parte de comisión;
  - en **muchos mercados pequeños e independientes** que se resuelven pronto.

  A esos precios la comisión es casi cero: depende de P × (1 − P), que es mínimo en los extremos.
- **El resto está saturado, cerrado o no está a tu alcance:**
  - arbitraje entre plataformas;
  - latencia en cripto;
  - programas de incentivos.

El bot incluye ahora:

- la estrategia `favorites`;
- un **escáner** de oportunidades;
- una herramienta de **investigación** que mide el sesgo con datos reales de Kalshi, para que lo
  compruebes antes de arriesgar dinero;
- un **barrido de series** que compara las series activas y te dice en cuáles están ganando los
  favoritos.

## ¿Dónde se gana dinero de verdad? (EE. UU., octubre 2026)

En Kalshi el dinero pasa de los *takers* a los *makers*: entre julio de 2021 y mayo de 2026 los takers
perdieron unos 584 millones de dólares ([Whelan y coautores](https://www2.gwu.edu/~forcpgm/2026-001.pdf)).
Estos son los sitios donde se pierde y se gana más, de lo más grande a lo más pequeño.

### 1. Las combinadas: nunca las compres, el dinero está en el otro lado

Es el producto donde más pierde el apostante minorista:

- En 2026, los minoristas perdieron más de 100 millones en combinadas solo entre enero y abril
  ([Sportico](https://www.sportico.com/business/sports-betting/2026/kalshi-parlays-retail-bettor-losses-rfq-1234894471/)).
  Según Bloomberg, hasta 294 millones hasta julio
  ([Gambling Insider](https://www.gamblinginsider.com/news/185043/kalshi-parlay-bettors-lose-more-than-headline-numbers-show-money-betting-against-them-is-masking-it)).
- Pierden 19 centavos de cada dólar en combinadas, frente a 6 en apuestas simples
  ([Detroit News](https://www.detroitnews.com/story/business/personal-finance/2026/07/30/retail-bettors-lose-big-on-complex-combo-bets/91105579007/)).
- Un estudio de 23 millones de operaciones muestra que las combinadas entre partidos distintos están
  **sistemáticamente sobrevaloradas** respecto al producto de sus patas, y más cuantas más patas tienen
  ([arXiv 2607.14430](https://arxiv.org/abs/2607.14430)).

Quien gana es quien hace de "casa": los creadores de mercado que cotizan las combinadas. Uno de ellos,
de 26 años y antes en FanDuel, gana siete cifras al mes
([The American Prospect](https://prospect.org/2026/08/26/house-always-wins-kalshi-prediction-markets/)).

¿Puede hacerlo un bot pequeño? Técnicamente sí: las peticiones de cotización (RFQ) están abiertas a
cualquiera con API. Pero no es fácil:

- **Competencia profesional:** dos bots responden a la mitad de las peticiones.
- **Velocidad:** se emiten unas 135 peticiones por segundo por un canal público de WebSocket
  ([Oddpool](https://www.oddpool.com/research/kalshi-rfq-market-makers)).
- **Comisiones:** desde el 21 de agosto de 2026 quien cotiza paga el 50 % de la comisión de taker
  ([Bitcoin.com News](https://news.bitcoin.com/igaming/kalshis-parlay-maker-fee-brought-26-million-four-weeks/)).
- **Riesgo correlacionado:** si un fin de semana ganan todos los favoritos, pagas muchas combinadas a la vez.

Un dato interesante del mismo estudio de Oddpool: en los mercados que no son de deportes **nadie
respondía** a las peticiones de cotización. Ahí un bot pequeño podría ser el único que cotiza.

### 2. Apuestas deportivas con valor contra Pinnacle

Kalshi **no limita a quien gana**, a diferencia de DraftKings o FanDuel, porque es un mercado entre
usuarios. Por eso los apostantes profesionales se están pasando a Kalshi
([ClawArbs](https://clawarbs.com/blog/prediction-market-value-betting/),
[SmartStake](https://www.smartstake.app/learn/kalshi-vs-sportsbook)).

El método: se toma la cuota de Pinnacle sin su margen como probabilidad justa y se compra en Kalshi
cuando su precio es mejor. Encaja con la estrategia `fair_value` si se le conecta una fuente de cuotas.

El matiz: en la semana 1 de la NFL, los precios de Kalshi eran entre un 7 % y un 25 % más caros que los
de DraftKings o FanDuel para el usuario normal
([Yogonet](https://www.yogonet.com/international/news/2025/09/09/115258-kalshi-trading-hits-441m-in-first-nfl-week-but-analysts-flag-pricing-gap-with-sportsbooks)).
La ventaja aparece en momentos y mercados concretos (props, ligas menos seguidas, antes de que se
mueva la línea), no siempre.

**Ojo con tu estado.** Los contratos deportivos están bloqueados en Massachusetts, Nevada, Washington y
Michigan. En septiembre de 2026 el Sexto Circuito dio la razón a Ohio y Tennessee para regularlos
([CoinDesk](https://www.coindesk.com/policy/2026/09/25/another-appeals-court-rules-against-prediction-market-provider-kalshi-says-sports-contracts-are-subject-to-state-regulations),
[The Block](https://www.theblock.co/news/regulation/2026-09-26-kalshi-loses-appeal-over-ohio-and-tennessee-sports-betting-laws-widening-circuit-split-416937)).

### 3. Favoritos: lo que ya hace el bot

Es una ventaja pequeña pero estructural:

- En mercados de temperatura, apostar contra los longshots dio entre +0,48 y +1,09¢ por contrato tras
  comisiones, en 180 días y 7 ciudades
  ([Schmiedey/kalshi-weather](https://github.com/Schmiedey/kalshi-weather)).
- Hay indicios de que el sesgo se va reduciendo con el tiempo, y por eso conviene medirlo por serie.
  Para eso está el barrido: `research --sweep` o la tarjeta "¿Dónde gana más el bot?" del panel.

### 4. Nichos con datos públicos: clima, Rotten Tomatoes, TSA, gasolina, cultura

Aquí es donde más ha ganado gente normal
([MarketWatch](https://x.com/MarketWatch/status/1925585494504530351)):

- Un trader ganó 100.000 $ en un mes con mercados como la Persona del Año de *Time* o la persona más
  buscada en Google.
- En Rotten Tomatoes, las críticas llegan por tandas y el contrato se liquida con la nota del lunes a
  las 10:00 (hora del Este). Si la nota queda justo en el umbral, pierde: "por encima" es estricto
  ([OddsShopper](https://www.oddsshopper.com/articles/prediction-markets/kalshi-rotten-tomatoes-markets)).

Pero exige estudiar cada mercado y hay poca liquidez. En un backtest de 500 estrategias de temperatura
en Nueva York, solo 70 salieron positivas y la mediana fue −41,6 %
([BotForKalshi](https://www.botforkalshi.com/blog/kalshi-trading-strategies-guide)).

### 5. Crear mercado donde no llegan los grandes

- Un estudiante de Princeton ganó unos 150.000 $ como creador de mercado desde las elecciones de 2024
  ([4AM Club](https://4amclub.substack.com/p/how-to-make-money-trading-on-kalshi)).
- Otro trader, ~165.000 $ con un algoritmo que revisa los mercados nuevos buscando los buenos para
  crear mercado ([MarketWatch](https://x.com/MarketWatch/status/1925585494504530351)).
- Los grandes (Susquehanna) se concentran en los partidos más grandes.

### Extras si vives en EE. UU.

- **Intereses del 4,05 % anual** sobre saldo y posiciones con 250 $ o más
  ([Kalshi](https://help.kalshi.com/faq/interest-apy-on-kalshi)).
- **Promociones de bienvenida** con créditos para cuentas nuevas. Lee las condiciones.
- **Incentivos de liquidez y volumen terminados.** Kalshi registró el 25 de septiembre de 2026 un nuevo
  programa de recompensas por depósito y trading, pendiente de revisión de la CFTC
  ([DefiRate](https://defirate.com/news/kalshi-volume-rewards-cftc-scrutinizes-prediction-markets/)).

### Cuidado con TikTok

Las plataformas de predicción pagan a creadores e *influencers* para aparecer en redes
([Rolling Stone](https://www.rollingstone.com/culture/culture-features/kalshi-polymarket-viral-moments-1235625058/)).
Los vídeos de "gané X dólares" son marketing o casos aislados: nadie publica sus pérdidas. Y hay un
riesgo real de adicción, sobre todo entre hombres jóvenes
([Fortune](https://fortune.com/2026/04/10/prediction-markets-gambling-addiction/)).

## 0. Antes de nada: ¿puedes operar en Kalshi?

- **España:** la DGOJ (Ministerio de Consumo) abrió un expediente sancionador contra Kalshi y
  Polymarket y ordenó a las operadoras **bloquear su acceso** como medida cautelar mientras dura el
  procedimiento. Se notificó en el BOE el 26 de mayo de 2026.
  - Si vives en España, hoy no puedes usar Kalshi legalmente.
  - No uses VPN ni declares otro país de residencia: incumple los términos de Kalshi y la normativa
    española, y te pueden cerrar la cuenta con el dinero dentro.

  Fuentes: [Decrypt](https://decrypt.co/es/369147/espana-bloquea-polymarket-kalshi-licencia-juego-mercados-prediccion/),
  [FocusGN](https://focusgn.com/latinoamerica/la-dgoj-abre-expediente-sancionador-a-polymarket-y-kalshi-y-ordena-su-bloqueo-en-espana),
  [Hipertextual](https://hipertextual.com/internet/espana-bloquea-polymarket-kalshi/).
- **Otros países:** desde octubre de 2025 Kalshi acepta usuarios de más de 140 países (por ejemplo
  México, Colombia, Argentina, Uruguay o Panamá), con restricciones. Consulta la
  [ayuda de Kalshi](https://help.kalshi.com/en/articles/14026044-international-access-eligibility).
- **Si no resides en EE. UU.:**
  - no puedes entrar en los programas de incentivos;
  - no cobras los intereses del 4,05 %.

## 1. Lo que dicen los datos

Bürgi, Deng y Whelan analizaron transacción a transacción más de 300.000 contratos de Kalshi desde
2021 ([CESifo](https://www.ifo.de/en/cesifo/publications/2026/working-paper/makers-and-takers-economics-kalshi-prediction-market),
[PDF](https://www.karlwhelan.com/Papers/Kalshi.pdf), [CEPR](https://cepr.org/publications/dp20631)).

| Qué compras | Resultado medio |
| --- | --- |
| Contratos de menos de 10¢ (longshots) | Pierdes **más del 60 %** de lo invertido |
| Contratos de más de 50¢ (favoritos) | Rendimiento **pequeño y positivo** |
| Media de todos los contratos | ≈ **−20 %** |
| Takers (cruzan el spread) | ≈ **−32 %** |
| Makers (dejan órdenes en el libro) | ≈ **−10 %** |

Cómo se interpreta:

- **Los precios son informativos y aciertan más cuanto más cerca está el cierre.** Ganarle al mercado
  "adivinando" es difícil.
- **El dinero se pierde sobre todo en los longshots.** Mucha gente paga 3–8¢ por algo que pasa bastante
  menos del 3–8 % de las veces: es una lotería.
- **Los makers lo hacen mejor que los takers, pero ni siquiera ellos ganan de media.** La ventaja no está
  en "ser maker" a secas, sino en ser maker en el lado correcto (el favorito).

## 2. Comisiones (vigentes desde el 5 de febrero de 2026)

Fórmulas oficiales ([tabla de Kalshi](https://kalshi.com/docs/kalshi-fee-schedule.pdf),
[explicación](https://pm.wiki/uk/learn/kalshi-fees-explained)):

- **Taker:** `redondeo hacia arriba(0,07 × contratos × P × (1 − P))`
- **Maker:** `redondeo hacia arriba(0,0175 × contratos × P × (1 − P))`, es decir, el 25 % de la tarifa taker.

Comisión por contrato:

| Precio | Taker | Maker | Maker, 1 contrato (redondeo) | Maker, 10+ contratos | Ganancia máxima |
| --- | --- | --- | --- | --- | --- |
| 10¢ | 0,63¢ | 0,16¢ | 1¢ | 0,2¢ | 90¢ |
| 50¢ | 1,75¢ | 0,44¢ | 1¢ | 0,5¢ | 50¢ |
| 90¢ | 0,63¢ | 0,16¢ | 1¢ | 0,2¢ | 10¢ |
| 95¢ | 0,33¢ | 0,08¢ | 1¢ | 0,1¢ | 5¢ |
| 97¢ | 0,20¢ | 0,05¢ | 1¢ | 0,1¢ | 3¢ |

Dos detalles prácticos:

1. **En los extremos la comisión es mínima** (a 95¢, 0,08¢ por contrato como maker), y ahí es justo
   donde el sesgo es mayor.
2. **El redondeo al centavo castiga las órdenes pequeñas.** Comprar 1 contrato a 95¢ paga 1¢ de comisión:
   el 20 % de la ganancia máxima de 5¢. Con 10 o más contratos por orden baja a 0,1¢. Por eso la
   estrategia usa órdenes de 10 contratos por defecto.

## 3. Por qué existe el sesgo (y por qué puede durar)

- **Mucho flujo minorista.** Los deportes son el 80–90 % del volumen de Kalshi y el volumen mensual
  rozó los 53.000 millones de dólares en septiembre de 2026
  ([The Block](https://theblock.co/news/business/2026-09-30-kalshi-ends-trader-incentive-program-417247),
  [Blockworks](https://app.blockworksresearch.com/unlocked/from-betting-to-trading-how-kalshi-is-reshaping-sports-markets)).
  El público prefiere las cuotas altas de los longshots.
- **Pero hay competencia profesional.** Susquehanna (SIG) es el creador de mercado institucional de
  Kalshi desde 2024 y cotiza spreads de ~2¢ en los partidos grandes
  ([BusinessWire](https://www.businesswire.com/news/home/20240403664852/en/Kalshi-Onboards-Its-First-Dedicated-Institutional-Market-Maker),
  [iGaming Business](https://igamingbusiness.com/sports-betting/class-action-suit-against-kalshi-market-makers/)).
  Donde están ellos, el margen es pequeño. Las oportunidades están en los mercados medianos y en la
  "cola larga" que no les compensa.
- **El estudio cubre 2021–2025.** Con más bots y profesionales el sesgo puede reducirse. Por eso el bot
  trae una herramienta para medirlo con datos recientes (sección 6).

## 4. Estrategias evaluadas

| Idea | Evidencia | Dificultad | Veredicto |
| --- | --- | --- | --- |
| **Comprar favoritos (88–97¢) como maker** | Sesgo favorito–longshot documentado; comisión mínima en extremos | Baja | ✅ **Principal.** Estrategia `favorites` |
| **Valor justo con tu propio modelo** (clima, economía, deportes) | Funciona solo si tu modelo es mejor que el precio | Media–alta | ✅ Si tienes modelo. Estrategia `fair_value` |
| **Creador de mercado** | Los makers pierden menos que los takers, pero compites con SIG | Media | ⚠️ Solo en mercados con spread amplio. Estrategia `market_maker` |
| **Arbitraje en eventos de varios resultados** | Existe, pero el "sobreprecio" normal es de 1,5–2,5 puntos y se lo comen las comisiones | Media | ⚠️ Raro. El escáner lo detecta |
| **Arbitraje Kalshi–Polymarket** | Diferencias de 1–2¢ que desaparecen tras comisiones; reglas de resolución distintas | Alta | ❌ No implementado |
| **Latencia en cripto de 15 minutos** | Requiere servidores en Chicago a ~1 ms; dominado por profesionales | Muy alta | ❌ No es para nosotros |
| **Programas de incentivos** | Liquidez terminó el 1/9/2026; volumen termina el 13/10/2026 entre acusaciones de *wash trading* | — | ❌ Terminados y solo para EE. UU. |
| **Intereses (4,05 % anual)** | Sobre saldo y posiciones, solo EE. UU. con 250 $ o más | — | Pequeño extra si te aplica |

Fuentes:

- Arbitraje: [pm.wiki](https://pm.wiki/es/learn/prediction-market-arbitrage-after-fees),
  [Prediction Hunt](https://www.predictionhunt.com/blog/kalshi-vs-polymarket-arbitrage),
  [sobreprecio en eventos](https://simplefunctions.dev/concepts/event-overround).
- Latencia: [QuantVPS](https://www.quantvps.com/blog/running-kalshi-bots-on-a-vps),
  [Indie Hackers](https://www.indiehackers.com/post/latency-arbitrage-in-15-minute-crypto-markets-building-a-polymarket-trading-edge-2026-f77cc226c0).
- Incentivos: [ayuda de Kalshi](https://help.kalshi.com/incentive-programs/liquidity-incentive-program),
  [Gambling.com](https://www.gambling.com/us/news/kalshi-end-volume-incentive-program-october-13).
- Intereses: [ayuda de Kalshi](https://help.kalshi.com/faq/interest-apy-on-kalshi).

### Ideas concretas por categoría (si quieres un modelo propio)

- **Clima (temperatura máxima).** Liquidan con el informe climático diario del NWS de una estación
  concreta (por ejemplo, Central Park en Nueva York). El pronóstico "en bruto" tiene sesgos conocidos
  respecto a esa estación: Miami ~3 °F, Nueva York ~1 °F. Corregirlos y combinar modelos (NWS + GFS)
  es una ventaja real
  ([ayuda de Kalshi](https://help.kalshi.com/markets/popular-markets/weather-markets),
  [ejemplo de herramienta](https://mcpservers.org/es/servers/rjw34/weather-edge-mcp)).
- **Deportes.** Las casas de apuestas grandes suelen tener líneas más afinadas que Kalshi para el
  público ([Yogonet](https://www.yogonet.com/international/news/2025/09/09/115258-kalshi-trading-hits-441m-in-first-nfl-week-but-analysts-flag-pricing-gap-with-sportsbooks)).
  Sus probabilidades, sin el margen, sirven como "valor justo".
- **Economía (Fed, IPC).** Las probabilidades de CME FedWatch o los *nowcasts* de la Fed de Cleveland
  son buenas referencias de valor justo.

## 5. Cuánto se puede ganar de verdad

Ejemplo ilustrativo de la estrategia de favoritos:

- compras a **94¢** algo que en realidad gana el **96,5 %** de las veces;
- 20 contratos por mercado, en 100 mercados independientes.

| | Resultado |
| --- | --- |
| Ganancia esperada por contrato (tras comisión maker de 0,1¢) | +2,4¢ (+2,6 %) |
| Ganancia esperada total | ≈ **+48 $** |
| Variación típica (1 desviación) | ± 37 $ |
| Capital inmovilizado con ~20 posiciones abiertas a la vez | ≈ 376 $ |
| Si en realidad gana el 94 % (precio justo) | ≈ 0 (pierdes las comisiones) |
| Si gana el 92 % | −2,1¢ por contrato |

Conclusiones:

- La ventaja **es pequeña** y necesita **muchas** operaciones para notarse. Un mes concreto puede salir
  en negativo aunque la estrategia sea buena.
- **Un solo fallo a 95¢ borra 19 aciertos.** El control de riesgo (poco por mercado, uno por evento)
  importa más que cualquier otra cosa.
- Si el sesgo desaparece, la estrategia pasa a perder poco a poco. **Mídelo antes** (siguiente sección).

## 6. Cómo usa el bot esta investigación

1. **`research` (o la pestaña Investigación del panel)** descarga mercados ya liquidados y sus
   operaciones. Calcula, por tramo de precio, cuánto ganó o perdió quien compró como taker y como maker,
   como en el estudio pero con datos actuales y en la categoría que elijas. Si en tus series comprar a
   90–97¢ no sale positivo, no actives `favorites` ahí.
2. **`scan` (o la pestaña Oportunidades)** lista, ahora mismo:
   - favoritos que cumplen los filtros;
   - mercados con spread amplio, para el creador de mercado;
   - eventos de varios resultados donde comprar NO en todos deja beneficio tras comisiones.
3. **Estrategia `favorites`**:
   - pone órdenes de compra *post-only* (nunca paga la comisión taker) en el lado favorito, entre
     `min_price` y `max_price`;
   - mejora en un tick la mejor oferta sin cruzar el spread;
   - 10 contratos por orden;
   - opera mercados que cierran en las próximas 48 h, uno por evento.

## 7. Riesgos y reglas

- **Riesgo de cola:** un favorito que pierde cuesta 88–97¢ por contrato. Limita el tamaño por mercado y
  la exposición total (`[risk]`).
- **Correlación:** no compres varios favoritos del mismo evento (por ejemplo, varias franjas de
  temperatura del mismo día). El bot permite limitar a uno por evento (`max_markets_per_event = 1`).
- **Reglas de Kalshi:** prohíben el *wash trading*, el *spoofing*, el *layering* y operar con
  información privilegiada ([términos](https://www.cftc.gov/filings/orgrules/rules0726243839.pdf)).
  El bot solo pone órdenes reales que está dispuesto a ejecutar; no lo uses para inflar volumen.
- **Impuestos:** las ganancias tributan según tu país.
- **Rentabilidad pasada no garantiza la futura.** Los propios datos del estudio muestran que el
  participante medio pierde.

## Fuentes

- Bürgi, Deng y Whelan (2025–2026), *Makers and Takers: The Economics of the Kalshi Prediction Market*:
  [CESifo](https://www.ifo.de/en/cesifo/publications/2026/working-paper/makers-and-takers-economics-kalshi-prediction-market),
  [PDF](https://www.karlwhelan.com/Papers/Kalshi.pdf),
  [UCD WP2025/19](https://www.ucd.ie/economics/t4media/WP2025_19.pdf),
  [VoxEU](https://cepr.org/voxeu/columns/economics-kalshi-prediction-market).
- Comisiones: [tabla oficial](https://kalshi.com/docs/kalshi-fee-schedule.pdf),
  [pm.wiki](https://pm.wiki/uk/learn/kalshi-fees-explained),
  [River Markets](https://www.rivermarkets.com/insights/kalshi-fees.html).
- Incentivos: [Kalshi](https://help.kalshi.com/incentive-programs/liquidity-incentive-program),
  [The Block](https://theblock.co/news/business/2026-09-30-kalshi-ends-trader-incentive-program-417247),
  [Gambling.com](https://www.gambling.com/us/news/kalshi-end-volume-incentive-program-october-13),
  [Unchained](https://unchainedcrypto.com/kalshi-ends-its-trader-volume-rewards-a-year-early-amid-wash-trading-allegations/).
- Intereses: [Kalshi](https://help.kalshi.com/faq/interest-apy-on-kalshi).
- Acceso internacional y España:
  [Kalshi](https://help.kalshi.com/en/articles/14026044-international-access-eligibility),
  [Decrypt](https://decrypt.co/es/369147/espana-bloquea-polymarket-kalshi-licencia-juego-mercados-prediccion/),
  [CoinCentral](https://coincentral.com/spain-orders-isps-to-block-polymarket-and-kalshi-access/).
- Volumen y competencia: [DefiRate](https://defirate.com/prediction-markets/world-cup-odds/volume/),
  [a16z crypto](https://www.a16zcrypto.com/posts/article/prediction-markets-beyond-sports-betting),
  [The American Prospect](https://prospect.org/2026/08/26/house-always-wins-kalshi-prediction-markets/).
- Rentabilidad de bots: [Tech Insider](https://tech-insider.org/prediction-markets/kalshi-trading-bot/),
  [DigitechBytes](https://digitechbytes.com/emerging-consumer-tech-explained/are-polymarket-trading-bots-actually-profitable-the-math-behind-2026-s-predictio/).
