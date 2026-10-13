"""La cuenta de mentira de cada estrategia de la Incubadora.

Cada estrategia empieza con 25 $ de mentira y juega con las mismas reglas de riesgo que trae el
bot de verdad:

  - como mucho un 15 % del saldo en un mismo mercado (o en un mismo evento),
  - como mucho un 60 % del saldo en juego a la vez,
  - si un día pierde más del 25 % del saldo con el que empezó, no compra más hasta el día siguiente.

Paga las comisiones de Kalshi: 7 % × contratos × precio × (1 − precio) al comprar lo que ya está en
el libro y 1,75 % al dejar la orden puesta, redondeadas al céntimo de arriba.

Una posición puede tener varias patas (todos los tramos de un evento, para la Contable). Dentro de
un mismo mercado, un SÍ y un NO se compensan como en Kalshi: juntos valen 1 $ seguro, así que la
pareja se cobra en el momento.

Todo se guarda como texto y números sencillos para que quepa en la memoria del pueblo (JSON).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation
from typing import Optional

from ..council.markets import parse_time

ZERO, ONE, CENT = Decimal(0), Decimal(1), Decimal("0.01")
START = Decimal("25")
MARKET_PCT = Decimal("0.15")
EXPOSURE_PCT = Decimal("0.6")
DAILY_LOSS_PCT = Decimal("0.25")
TAKER = Decimal("0.07")
MAKER = Decimal("0.0175")
KEEP = 300  # operaciones decididas que se guardan una a una (los totales se llevan aparte)
CURVE_DAYS = 120
FORGET_DAYS = 7  # una posición sin resultado una semana después de su cierre se anula
SIDES = ("yes", "no")


def dec(value, default: Decimal = ZERO) -> Decimal:
    if value is None or value == "":
        return default
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return default


def text(value: Decimal) -> str:
    """Un importe como texto, sin notación científica (0E-8 → "0.00000000")."""
    return format(value, "f")


def fee(count, price: Decimal, rate: Decimal) -> Decimal:
    """Comisión de Kalshi de una orden: tasa × contratos × precio × (1 − precio), al céntimo de arriba."""
    count = Decimal(count)
    if count <= 0 or rate <= 0:
        return ZERO
    return (rate * count * price * (ONE - price)).quantize(CENT, rounding=ROUND_CEILING)


def new_account(day: str, start: Decimal = START) -> dict:
    return {
        "start": text(start),
        "cash": text(start),
        "since": day,
        "open": {},
        "settled": [],
        "totals": {"ops": 0, "wins": 0, "losses": 0, "pnl": "0", "spent": "0"},
        "day": {"date": day, "start": text(start), "paused": False},
        "curve": [],
    }


def _later(a: Optional[str], b: Optional[str]) -> Optional[str]:
    ta, tb = parse_time(a), parse_time(b)
    if ta is None:
        return b
    if tb is None:
        return a
    return a if ta >= tb else b


def legs_held(pos: dict) -> Decimal:
    """Lo que costaron los contratos que quedan en una posición (las parejas ya cobradas no cuentan)."""
    return sum((dec(leg.get("yes_cost")) + dec(leg.get("no_cost")) for leg in pos["legs"].values()), ZERO)


def describe(pos: dict) -> list:
    """Las patas de una posición para enseñarlas: [{ticker, side, count, price}] de lo que queda."""
    out = []
    for ticker, leg in pos["legs"].items():
        for side in SIDES:
            n = int(leg.get(side) or 0)
            if n:
                out.append(
                    {
                        "ticker": ticker,
                        "side": "SÍ" if side == "yes" else "NO",
                        "count": n,
                        "price": float(round(dec(leg.get(f"{side}_cost")) / n, 4)),
                    }
                )
    return out


class Ledger:
    """Las cuentas de una estrategia, sobre el dict de su memoria (que se guarda tal cual)."""

    def __init__(self, account: dict):
        self.a = account

    # --- lo que hay ---------------------------------------------------------------------

    @property
    def cash(self) -> Decimal:
        return dec(self.a.get("cash"))

    @property
    def open(self) -> dict:
        return self.a.setdefault("open", {})

    def held(self, key: str) -> Decimal:
        pos = self.open.get(key)
        return legs_held(pos) if pos else ZERO

    def exposure(self) -> Decimal:
        return sum((legs_held(p) for p in self.open.values()), ZERO)

    def equity(self) -> Decimal:
        """Saldo de mentira: lo libre más lo que costó lo que sigue en juego (sin adivinar lo que vale)."""
        return self.cash + self.exposure()

    def position(self, key: str) -> Optional[dict]:
        return self.open.get(key)

    def net(self, key: str, ticker: str) -> int:
        """SÍ menos NO que quedan en un mercado (positivo: hay SÍ de más)."""
        pos = self.open.get(key)
        leg = (pos or {}).get("legs", {}).get(ticker) or {}
        return int(leg.get("yes") or 0) - int(leg.get("no") or 0)

    # --- los límites -----------------------------------------------------------------------

    def new_day(self, day: str) -> None:
        """Al cambiar de día apunta cómo acabó el anterior y empieza la cuenta del nuevo."""
        current = self.a.get("day")
        if not current:
            self.a["day"] = {"date": day, "start": text(self.equity()), "paused": False}
            return
        if current.get("date") == day:
            return
        curve = self.a.setdefault("curve", [])
        curve.append({"date": current["date"], "start": current.get("start"), "end": text(self.equity())})
        del curve[:-CURVE_DAYS]
        self.a["day"] = {"date": day, "start": text(self.equity()), "paused": False}

    def paused(self) -> bool:
        """True si hoy ya ha perdido más del 25 % del saldo con el que empezó el día."""
        day = self.a.get("day") or {}
        if day.get("paused"):
            return True
        start = dec(day.get("start"))
        if start > 0 and start - self.equity() >= DAILY_LOSS_PCT * start:
            day["paused"] = True
            return True
        return False

    def room(self, key: str) -> Decimal:
        """Cuánto más se puede gastar en esta posición sin saltarse ningún límite."""
        equity = self.equity()
        by_market = MARKET_PCT * equity - self.held(key)
        by_total = EXPOSURE_PCT * equity - self.exposure()
        return max(ZERO, min(by_market, by_total, self.cash))

    def affordable(self, key: str, unit: Decimal, want: int) -> int:
        """Cuántos caben de lo que se quiere, si cada uno cuesta `unit` (con su comisión)."""
        if want <= 0 or unit <= 0 or self.paused():
            return 0
        fits = int((self.room(key) / unit).to_integral_value(rounding=ROUND_FLOOR))
        return max(0, min(int(want), fits))

    # --- comprar ---------------------------------------------------------------------------

    def buy(
        self,
        key: str,
        ticker: str,
        side: str,
        count: int,
        price: Decimal,
        paid_fee: Decimal,
        *,
        name: str,
        ends: Optional[str],
        when: datetime,
        note: str = "",
        sure: bool = False,
    ) -> dict:
        """Apunta una compra ya decidida (y recortada a los límites). `side`: "yes" o "no".

        `key` es la posición: el mercado o, si compra varios tramos a la vez, el evento.
        `sure`: el resultado está asegurado al comprar (la Contable): no hay suerte que medir.
        """
        count, price = int(count), Decimal(price)
        if count <= 0 or side not in SIDES:
            raise ValueError("compra no válida")
        pos = self.open.get(key)
        if pos is None:
            pos = {
                "key": key,
                "name": name,
                "legs": {},
                "realized": "0",
                "fees": "0",
                "spent": "0",
                "opened_at": when.isoformat(timespec="seconds"),
                "ends": ends,
                "note": note,
                "sure": sure,
                "fills": [],
            }
            self.open[key] = pos
        leg = pos["legs"].setdefault(ticker, {"yes": 0, "yes_cost": "0", "no": 0, "no_cost": "0"})
        other = "no" if side == "yes" else "yes"
        cash = self.cash - count * price - paid_fee
        pairs = min(count, int(leg[other]))
        if pairs:
            avg = dec(leg[f"{other}_cost"]) / int(leg[other])
            pos["realized"] = text(dec(pos["realized"]) + pairs * (ONE - avg - price))
            leg[other] = int(leg[other]) - pairs
            leg[f"{other}_cost"] = text(dec(leg[f"{other}_cost"]) - pairs * avg) if leg[other] else "0"
            cash += pairs  # cada pareja SÍ + NO vale 1 $ seguro: se cobra ya
        rest = count - pairs
        leg[side] = int(leg[side]) + rest
        leg[f"{side}_cost"] = text(dec(leg[f"{side}_cost"]) + rest * price)
        pos["fees"] = text(dec(pos["fees"]) + paid_fee)
        pos["spent"] = text(dec(pos["spent"]) + count * price + paid_fee)
        pos["ends"] = _later(pos.get("ends"), ends)
        fills = pos.setdefault("fills", [])
        fills.append(
            {
                "ticker": ticker,
                "side": "SÍ" if side == "yes" else "NO",
                "count": count,
                "price": float(price),
                "at": when.isoformat(timespec="seconds"),
            }
        )
        del fills[:-12]
        self.a["cash"] = text(cash)
        return pos

    # --- cobrar --------------------------------------------------------------------------

    def due(self, when: datetime, wait_minutes: int = 10) -> list:
        """Mercados de las posiciones abiertas que ya deberían estar decididos."""
        limit = when - timedelta(minutes=wait_minutes)
        out = []
        for pos in self.open.values():
            end = parse_time(pos.get("ends"))
            if end is None or end <= limit:
                out.extend(pos["legs"])
        return out

    def settle(self, results: dict, when: datetime) -> list:
        """Cobra las posiciones cuyos mercados ya se han decidido (`results`: ticker → "yes" | "no").

        Devuelve las operaciones cerradas. Si una semana después del cierre sigue sin resultado,
        se anula y se devuelve lo que costó (como hace Kalshi con un mercado anulado).
        """
        closed = []
        for key in list(self.open):
            pos = self.open[key]
            legs = pos["legs"]
            if legs and all(t in results for t in legs):
                payout = sum(int(leg["yes"]) if results[t] == "yes" else int(leg["no"]) for t, leg in legs.items())
                closed.append(self._close(key, Decimal(payout), when, decided=True))
                continue
            end = parse_time(pos.get("ends"))
            if end is not None and end < when - timedelta(days=FORGET_DAYS):
                closed.append(self._close(key, legs_held(pos), when, decided=False))
        return closed

    def _close(self, key: str, payout: Decimal, when: datetime, decided: bool) -> dict:
        pos = self.open.pop(key)
        held = legs_held(pos)
        pnl = dec(pos["realized"]) + payout - held - dec(pos["fees"])
        self.a["cash"] = text(self.cash + payout)
        # Para medir la suerte: lo que quedaba apostado en cada pata (contratos y precio medio).
        bets = []
        if decided and not pos.get("sure"):
            bets = [[b["count"], b["price"]] for b in describe(pos)]
        if not decided:
            result = "anulada"
        else:
            result = "ganada" if pnl > 0 else "perdida" if pnl < 0 else "empate"
        record = {
            "key": key,
            "name": pos["name"],
            "result": result,
            "pnl": float(round(pnl, 4)),
            "spent": float(round(dec(pos["spent"]), 4)),
            "payout": float(payout),
            "fees": float(dec(pos["fees"])),
            "held": describe(pos),
            "fills": len(pos.get("fills") or []),
            "note": pos.get("note", ""),
            "opened_at": pos.get("opened_at"),
            "settled_at": when.isoformat(timespec="seconds"),
            "bets": bets,
        }
        settled = self.a.setdefault("settled", [])
        settled.append(record)
        del settled[:-KEEP]
        totals = self.a.setdefault("totals", {"ops": 0, "wins": 0, "losses": 0, "pnl": "0", "spent": "0"})
        totals["ops"] = int(totals.get("ops", 0)) + 1
        totals["wins"] = int(totals.get("wins", 0)) + (1 if pnl > 0 else 0)
        totals["losses"] = int(totals.get("losses", 0)) + (1 if pnl < 0 else 0)
        totals["pnl"] = text(dec(totals.get("pnl")) + pnl)
        totals["spent"] = text(dec(totals.get("spent")) + dec(pos["spent"]))
        return record

    # --- cómo va ---------------------------------------------------------------------------

    def days(self, today: str) -> int:
        """Días que lleva probándose (contando hoy)."""
        try:
            first = datetime.fromisoformat(str(self.a.get("since") or today)).date()
            now = datetime.fromisoformat(today).date()
        except ValueError:
            return 1
        return max(1, (now - first).days + 1)

    def worst_day(self) -> Decimal:
        """Lo peor que ha ido un día (incluido hoy, con lo ya cobrado)."""
        changes = [dec(p.get("end")) - dec(p.get("start")) for p in self.a.get("curve") or []]
        day = self.a.get("day") or {}
        if day.get("start") is not None:
            changes.append(self.equity() - dec(day.get("start")))
        return min(changes, default=ZERO)

    def drawdown(self) -> Decimal:
        """La mayor caída desde un máximo, en tanto por uno (0,3 = llegó a perder un 30 % desde su mejor día)."""
        points = [dec(self.a.get("start"), START)]
        points += [dec(p.get("end")) for p in self.a.get("curve") or []]
        points.append(self.equity())
        peak, worst = points[0], ZERO
        for value in points:
            peak = max(peak, value)
            if peak > 0:
                worst = max(worst, (peak - value) / peak)
        return worst
