"""Las estrategias de la Incubadora: maneras de ganar distintas de la de Kali.

Kali compra favoritos (88–97¢) y espera a que se decidan. Aquí se prueban, con dinero de mentira,
otras tres que podrían operar a la vez que ella sin pisarse:

  Notaria   compra lo que el termómetro ya ha decidido. La máxima del día solo puede subir: si en
            Chicago ya van 81°, el tramo «78° a 79°» no puede ganar y su NO vale 1 $ seguro. Si
            alguien aún lo vende a 98¢ o menos, lo compra al precio de venta (pagando la comisión
            de tomar del libro). Para no fiarse de una lectura rara usa la segunda más alta del día
            y deja 1° de margen, por si el informe oficial redondea distinto.
  Tendera   hace de tendera en los tramos de temperatura de mañana (y en los de hoy hasta las 10 de
            la mañana, antes de que el termómetro decida nada): deja una orden de compra y otra de
            venta 1¢ mejores que las de los demás y gana la diferencia cuando le compran y le venden.
            Para saber si se las habrían llenado mira las operaciones públicas de Kalshi: si alguien
            vendió a su precio de compra o más barato, esa venta se la habría llevado ella.
  Contable  busca dinero gratis. Los tramos de temperatura de un día (o los dos equipos de un
            partido) se reparten 1 $: solo uno gana. Si comprar el SÍ de todos cuesta menos de 1 $
            con comisiones, o el NO de todos menos de lo que se cobra seguro, lo compra.

Ninguna toca dinero real ni el bot de verdad.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from ..autopsy import market_end, yes_price
from ..brackets import bracket
from ..council.markets import parse_time, weather_day
from ..names import cents, market_name, money, plural
from ..sources.kalshi_public import price
from ..sources.nws import CITIES, climate_day
from .ledger import CENT, MAKER, ONE, SIDES, TAKER, ZERO, Ledger, dec, fee

MONTHS = "ene feb mar abr may jun jul ago sep oct nov dic".split()
TIE_SERIES = ("KXNFLGAME",)  # en la NFL puede haber empate: entonces todos los SÍ pierden


@dataclass
class Data:
    """Lo que la Mentora trae a la Incubadora en cada vuelta."""

    now: datetime
    today: str  # tu día (la fecha local), para las cuentas de cada día
    weather: list = field(default_factory=list)  # mercados de temperatura abiertos de las 7 ciudades
    games: list = field(default_factory=list)  # mercados de partidos abiertos
    cities: dict = field(default_factory=dict)  # lo que mide Nube: ciudad → fila
    trades: Optional[Callable] = None  # operaciones públicas: (ticker, min_ts, max_ts) → lista


def ask_of(market: dict, side: str) -> Optional[Decimal]:
    """Lo que cuesta comprar ese lado ahora mismo (lo más barato que alguien vende)."""
    if side == "yes":
        return price(market, "yes_ask")
    no = price(market, "no_ask")
    if no is not None:
        return no
    bid = price(market, "yes_bid")  # quien compra SÍ a x vende NO a 1 − x
    return ONE - bid if bid is not None else None


def ends_of(market: dict) -> Optional[str]:
    end = market_end(market)
    return end.isoformat() if end else market.get("close_time")


def name_of(market: dict) -> str:
    ticker = market.get("ticker", "")
    return market_name(ticker, market.get("title", ""), market.get("yes_sub_title") or market.get("subtitle") or "")


def volume(market: dict) -> Decimal:
    return dec(market.get("volume_24h_fp"), dec(market.get("volume_24h")))


def side_name(side: str) -> str:
    return "SÍ" if side == "yes" else "NO"


def _stats(mem: dict, today: str) -> dict:
    """Lo que la estrategia ha visto hoy (se empieza de cero cada día)."""
    stats = mem.get("stats")
    if not stats or stats.get("date") != today:
        stats = {"date": today}
        mem["stats"] = stats
    return stats


def _remember(mem: dict, key: str, value: str, keep: int = 400) -> None:
    items = mem.setdefault(key, [])
    items.append(value)
    del items[:-keep]


# --- Notaria: lo que el termómetro ya ha decidido ---------------------------------------------

NOTARY_MARGIN = 1  # °F de más, por si el informe oficial redondea distinto que las lecturas
NOTARY_MAX = Decimal("0.98")  # a 99¢ ya no compensa: 1¢ menos la comisión
NOTARY_FLOOR = Decimal("0.80")  # si lo «seguro» se vende tan barato, algo no cuadra: mejor no tocarlo
NOTARY_CONTRACTS = 10
STALE_HOURS = 3  # lecturas más viejas que esto no deciden nada


def decided(market: dict, cities: dict, now: datetime) -> Optional[tuple]:
    """("yes" | "no", por qué) si lo ya medido hoy decide este mercado de máxima; None si no."""
    ticker = market.get("ticker") or ""
    day = weather_day(ticker)
    if day is None:
        return None
    key, date = day
    city, row = CITIES.get(key), cities.get(key)
    if city is None or not row or date != climate_day(city, now)[0]:
        return None
    confirmed, at = row.get("confirmed"), parse_time(row.get("at"))
    if confirmed is None or at is None or now - at > timedelta(hours=STALE_HOURS):
        return None
    kind = bracket(market.get("yes_sub_title") or market.get("subtitle") or "", ticker)
    if kind is None:
        return None
    seen = f"ya van {confirmed}° en {city.name}"
    if kind[0] == "between" and confirmed >= kind[2] + 1 + NOTARY_MARGIN:
        return "no", f"{seen}: ya no puede quedar entre {kind[1]}° y {kind[2]}°"
    if kind[0] == "below" and confirmed >= kind[1] + 1 + NOTARY_MARGIN:
        return "no", f"{seen}: ya no puede quedar en {kind[1]}° o menos"
    if kind[0] == "above" and confirmed >= kind[1] + NOTARY_MARGIN:
        return "yes", f"{seen}: ya ha llegado a {kind[1]}°"
    return None


class Notaria:
    id = "notaria"
    name = "Notaria"
    color = "#d946ef"
    role = "Compra lo que el termómetro ya ha decidido"
    about = (
        "La máxima del día solo puede subir. Cuando lo ya medido decide un tramo de temperatura (ya van 81° "
        "y el tramo es «78° a 79°»), su NO vale 1 $ seguro. Si alguien aún lo vende a 98¢ o menos, lo compra "
        "de mentira. Gana poco cada vez y casi sin riesgo, si el mercado es lento en darse cuenta."
    )
    rule = "Tramos de hoy ya decididos por lo medido (con 1° de margen) · compra si cuesta 98¢ o menos"

    def step(self, ledger: Ledger, data: Data, mem: dict) -> list:
        stats = _stats(mem, data.today)
        seen, cheap = set(stats.get("seen") or []), set(stats.get("cheap") or [])
        events = []
        for m in data.weather:
            got = decided(m, data.cities, data.now)
            if not got:
                continue
            side, why = got
            ticker = m["ticker"]
            ask = ask_of(m, side)
            seen.add(ticker)
            if ask is not None and ask <= NOTARY_MAX:
                cheap.add(ticker)
            if ask is None or not NOTARY_FLOOR <= ask <= NOTARY_MAX:
                continue
            if ledger.position(ticker) or ticker in (mem.get("bought") or []):
                continue
            count = ledger.affordable(ticker, ask + fee(1, ask, TAKER), NOTARY_CONTRACTS)
            if not count:
                continue
            name = name_of(m)
            ledger.buy(
                ticker,
                ticker,
                side,
                count,
                ask,
                fee(count, ask, TAKER),
                name=name,
                ends=ends_of(m),
                when=data.now,
                note=why,
            )
            _remember(mem, "bought", ticker)
            stats["bought"] = int(stats.get("bought") or 0) + 1
            events.append(
                {
                    "kind": "buy",
                    "ticker": ticker,
                    "name": name,
                    "side": side_name(side),
                    "count": count,
                    "price": float(ask),
                    "why": why,
                }
            )
        stats["seen"], stats["cheap"] = sorted(seen), sorted(cheap)
        return events

    def research(self, mem: dict) -> list:
        stats = mem.get("stats") or {}
        seen, cheap = len(stats.get("seen") or []), len(stats.get("cheap") or [])
        if not seen:
            return ["Hoy todavía no hay ningún tramo decidido por el termómetro (suele pasar a partir de mediodía)."]
        lines = [
            f"Hoy el termómetro ya ha decidido {plural(seen, 'tramo', 'tramos')}: "
            f"{plural(cheap, 'se podía', 'se podían')} comprar a 98¢ o menos."
        ]
        if seen and not cheap:
            lines.append("El mercado se da cuenta enseguida: lo decidido ya está a 99¢ cuando ella llega.")
        return lines

    def doing(self, mem: dict, events: list) -> str:
        if events:
            e = events[0]
            return f"compra {e['side']} a {round(e['price'] * 100)}¢ · {short(e['name'])}"
        seen = len((mem.get("stats") or {}).get("seen") or [])
        if seen:
            return f"revisa los termómetros · {plural(seen, 'tramo decidido', 'tramos decididos')} hoy"
        return "revisa los termómetros de 7 ciudades"


# --- Tendera: compra un poco más caro y vende un poco más barato que nadie --------------------

QUOTE_SPREAD = Decimal("0.04")  # solo donde el libro deja al menos 4¢ entre compra y venta
QUOTE_SIZE = 2
QUOTE_MAX_NET = 4  # como mucho 4 SÍ (o NO) de más en un mismo mercado
QUOTE_MARKETS = 5
QUOTE_STALE = 900.0  # s: si el pueblo estuvo parado más que esto, lo que pasó en medio no cuenta
MID_LOW, MID_HIGH = Decimal("0.10"), Decimal("0.90")
MIN_VOLUME = Decimal("10")
MORNING_HOUR = 10  # en los tramos de hoy solo cotiza antes de las 10 (luego el termómetro decide)
MIN_HOURS_LEFT = 3.0


def quotable(market: dict, now: datetime) -> bool:
    """Si en este tramo tiene sentido dejar órdenes a los dos lados."""
    ticker = market.get("ticker") or ""
    day = weather_day(ticker)
    if day is None:
        return False
    city = CITIES.get(day[0])
    if city is None:
        return False
    lead = (day[1] - climate_day(city, now)[0]).days
    if lead not in (0, 1) or (lead == 0 and now.astimezone(ZoneInfo(city.tz)).hour >= MORNING_HOUR):
        return False
    bid, ask = price(market, "yes_bid"), price(market, "yes_ask")
    if bid is None or ask is None or ask - bid < QUOTE_SPREAD:
        return False
    if not MID_LOW <= (bid + ask) / 2 <= MID_HIGH:
        return False
    close = parse_time(market.get("close_time"))
    if close is None or (close - now).total_seconds() < MIN_HOURS_LEFT * 3600:
        return False
    return volume(market) >= MIN_VOLUME


def matched(quote: dict, trades: list, since: float, until: float) -> list:
    """Lo que le habrían llenado a sus órdenes: [("yes" | "no", contratos, precio)].

    Si alguien vendió SÍ a su precio de compra o más barato, esa venta se la habría llevado ella
    (su orden era la mejor); igual con quien compró SÍ a su precio de venta o más caro.
    """
    bid, ask = dec(quote.get("bid")), dec(quote.get("ask"))
    left = {"yes": int(quote.get("bid_size") or 0), "no": int(quote.get("ask_size") or 0)}
    got = {"yes": 0, "no": 0}
    rows = []
    for t in trades:
        if t.get("is_block_trade"):
            continue
        when = parse_time(t.get("created_time"))
        if when is None or not since <= when.timestamp() < until:
            continue
        count = dec(t.get("count_fp"), dec(t.get("count")))
        rows.append((when, yes_price(t), count, t.get("taker_outcome_side") or t.get("taker_side")))
    rows.sort(key=lambda r: r[0])
    for _, p, n, taker in rows:
        if p is None or n < 1:
            continue
        if taker == "no" and p <= bid and left["yes"] > 0:
            k = min(int(n), left["yes"])
            left["yes"] -= k
            got["yes"] += k
        elif taker == "yes" and p >= ask and left["no"] > 0:
            k = min(int(n), left["no"])
            left["no"] -= k
            got["no"] += k
    return [(s, got[s], bid if s == "yes" else ONE - ask) for s in SIDES if got[s]]


class Tendera:
    id = "tendera"
    name = "Tendera"
    color = "#0d9488"
    role = "Compra barato y vende caro en los tramos de mañana"
    about = (
        "Hace de tendera: en los tramos de temperatura de mañana deja una orden de compra y otra de venta 1¢ "
        "mejores que las de los demás, y gana la diferencia cuando le compran y le venden. Si alguien que sabe "
        "más le vende justo antes de que el precio caiga, pierde: eso es lo que hay que medir."
    )
    rule = "Tramos de mañana (y de hoy antes de las 10) con 4¢ o más de hueco · 2 contratos a cada lado"

    def step(self, ledger: Ledger, data: Data, mem: dict) -> list:
        stats = _stats(mem, data.today)
        quotes = mem.setdefault("quotes", {})
        now_ts = data.now.timestamp()
        events = []
        # 1) Lo que le habrían llenado desde la vuelta anterior.
        for ticker, quote in list(quotes.items()):
            quotes.pop(ticker)
            since = float(quote.get("since") or 0)
            if data.trades is None or now_ts - since > QUOTE_STALE:
                continue
            try:
                trades = data.trades(ticker, int(since), int(now_ts) + 1)
            except Exception:  # noqa: BLE001 - sin datos de esa vuelta, no cuenta
                continue
            for side, count, paid in matched(quote, trades, since, now_ts):
                # Lo que compensa lo que ya tiene (SÍ contra NO) se cobra al momento; lo demás hay que pagarlo.
                leg = ((ledger.position(ticker) or {}).get("legs") or {}).get(ticker) or {}
                opposite = int(leg.get("no" if side == "yes" else "yes") or 0)
                unit = paid + fee(1, paid, MAKER)
                count = min(count, opposite + int(ledger.cash / unit))
                if count <= 0:
                    continue
                ledger.buy(
                    ticker,
                    ticker,
                    side,
                    count,
                    paid,
                    fee(count, paid, MAKER),
                    name=quote.get("name") or ticker,
                    ends=quote.get("ends"),
                    when=data.now,
                    note="órdenes 1¢ mejores que las de los demás",
                )
                stats["fills"] = int(stats.get("fills") or 0) + 1
                events.append(
                    {
                        "kind": "fill",
                        "ticker": ticker,
                        "name": quote.get("name") or ticker,
                        "side": side_name(side),
                        "count": count,
                        "price": float(paid),
                    }
                )
        # 2) Las órdenes de esta vuelta.
        if not ledger.paused():
            chosen = [m for m in data.weather if quotable(m, data.now)]
            chosen.sort(key=lambda m: (ledger.position(m["ticker"]) is None, -volume(m)))
            for m in chosen[:QUOTE_MARKETS]:
                quote = self._quote(ledger, m)
                if quote:
                    quote["since"] = now_ts
                    quotes[m["ticker"]] = quote
        stats["quoted"] = sorted(set(stats.get("quoted") or []) | set(quotes))
        return events

    @staticmethod
    def _quote(ledger: Ledger, m: dict) -> Optional[dict]:
        ticker = m["ticker"]
        bid, ask = price(m, "yes_bid") + CENT, price(m, "yes_ask") - CENT
        net = ledger.net(ticker, ticker)
        pos = ledger.position(ticker)
        leg = (pos or {}).get("legs", {}).get(ticker) or {}
        sizes = {"yes": QUOTE_SIZE if net < QUOTE_MAX_NET else 0, "no": QUOTE_SIZE if net > -QUOTE_MAX_NET else 0}
        prices = {"yes": bid, "no": ONE - ask}

        def need():  # lo que añadiría en juego si se llenara todo (lo que compensa no cuenta)
            adds = {k: max(0, sizes[k] - int(leg.get("no" if k == "yes" else "yes") or 0)) for k in SIDES}
            return sum((adds[k] * (prices[k] + fee(1, prices[k], MAKER)) for k in SIDES), ZERO)

        room = ledger.room(ticker)
        while need() > room and any(sizes.values()):
            biggest = max(SIDES, key=lambda k: sizes[k])
            sizes[biggest] -= 1
        if not any(sizes.values()):
            return None
        return {
            "ticker": ticker,
            "name": name_of(m),
            "ends": ends_of(m),
            "bid": str(bid),
            "ask": str(ask),
            "bid_size": sizes["yes"],
            "ask_size": sizes["no"],
            "book": [float(price(m, "yes_bid")), float(price(m, "yes_ask"))],
        }

    def research(self, mem: dict) -> list:
        stats = mem.get("stats") or {}
        quotes = mem.get("quotes") or {}
        lines = []
        if quotes:
            parts = [
                f"«{short(q['name'])}» compra a {cents(dec(q['bid']))} y vende a {cents(dec(q['ask']))}"
                for q in list(quotes.values())[:3]
            ]
            lines.append(f"Ahora tiene órdenes en {plural(len(quotes), 'tramo', 'tramos')}: " + "; ".join(parts) + ".")
        else:
            lines.append("Ahora no tiene órdenes: no hay tramos de mañana con hueco suficiente entre compra y venta.")
        fills = int(stats.get("fills") or 0)
        lines.append(
            f"Hoy le habrían llenado {plural(fills, 'orden', 'órdenes')}."
            if fills
            else "Hoy todavía no le habrían llenado ninguna orden."
        )
        return lines

    def doing(self, mem: dict, events: list) -> str:
        if events:
            e = events[0]
            return f"le compran: {e['count']} {e['side']} a {round(e['price'] * 100)}¢ · {short(e['name'])}"
        quotes = mem.get("quotes") or {}
        if quotes:
            return f"tiene órdenes en {plural(len(quotes), 'tramo', 'tramos')} de mañana"
        return "busca tramos con hueco entre compra y venta"


# --- Contable: tramos que no suman 1 $ ---------------------------------------------------------

SUM_EDGE = CENT  # al menos 1¢ de ganancia segura por juego, ya pagadas las comisiones
SUM_SETS = 5


def event_of(market: dict) -> str:
    return market.get("event_ticker") or market.get("ticker", "").rsplit("-", 1)[0]


def complete(legs: list) -> bool:
    """True si los tramos de un día cubren todas las temperaturas, sin huecos ni repetidos."""
    kinds = [bracket(m.get("yes_sub_title") or m.get("subtitle") or "", m.get("ticker", "")) for m in legs]
    if not kinds or any(k is None for k in kinds):
        return False
    below = [k for k in kinds if k[0] == "below"]
    above = [k for k in kinds if k[0] == "above"]
    between = sorted((k for k in kinds if k[0] == "between"), key=lambda k: k[1])
    if len(below) != 1 or len(above) != 1:
        return False
    edge = below[0][1]
    for k in between:
        if k[1] != edge + 1 or k[2] < k[1]:
            return False
        edge = k[2]
    return above[0][1] == edge + 1


def event_name(legs: list) -> str:
    m = legs[0]
    day = weather_day(m.get("ticker", ""))
    if day:
        city = CITIES.get(day[0])
        place = city.name if city else day[0]
        return f"Máxima en {place} ({day[1].day} {MONTHS[day[1].month - 1]})"
    title = re.sub(r"\s*Winner\?$", "", m.get("title") or "").strip()
    return title or event_of(m)


def sums(legs: list, side: str) -> Optional[tuple]:
    """(coste, comisiones, lo que se cobra seguro) de comprar ese lado de todos los tramos, un juego."""
    prices = [ask_of(m, side) for m in legs]
    if any(p is None for p in prices):
        return None
    cost = sum(prices, ZERO)
    fees = sum((fee(1, p, TAKER) for p in prices), ZERO)
    payout = ONE if side == "yes" else Decimal(len(legs) - 1)
    return cost, fees, payout


class Contable:
    id = "contable"
    name = "Contable"
    color = "#475569"
    role = "Busca dinero gratis: tramos que no suman 1 $"
    about = (
        "Los tramos de temperatura de un día (o los dos equipos de un partido) se reparten 1 $: solo uno gana. "
        "Si comprar el SÍ de todos cuesta menos de 1 $ con comisiones, o el NO de todos menos de lo que se cobra "
        "seguro, eso es dinero gratis y lo compra de mentira. Lo normal es no encontrar nada: también eso enseña."
    )
    rule = "Todos los tramos de un evento a la vez · solo si sale ganando 1¢ o más seguro, con comisiones"

    def step(self, ledger: Ledger, data: Data, mem: dict) -> list:
        stats = _stats(mem, data.today)
        groups = []
        weather: dict = {}
        for m in data.weather:
            weather.setdefault(event_of(m), []).append(m)
        groups += [(event, legs, "weather") for event, legs in weather.items() if complete(legs)]
        games: dict = {}
        for m in data.games:
            games.setdefault(event_of(m), []).append(m)
        groups += [(event, legs, event.split("-", 1)[0]) for event, legs in games.items() if len(legs) == 2]
        stats["checked"] = max(int(stats.get("checked") or 0), len(groups))
        events = []
        for event, legs, kind in groups:
            for side in SIDES:
                if side == "yes" and kind in TIE_SERIES:
                    continue
                got = sums(legs, side)
                if not got:
                    continue
                cost, fees, payout = got
                gap = payout - cost - fees
                best = stats.get("best")
                if best is None or gap > dec(best.get("gap")):
                    stats["best"] = {"gap": str(gap), "name": event_name(legs), "side": side_name(side)}
                if gap < SUM_EDGE or ledger.position(event) or event in (mem.get("done") or []):
                    continue
                sets = ledger.affordable(event, cost + fees, SUM_SETS)
                if not sets:
                    continue
                name = event_name(legs)
                note = (
                    f"el {side_name(side)} de los {len(legs)} cuesta {cents(cost + fees)} con comisiones "
                    f"y se cobra {money(payout)} seguro"
                )
                for m in legs:
                    p = ask_of(m, side)
                    ledger.buy(
                        event,
                        m["ticker"],
                        side,
                        sets,
                        p,
                        fee(sets, p, TAKER),
                        name=name,
                        ends=ends_of(m),
                        when=data.now,
                        note=note,
                        sure=True,
                    )
                _remember(mem, "done", event)
                stats["found"] = int(stats.get("found") or 0) + 1
                events.append(
                    {
                        "kind": "buy",
                        "ticker": event,
                        "name": name,
                        "side": side_name(side),
                        "count": sets,
                        "price": float(cost + fees),
                        "gap": float(gap),
                        "legs": len(legs),
                        "why": note,
                    }
                )
        return events

    def research(self, mem: dict) -> list:
        stats = mem.get("stats") or {}
        checked, found = int(stats.get("checked") or 0), int(stats.get("found") or 0)
        lines = [f"Hoy ha sumado los tramos de {plural(checked, 'evento', 'eventos')} a la vez."]
        if found:
            lines.append(f"Ha encontrado dinero gratis {plural(found, 'vez', 'veces')}.")
        best = stats.get("best")
        if best and not found:
            gap = dec(best.get("gap"))
            lines.append(
                f"Ningún atajo: lo más cerca fue «{best['name']}» (el {best['side']} de todos), "
                f"donde faltaban {cents(-gap)}."
                if gap < 0
                else f"Lo más cerca: «{best['name']}», con {cents(gap)} de ventaja."
            )
        return lines

    def doing(self, mem: dict, events: list) -> str:
        if events:
            e = events[0]
            return f"¡dinero gratis! {e['side']} de los {e['legs']} · {short(e['name'])}"
        best = (mem.get("stats") or {}).get("best")
        if best and dec(best.get("gap")) < 0:
            return f"suma tramos · lo más cerca: faltan {cents(-dec(best['gap']))}"
        return "suma los tramos de cada evento"


def short(name: str) -> str:
    return str(name or "").replace("Máxima en ", "")


STRATEGIES = (Notaria(), Tendera(), Contable())
