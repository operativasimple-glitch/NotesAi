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
Plan de swing trading con cuenta de fondeo (FTMO US), pensado para usarlo cada día desde el iPad:

- **Hoy**: cabina que cambia sola con la hora de Nueva York: qué toca ahora, pasos del día con checklist, sesgo de la semana, noticias fuertes y checklist antes de entrar.
- **Diario**: abre y cierra operaciones (o pega las alertas de TradingView) y calcula el R, el acierto, la peor racha y si estás listo para pagar el challenge. Importa el CSV de FTMO MetriX.
- **TradingView**: los indicadores PSF A (dip en índices) y PSF B (swing macro en EURUSD, USDJPY y oro), con el código en `swing/pine/` y en la propia página.
- **Plan**: firma, reglas, backtest 2005–2026, contexto macro y calculadoras de challenge y de lotaje.

Los datos se guardan en el navegador del dispositivo, con copia de seguridad descargable. No es consejo financiero.
