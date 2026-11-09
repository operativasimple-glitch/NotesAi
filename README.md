# NotesAi
App para apuntes gratis

## Radar Ballena (`radar/`)
App gratis para seguir el mercado cripto desde el móvil, sin servidor y sin suscripción:

- **Termómetro del mercado**: 8 señales (miedo y codicia, precio, funding, largos/cortos, liquidaciones, flujo de ballenas, interés abierto y libro de órdenes) en una nota de 0 a 100.
- **Ballenas en directo**: órdenes grandes en Hyperliquid con la cartera que las hizo, posiciones abiertas de las 40 mayores carteras y hacia dónde apuestan.
- **Liquidaciones**: en directo (OKX) y mapa de zonas de liquidación (estimado + niveles reales de las ballenas).
- **Carteras**: sigue las que quieras, sin límite, con avisos cuando abren, cierran o cambian posiciones.
- **Asistente**: responde con los datos en directo usando Google Gemini (clave gratuita, la misma que la app de apuntes).

Fuentes públicas: Hyperliquid, OKX, Gate.io y alternative.me. No es consejo financiero.

## Plan Swing (`swing/`)
App de swing trading con cuenta de fondeo (FTMO US), para el ordenador, el iPad o el móvil:

- **Hoy**: un aviso grande que cambia solo con la hora de Nueva York (qué toca ahora y cuenta atrás), pasos del día, sesgo de la semana, noticias, riesgo abierto y posiciones con entrada, SL y TP. Pega las alertas de TradingView y la app abre, mueve a breakeven o cierra la operación, y apunta cada alerta para que sepas que llegan.
- **Journal** al estilo TradeZella: P&L, acierto, profit factor, R medio y drawdown, curva de resultados, calendario de P&L con totales semanales, resultados por setup, por día y por mercado, y tabla de operaciones con etiquetas, valoración y notas. Importa el CSV de FTMO MetriX o MT5 y exporta a CSV.
- **TradingView**: los indicadores PSF A (dip en índices) y PSF B (swing macro en EURUSD, USDJPY y oro) dibujan la entrada, el SL y el TP y traen una alerta de prueba. El código está en `swing/pine/` y en la propia app.
- **Plan** y **Herramientas**: reglas de la cuenta, setups, backtest 2005–2026, simulador del challenge y tamaño de posición.

Los datos se guardan en el navegador del dispositivo, con copia de seguridad descargable. No es consejo financiero.
