# VWAP ±2σ · Reversión en el Nasdaq (TradingView)

`vwap_2sigma_nasdaq.pine` es una **estrategia en Pine Script v6** que hace todo el setup de una vez:
dibuja el VWAP con sus bandas, marca las señales, envía las alertas y gestiona la orden (stop, objetivo
y breakeven). Al ser una estrategia, también puedes ver cómo le habría ido en el **Probador de estrategias**
antes de arriesgar dinero.

## Instalación

1. Abre un gráfico de **MNQ1!** (Micro Nasdaq, 2 $/punto) o **NQ1!** (20 $/punto) en **2 m o 5 m**.
2. Abajo, **Editor de Pine** → pega el contenido de `vwap_2sigma_nasdaq.pine` → **Guardar** → **Añadir al gráfico**.
3. Si tienes también el VWAP nativo con anclaje *Session* y fuente *hlc3*, las líneas coinciden.

## Reglas que aplica

| Regla | Valor por defecto |
|---|---|
| VWAP | Anclaje *Sesión*, fuente `hlc3` |
| Bandas | 1σ (×1.0) y 2σ (×2.0); no hay banda 3 |
| Compra | El cierre cruza **hacia abajo** la banda inferior 2σ |
| Venta | El cierre cruza **hacia arriba** la banda superior 2σ |
| Stop / objetivo | 20 / 40 puntos desde la entrada |
| Breakeven | Al ir +20 puntos a favor, el stop pasa a la entrada |
| Horario | Solo 09:30–16:00 (hora de Nueva York); cierra lo abierto al terminar |
| Posiciones | Una a la vez, 1 contrato |

Todo se cambia en **Configuración → Entradas**.

## Alertas

- **Con el script (recomendado):** *Crear alerta* → Condición: `VWAP 2σ` → **Solo llamadas a la función alert()**.
  Recibirás mensajes como `COMPRA MNQ1! ~21500.25 · SL 21480.25 · TP 21540.25 · BE en 21520.25`,
  el aviso `BREAKEVEN … Mueve el stop a …` y `CIERRA … fin del horario`.
- **A mano, como en la guía:** Condición `MNQ1!` → *Cruce hacia abajo* → `VWAP 2σ: Banda inferior 2σ`
  (compra) y *Cruce hacia arriba* → `Banda superior 2σ` (venta). Estas no filtran horario ni posición abierta.

## A tener en cuenta

- **El anclaje *Sesión* en futuros no reinicia a las 09:30.** En NQ1!/MNQ1! con horario extendido la sesión
  empieza a las 18:00 de Nueva York. Para reiniciar con la campana, elige *Anclaje → Apertura NY 09:30*
  (o activa solo horario regular, RTH, en el gráfico).
- **Tamaño:** 20 puntos son 40 $ por contrato en MNQ y 400 $ en NQ. Con poco capital, usa MNQ.
- **Es una estrategia contra tendencia.** En días de tendencia el precio "camina" por la banda 2σ y salta
  varios stops seguidos. Revisa el Probador de estrategias en varios meses antes de operarla.
- **Breakeven:** compara el resultado con *Mover el stop a breakeven* activado y desactivado; en el Nasdaq
  muchas operaciones tocan +20 y vuelven a la entrada antes de llegar a +40.
- En el backtest, si una vela toca stop y objetivo a la vez, TradingView supone el orden dentro de la vela.
  Las comisiones (0,50 $/contrato por lado) y el deslizamiento (1 tick) se ajustan en **Propiedades**.
