"""La Probadora: prueba en vivo, con apuestas de mentira, cambios en cómo Kali juega los partidos.

Sigue los partidos de Kalshi (las mismas series que Kali) y aplica la regla de Kali —comprar el
favorito entre 88 y 97¢ con poco spread, un mercado por partido, nunca en los últimos 15 minutos—
de varias maneras a la vez:

  tal cual       como juega Kali (5 contratos, desde 88¢, sin límite de partidos)
  max3           como mucho 3 partidos abiertos a la vez
  menos          3 contratos por partido
  desde92        solo favoritos desde 92¢

Todo es de mentira: no compra nada. Supone que se habría llevado el precio de compra que había en
ese momento (como hace Kali, que espera en el libro). Cuando el partido se decide, se apunta lo
que habría ganado o perdido cada manera, con la comisión de maker de Kalshi.
"""

from __future__ import annotations

import math
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional
from zoneinfo import ZoneInfo

from ..council.markets import parse_time
from ..names import cents, market_name, money, plural
from ..sources.kalshi_public import KalshiPublic, price
from .base import Agent

GAME_SERIES = ("KXMLBGAME", "KXNFLGAME", "KXNHLGAME", "KXNBAGAME", "KXNCAAFGAME")
MAX_PRICE = Decimal("0.97")
MAX_SPREAD = Decimal("0.04")
HORIZON_HOURS = 6.0  # partidos que acaban en las próximas 6 h (como Kali)
LAST_MINUTES = 15  # en los últimos 15 minutos no se compra
MAKER_FEE = Decimal("0.0175")

# (id, nombre, desde qué precio, contratos, partidos abiertos a la vez; 0 = sin límite)
VARIANTS = (
    ("tal_cual", "Kali tal cual", Decimal("0.88"), 5, 0),
    ("max3", "Máx. 3 partidos a la vez", Decimal("0.88"), 5, 3),
    ("menos", "3 contratos por partido", Decimal("0.88"), 3, 0),
    ("desde92", "Solo desde 92¢", Decimal("0.92"), 5, 0),
)


def maker_fee(count: int, paid: Decimal) -> Decimal:
    raw = MAKER_FEE * count * paid * (1 - paid)
    return Decimal(math.ceil(raw * 100)) / 100


def game_end(market: dict) -> Optional[datetime]:
    """Cuándo acaba de verdad el partido (Kalshi pone el cierre oficial días después)."""
    return parse_time(market.get("expected_expiration_time")) or parse_time(market.get("close_time"))


def favorite(market: dict) -> Optional[tuple]:
    """("SÍ" | "NO", precio de compra como maker) si hay favorito en 88–97¢ con poco spread."""
    bid, ask = price(market, "yes_bid"), price(market, "yes_ask")
    if bid is None or ask is None or ask - bid > MAX_SPREAD:
        return None
    if Decimal("0.88") <= bid <= MAX_PRICE:
        return "SÍ", bid
    no_bid = 1 - ask
    if Decimal("0.88") <= no_bid <= MAX_PRICE:
        return "NO", no_bid
    return None


def iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).isoformat()


class Probadora(Agent):
    id = "probadora"
    name = "Probadora"
    role = "Prueba de mentira otras formas de jugar los partidos"
    about = (
        "Sigue los partidos de Kalshi y apuesta de mentira como Kali, pero de cuatro maneras a la vez: tal "
        "cual, con como mucho 3 partidos a la vez, con 3 contratos y solo desde 92¢. Cuando acaba cada "
        "partido apunta cuál habría ganado más. No compra nada."
    )
    home = "pruebas"
    color = "#5ee7f0"
    interval = 300.0

    def __init__(self, world, kalshi: KalshiPublic, tz: str = "America/Chicago", clock=time.time):
        super().__init__(world, clock)
        self.kalshi = kalshi
        self.tz = ZoneInfo(tz)

    # --- la vuelta ---------------------------------------------------------------------

    def tick(self, now: float) -> None:
        when = datetime.fromtimestamp(now, tz=timezone.utc)
        self.doing("pruebas", "mira los partidos que acaban pronto")
        decided = self._settle(when)  # primero se cobran los acabados: dejan hueco a los nuevos
        games = self._games(when)
        opened = self._enter(games, when)
        board = self._board(when)
        self._speak(opened, decided, board)
        books = self.memory.get("books") or {}
        open_games = len({t for b in books.values() for t in b})
        leader = board[0] if board and board[0]["trades"] else None
        self.status(
            f"Pruebas en partidos · {plural(open_games, 'partido abierto', 'partidos abiertos')} de mentira",
            mood="happy" if decided else "ok",
            variants=board,
            open=[t for t in (books.get("tal_cual") or {}).values()][-12:],
            last=list(reversed((self.memory.get("settled") or {}).get("tal_cual", [])[-8:])),
            games_seen=len(games),
            at=iso(when),
        )
        lead = f" · va mejor «{leader['name']}»" if leader else ""
        self.doing("pruebas", f"prueba 4 maneras en {plural(len(games), 'partido', 'partidos')}{lead}")

    # --- los partidos ------------------------------------------------------------------

    def _games(self, when: datetime) -> list:
        found, errors = [], 0
        for series in GAME_SERIES:
            try:
                found.extend(self.kalshi.series_markets(series, max_pages=3))
            except Exception:  # noqa: BLE001 - una serie que falla no para a las demás
                errors += 1
        if errors == len(GAME_SERIES):
            raise RuntimeError("Kalshi no contesta")
        games = []
        for m in found:
            end = game_end(m)
            if end is None:
                continue
            left = (end - when).total_seconds() / 60
            if LAST_MINUTES < left <= HORIZON_HOURS * 60:
                games.append(m)
        return games

    def _enter(self, games: list, when: datetime) -> list:
        """Abre las apuestas de mentira de cada manera. Devuelve las nuevas de "tal cual"."""
        books = self.memory.setdefault("books", {})
        events = self.memory.setdefault("events", {})  # manera -> eventos ya jugados
        opened = []
        # De los que acaban antes a los que acaban después, como los vería Kali.
        for m in sorted(games, key=lambda g: game_end(g) or when):
            fav = favorite(m)
            if fav is None:
                continue
            side, paid = fav
            ticker, event = m.get("ticker", ""), m.get("event_ticker") or m.get("ticker", "")
            for vid, _name, floor, size, max_open in VARIANTS:
                book = books.setdefault(vid, {})
                played = events.setdefault(vid, [])
                if paid < floor or event in played or ticker in book:
                    continue
                if max_open and len(book) >= max_open:
                    continue
                fee = maker_fee(size, paid)
                trade = {
                    "ticker": ticker,
                    "event": event,
                    "name": market_name(ticker, m.get("title", ""), m.get("yes_sub_title", "")),
                    "side": side,
                    "price": str(paid),
                    "count": size,
                    "fee": str(fee),
                    "at": iso(when),
                    "ends": iso(game_end(m) or when),
                }
                book[ticker] = trade
                played.append(event)
                del played[:-800]
                if vid == "tal_cual":
                    opened.append(trade)
        return opened

    def _settle(self, when: datetime) -> list:
        books = self.memory.setdefault("books", {})
        settled = self.memory.setdefault("settled", {})
        due = sorted(
            {
                t
                for book in books.values()
                for t, trade in book.items()
                if (parse_time(trade.get("ends")) or when) < when - timedelta(minutes=10)
            }
        )
        if not due:
            return []
        results = {}
        for m in self.kalshi.markets_by_ticker(due[:80]):
            if m.get("result") in ("yes", "no"):
                results[m["ticker"]] = m["result"]
        decided = []
        for vid, book in books.items():
            for ticker in list(book):
                trade = book[ticker]
                if ticker in results:
                    book.pop(ticker)
                    won = (results[ticker] == "yes") == (trade["side"] == "SÍ")
                    paid, count = Decimal(trade["price"]), int(trade["count"])
                    net = (count if won else 0) - paid * count - Decimal(trade["fee"])
                    trade.update(result=results[ticker], won=won, pnl=str(net), settled_at=iso(when))
                    done = settled.setdefault(vid, [])
                    done.append(trade)
                    del done[:-400]
                    decided.append((vid, trade))
                elif (parse_time(trade.get("ends")) or when) < when - timedelta(days=5):
                    book.pop(ticker)  # cinco días sin resultado: se olvida
        return decided

    # --- el marcador --------------------------------------------------------------------

    def _board(self, when: datetime) -> list:
        settled = self.memory.get("settled") or {}
        books = self.memory.get("books") or {}
        rows = []
        for vid, name, floor, size, max_open in VARIANTS:
            done = settled.get(vid) or []
            pnl = sum((Decimal(t["pnl"]) for t in done), Decimal(0))
            cost = sum((Decimal(t["price"]) * int(t["count"]) for t in done), Decimal(0))
            by_day: dict = {}
            for t in done:
                day = (parse_time(t.get("settled_at")) or when).astimezone(self.tz).date().isoformat()
                by_day[day] = by_day.get(day, Decimal(0)) + Decimal(t["pnl"])
            rows.append(
                {
                    "id": vid,
                    "name": name,
                    "rule": f"desde {cents(floor)} · {size} contratos"
                    + (f" · máx. {max_open} a la vez" if max_open else ""),
                    "trades": len(done),
                    "wins": sum(1 for t in done if t.get("won")),
                    "pnl": float(round(pnl, 2)),
                    "roi": float(round(pnl / cost, 4)) if cost else 0.0,
                    "worst_day": float(round(min(by_day.values()), 2)) if by_day else 0.0,
                    "open": len(books.get(vid) or {}),
                }
            )
        rows.sort(key=lambda r: (-r["pnl"], r["name"]))
        return rows

    # --- lo que cuenta ------------------------------------------------------------------

    def _speak(self, opened: list, decided: list, board: list) -> None:
        losses = [t for vid, t in decided if vid == "tal_cual" and not t.get("won")]
        floor92 = next(floor for vid, _, floor, *_ in VARIANTS if vid == "desde92")
        for t in losses[:2]:
            note = " Solo desde 92¢ no habría entrado." if Decimal(t["price"]) < floor92 else ""
            self.say(
                f"Partido perdido de mentira: «{t['name']}» ({t['side']} a {cents(Decimal(t['price']))}), "
                f"{money(Decimal(t['pnl']), sign=True)}.{note}",
                kind="trade",
                paper=True,
                net=t["pnl"],
            )
        if decided:
            n = len({t["ticker"] for _, t in decided})
            top = board[0]
            self.say(
                f"Se han decidido {plural(n, 'partido', 'partidos')} de las pruebas. Va mejor «{top['name']}»: "
                f"{money(Decimal(str(top['pnl'])), sign=True)} de mentira.",
                kind="info",
            )

    def talk(self, who: Optional[str] = None) -> str:
        detail = self.world.bot(self.id).get("detail") or {}
        rows = [r for r in detail.get("variants") or [] if r.get("trades")]
        if not rows:
            return "Todavía no se ha decidido ningún partido de las pruebas. Vuelve cuando acaben los de hoy."
        parts = [f"«{r['name']}»: {money(Decimal(str(r['pnl'])), sign=True)} en {r['trades']} partidos" for r in rows]
        return "Así van las pruebas de mentira: " + "; ".join(parts) + "."
