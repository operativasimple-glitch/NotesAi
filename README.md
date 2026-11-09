# NotesAi
App para apuntes gratis

## Radar Ballena (`radar/`)
App gratis para seguir el mercado cripto desde el móvil, sin servidor y sin suscripción:

- **Termómetro del mercado**: 8 señales (miedo y codicia, precio, funding, largos/cortos, liquidaciones, flujo de ballenas, interés abierto y libro de órdenes) en una nota de 0 a 100.
- **Ballenas en directo**: órdenes grandes en Hyperliquid con la cartera que las hizo, posiciones abiertas de las 40 mayores carteras y hacia dónde apuestan.
- **Liquidaciones**: en directo (OKX) y mapa de zonas de liquidación (estimado + niveles reales de las ballenas).
- **Carteras**: sigue las que quieras, sin límite, con avisos cuando abren, cierran o cambian posiciones.
- **Avisos 24/7**: con la app instalada en la pantalla de inicio, un servidor revisa cada minuto las carteras que sigues y te manda una notificación aunque la app esté cerrada.
- **Famosos**: la ballena «insider» de los aranceles de Trump, la multisig de World Liberty (familia Trump), Machi Big Brother y James Wynn (atribuciones de Arkham, Lookonchain y Etherscan).
- **Asistente**: responde con los datos en directo usando Google Gemini (clave gratuita, la misma que la app de apuntes).

Fuentes públicas: Hyperliquid, OKX, Gate.io y alternative.me. No es consejo financiero.

### Servidor de avisos (`supabase/`)
Función `rb` de Supabase (proyecto trading-journal) más tablas `rb_*` y una tarea `pg_cron` que la llama cada minuto. Revisa posiciones en Hyperliquid y movimientos de tokens en Ethereum, y envía Web Push cifrado (RFC 8291/8292, sin dependencias). Las claves VAPID se generan y se guardan en la base de datos.
