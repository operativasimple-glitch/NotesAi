"""El Consejo: la Investigadora trae los mercados y cada miembro da su precio justo.

Los miembros (Cazatormentas, Dinero Listo, Piloto, Miedo, Codicia y, si hay clave, Claude) apuestan
de mentira cuando su precio justo se separa del de Kalshi. La Investigadora apunta esas apuestas y,
cuando los mercados se deciden, lleva el marcador: ¿quién acertó? Nada de esto usa dinero real.
"""

from __future__ import annotations

import statistics
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from zoneinfo import ZoneInfo

from ..council import book
from ..council.claude import ClaudeForecaster, prompt_for
from ..council.markets import WEATHER_SERIES, hours_left, parse_time, topic
from ..council.models import MEMBERS
from ..names import money, plural
from ..sources.kalshi_public import KalshiPublic
from .base import Agent

TOPICS_MAX = 48
DEBATE_EVERY = 1800.0  # una discusión cada media hora como mucho
CATCHPHRASE = {
    "tormenta": "Los números del Servicio Meteorológico no mienten.",
    "dinero": "Mira cómo se mueve el precio: el dinero sabe algo.",
    "piloto": "Los favoritos casi siempre cumplen.",
    "miedo": "¿Y si sale mal? Yo no me fío.",
    "codicia": "¡El que va ganando gana! Lo quiero todo.",
    "claude": "Lo he pensado con calma.",
}


def pct(p: Optional[float]) -> str:
    return "—" if p is None else f"{round(p * 100)} %"


def cents(p: Optional[float]) -> str:
    return "—" if p is None else f"{round(p * 100)}¢"


def iso(when: datetime) -> str:
    return when.isoformat(timespec="seconds")


class Investigadora(Agent):
    id = "investigadora"
    name = "Investigadora"
    role = "Organiza el Consejo y lleva el marcador"
    about = (
        "Cada 10 minutos trae al Consejo los mercados de clima de las 7 ciudades y las apuestas abiertas de "
        "Kali. Apunta las apuestas de mentira de cada miembro y, cuando los mercados se deciden, quién acertó."
    )
    home = "consejo"
    color = "#dcd8ee"
    interval = 600.0

    def __init__(self, world, kalshi: KalshiPublic, members=MEMBERS, clock=time.time):
        self.members = tuple(members)
        self.voices = tuple(m.id for m in self.members)
        super().__init__(world, clock)
        self.kalshi = kalshi
        for m in self.members:
            world.register(m.id, name=m.name, role=m.role, about=m.about, home="consejo", color=m.color)

    # --- la vuelta ---------------------------------------------------------------------

    def tick(self, now: float) -> None:
        when = datetime.fromtimestamp(now, tz=timezone.utc)
        self.doing("laboratorio", "trae los mercados al Consejo")
        topics = self._topics(when)
        ctx = {
            "now": when,
            "cities": {r["key"]: r for r in (self.peer("nube").get("detail") or {}).get("cities") or []},
        }
        fairs = {m.id: self._fairs(m, topics, ctx) for m in self.members if m.model}
        claude = self._claude_fairs(topics, when)
        if claude is not None:
            fairs["claude"] = claude
        new = self._bet(topics, fairs, when)
        self.doing("boveda", "mira qué mercados se han decidido")
        decided = self._settle(when)
        people = self._people(fairs)
        board = book.board(people, self.memory.get("books") or {}, self.memory.get("settled") or {})
        consensus = {t["ticker"]: c for t in topics if (c := self._consensus(t["ticker"], fairs)) is not None}
        self._speak(topics, fairs, new, decided, board, consensus, when)
        self._publish_members(topics, fairs, board, when)
        open_bets = sum(len(b) for b in (self.memory.get("books") or {}).values())
        leader = board[0] if board and board[0]["trades"] else None
        self.status(
            f"Consejo · {plural(len(topics), 'mercado', 'mercados')} · "
            f"{plural(open_bets, 'apuesta abierta', 'apuestas abiertas')} de mentira",
            mood="happy" if decided else "ok",
            topics=[self._public_topic(t, fairs, consensus) for t in topics],
            board=board,
            consensus=consensus,
            open=open_bets,
            leader=leader["id"] if leader else None,
            at=iso(when),
        )
        lead = f" · va ganando {leader['name']}" if leader else ""
        self.doing("consejo", f"modera {plural(len(topics), 'mercado', 'mercados')}{lead}")

    # --- los mercados ------------------------------------------------------------------

    def _topics(self, when: datetime) -> list:
        markets, errors = [], 0
        for series in WEATHER_SERIES:
            try:
                markets.extend(self.kalshi.series_markets(series))
            except Exception:  # noqa: BLE001 - una serie que falla no para a las demás
                errors += 1
        held = [p["ticker"] for p in (self.peer("kali").get("detail") or {}).get("positions") or [] if p.get("ticker")]
        try:
            held_markets = self.kalshi.markets_by_ticker(held) if held else []
        except Exception:  # noqa: BLE001
            held_markets, errors = [], errors + 1
        if errors and not markets and not held_markets:
            raise RuntimeError("Kalshi no contesta")
        chosen: dict = {}
        for m in held_markets:
            t = topic(m, held=True)
            if t:
                chosen[t["ticker"]] = t
        for m in markets:
            t = topic(m)
            if not t or t["ticker"] in chosen or t["mid"] is None or not 0.03 <= t["mid"] <= 0.97:
                continue
            left = hours_left(t, when)
            if left is None or not 0 < left <= 48:
                continue
            chosen[t["ticker"]] = t
        ordered = sorted(chosen.values(), key=lambda t: (not t["held"], t["kind"] != "weather", t["ticker"]))
        return ordered[:TOPICS_MAX]

    def _fairs(self, member, topics: list, ctx: dict) -> dict:
        fairs = {}
        for t in topics:
            fair = member.model(t, ctx)
            if fair is not None:
                fairs[t["ticker"]] = round(fair, 4)
        return fairs

    def _claude_fairs(self, topics: list, when: datetime) -> Optional[dict]:
        claude = self.peer("claude")
        if not claude:
            return None
        tickers = {t["ticker"] for t in topics}
        fairs = {}
        for ticker, o in ((claude.get("detail") or {}).get("opinions") or {}).items():
            at = parse_time(o.get("at"))
            if ticker in tickers and at and (when - at).total_seconds() < 6 * 3600:
                fairs[ticker] = o["fair"]
        return fairs

    def _people(self, fairs: dict) -> list:
        people = [{"id": m.id, "name": m.name, "color": m.color} for m in self.members]
        if "claude" in fairs:
            bot = self.peer("claude")
            people.append({"id": "claude", "name": bot.get("name", "Claude"), "color": bot.get("color", "#eb6834")})
        return people

    @staticmethod
    def _consensus(ticker: str, fairs: dict) -> Optional[float]:
        values = [f[ticker] for f in fairs.values() if ticker in f]
        return round(statistics.median(values), 4) if len(values) >= 2 else None

    # --- apuestas de mentira -------------------------------------------------------------

    def _bet(self, topics: list, fairs: dict, when: datetime) -> list:
        books = self.memory.setdefault("books", {})
        traded = self.memory.setdefault("traded", {})
        new = []
        for t in topics:
            left = hours_left(t, when)
            if left is None or left <= 0.25 or left > book.MAX_HOURS or t["bid"] is None or t["ask"] is None:
                continue
            for member_id, member_fairs in fairs.items():
                fair = member_fairs.get(t["ticker"])
                done = traded.setdefault(member_id, [])
                if fair is None or t["ticker"] in done:
                    continue
                choice = book.decide(fair, t)
                if not choice:
                    continue
                side, price = choice
                trade = {
                    "ticker": t["ticker"],
                    "name": t["name"],
                    "side": side,
                    "price": price,
                    "fee": book.fee(price),
                    "fair": fair,
                    "at": iso(when),
                    "close_time": t.get("close_time"),
                }
                books.setdefault(member_id, {})[t["ticker"]] = trade
                done.append(t["ticker"])
                del done[:-600]
                new.append((member_id, trade))
        return new

    def _settle(self, when: datetime) -> list:
        books = self.memory.setdefault("books", {})
        settled = self.memory.setdefault("settled", {})
        due = sorted(
            {
                ticker
                for trades in books.values()
                for ticker, trade in trades.items()
                if (hours_left({"close_time": trade.get("close_time")}, when) or 0) < -0.05
            }
        )
        if not due:
            return []
        results = {}
        for m in self.kalshi.markets_by_ticker(due[:60]):
            if m.get("result") in ("yes", "no"):
                results[m["ticker"]] = m["result"]
        decided = []
        for member_id, trades in books.items():
            for ticker in list(trades):
                trade = trades[ticker]
                if ticker in results:
                    trades.pop(ticker)
                    trade.update(result=results[ticker], pnl=book.pnl(trade, results[ticker]), settled_at=iso(when))
                    done = settled.setdefault(member_id, [])
                    done.append(trade)
                    del done[:-300]
                    decided.append((member_id, trade))
                elif (hours_left({"close_time": trade.get("close_time")}, when) or 0) < -24 * 7:
                    trades.pop(ticker)  # una semana sin resultado: se olvida
        return decided

    # --- lo que se dicen -----------------------------------------------------------------

    def _name(self, member_id: str) -> str:
        if member_id == "claude":
            return self.peer("claude").get("name", "Claude")
        return next((m.name for m in self.members if m.id == member_id), member_id)

    def _speak(self, topics, fairs, new, decided, board, consensus, when: datetime) -> None:
        memory = self.memory
        # La apuesta nueva más clara se la cuenta su autor a la Investigadora.
        if new:
            member_id, trade = max(new, key=lambda mt: abs(mt[1]["fair"] - mt[1]["price"]))
            worth = trade["fair"] if trade["side"] == "SÍ" else 1 - trade["fair"]
            self.world.say(
                member_id,
                f"Apunta: compro el {trade['side']} de «{trade['name']}» a {cents(trade['price'])}; "
                f"para mí vale {cents(worth)}. De mentira, claro.",
                to=self.id,
                kind="info",
                ticker=trade["ticker"],
            )
            if len(new) > 1:
                self.say(f"Apuntadas {len(new)} apuestas de mentira nuevas en el Consejo.", kind="info")
        # Los resultados: los dos más llamativos los cuenta cada uno; el resto, la Investigadora.
        for member_id, trade in sorted(decided, key=lambda mt: -abs(mt[1]["pnl"]))[:2]:
            if trade["pnl"] > 0:
                text = f"¡Acerté con «{trade['name']}»! {money(_d(trade['pnl']), sign=True)} de mentira."
            else:
                text = f"Fallé con «{trade['name']}»: {money(_d(trade['pnl']), sign=True)} de mentira."
            self.world.say(member_id, text, kind="trade", ticker=trade["ticker"], net=str(trade["pnl"]), paper=True)
        if decided:
            wins = sum(1 for _, t in decided if t["pnl"] > 0)
            leader = board[0] if board and board[0]["trades"] else None
            tail = f" Va ganando {leader['name']} ({money(_d(leader['pnl']), sign=True)})." if leader else ""
            self.say(
                f"Se han decidido {plural(len(decided), 'apuesta', 'apuestas')} del Consejo: "
                f"{plural(wins, 'acierto', 'aciertos')}.{tail}",
                kind="info",
            )
        # Una discusión: el que más cree en un mercado le pregunta al que menos.
        if when.timestamp() - memory.get("debated_at", 0) >= DEBATE_EVERY:
            debated = memory.setdefault("debated", {})
            best = None
            for t in topics:
                opinions = [(mid, f[t["ticker"]]) for mid, f in fairs.items() if t["ticker"] in f]
                if len(opinions) < 2 or when.timestamp() - debated.get(t["ticker"], 0) < 6 * 3600:
                    continue
                hi = max(opinions, key=lambda o: o[1])
                lo = min(opinions, key=lambda o: o[1])
                gap = hi[1] - lo[1]
                if gap >= 0.15 and (best is None or gap > best[0]):
                    best = (gap, t, hi, lo)
            if best:
                _, t, hi, lo = best
                memory["debated_at"] = when.timestamp()
                debated[t["ticker"]] = when.timestamp()
                for ticker in [k for k, v in debated.items() if when.timestamp() - v > 86400]:
                    del debated[ticker]
                self.world.say(
                    hi[0],
                    f"«{t['name']}»: yo le doy un {pct(hi[1])} y tú solo un {pct(lo[1])}, {self._name(lo[0])}. "
                    f"¿Qué sabes tú que yo no sé?",
                    to=lo[0],
                    kind="chat",
                    ticker=t["ticker"],
                )
                self.world.say(lo[0], CATCHPHRASE.get(lo[0], "Ya veremos quién acierta."), to=hi[0], kind="chat")
        # Si el Consejo cree que una apuesta de Kali vale mucho menos de lo que pagó, se lo dice.
        warned = memory.setdefault("kali_warned", {})
        day = when.date().isoformat()
        for p in (self.peer("kali").get("detail") or {}).get("positions") or []:
            c = consensus.get(p.get("ticker"))
            avg = _f(p.get("avg_price"))
            if c is None or avg is None:
                continue
            worth = c if p.get("side") == "SÍ" else 1 - c
            if worth <= avg - 0.10 and warned.get(p["ticker"]) != day:
                warned[p["ticker"]] = day
                self.say(
                    f"Kali, el Consejo cree que tu {p.get('side')} de «{p.get('name')}» vale {cents(worth)} "
                    f"y lo pagaste a {cents(avg)}.",
                    to="kali",
                    kind="alert",
                    ticker=p["ticker"],
                )
        for ticker in [k for k, v in warned.items() if v != day]:
            del warned[ticker]

    # --- lo que enseña cada uno ----------------------------------------------------------

    @staticmethod
    def _public_topic(t: dict, fairs: dict, consensus: dict) -> dict:
        keep = ("ticker", "name", "kind", "city", "date", "bid", "ask", "mid", "close_time", "held", "volume")
        out = {k: t.get(k) for k in keep}
        out["fairs"] = {mid: f[t["ticker"]] for mid, f in fairs.items() if t["ticker"] in f}
        out["consensus"] = consensus.get(t["ticker"])
        return out

    def _publish_members(self, topics: list, fairs: dict, board: list, when: datetime) -> None:
        by_ticker = {t["ticker"]: t for t in topics}
        rows = {r["id"]: r for r in board}
        books = self.memory.get("books") or {}
        settled = self.memory.get("settled") or {}
        for m in self.members:
            mine = fairs.get(m.id) or {}
            calls = []
            for ticker, fair in mine.items():
                t = by_ticker.get(ticker)
                if t and t.get("mid") is not None:
                    calls.append(
                        {"ticker": ticker, "name": t["name"], "fair": fair, "mid": t["mid"], "edge": fair - t["mid"]}
                    )
            calls.sort(key=lambda c: -abs(c["edge"]))
            row = rows.get(m.id) or book.record([])
            if row["trades"]:
                text = f"{row['wins']} de {row['trades']} acertadas · {money(_d(row['pnl']), sign=True)} de mentira"
            else:
                text = "Aún sin apuestas decididas" if books.get(m.id) else "Escuchando al Consejo"
            mood = "happy" if row["pnl"] > 0 else "sad" if row["pnl"] < 0 and row["trades"] >= 3 else "ok"
            self.world.update(
                m.id,
                status=text,
                mood=mood,
                detail={
                    "member": True,
                    "record": {k: row[k] for k in ("trades", "wins", "pnl", "roi")},
                    "calls": calls[:10],
                    "open": list((books.get(m.id) or {}).values())[-12:],
                    "last": list(reversed((settled.get(m.id) or [])[-8:])),
                    "opinions": len(mine),
                },
            )
            if calls:
                c = calls[0]
                self.doing("consejo", f"justo {cents(c['fair'])} · {c['name']} (paga {cents(c['mid'])})", bot_id=m.id)
            else:
                self.doing("consejo", "escucha al Consejo", bot_id=m.id)

    def talk(self, who: Optional[str] = None) -> str:
        if who and who != self.id:
            bot = self.world.bot(who)
            detail = bot.get("detail") or {}
            record = detail.get("record") or {}
            calls = detail.get("calls") or []
            parts = []
            if record.get("trades"):
                parts.append(
                    f"Llevo {record['wins']} de {record['trades']} acertadas: "
                    f"{money(_d(record['pnl']), sign=True)} de mentira."
                )
            else:
                parts.append("Todavía no se ha decidido ninguna de mis apuestas.")
            if calls:
                c = calls[0]
                parts.append(
                    f"Lo que más claro veo: «{c['name']}» vale {pct(c['fair'])} y se paga a {cents(c['mid'])}."
                )
            parts.append(CATCHPHRASE.get(who, ""))
            return " ".join(p for p in parts if p)
        detail = self.world.bot(self.id).get("detail") or {}
        board = [r for r in detail.get("board") or [] if r.get("trades")]
        text = f"Hoy el Consejo mira {plural(len(detail.get('topics') or []), 'mercado', 'mercados')}."
        if board:
            r = board[0]
            text += (
                f" Va ganando {r['name']}: {money(_d(r['pnl']), sign=True)} de mentira ({r['wins']} de {r['trades']})."
            )
        else:
            text += " Aún no se ha decidido ninguna apuesta: el marcador está a cero."
        return text + f" Apuestas abiertas: {detail.get('open', 0)}."


def _d(value) -> Decimal:
    return Decimal(str(value))


def _f(value) -> Optional[float]:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


class ClaudeMember(Agent):
    """Claude en el Consejo: cada pocos minutos piensa un mercado y da su probabilidad."""

    id = "claude"
    name = "Claude"
    role = "Analiza los mercados con IA"
    about = (
        "Lee cada mercado (las reglas, los precios, el tiempo y lo que opinan los demás) y da su probabilidad "
        "con una frase. Cada pregunta cuesta un poco de dinero de tu cuenta de Anthropic, así que tiene un "
        "límite de preguntas al día."
    )
    home = "consejo"
    color = "#eb6834"
    interval = 300.0

    def __init__(
        self, world, forecaster: ClaudeForecaster, max_calls: int = 12, tz: str = "America/Chicago", clock=time.time
    ):
        super().__init__(world, clock)
        self.forecaster = forecaster
        self.max_calls = max_calls
        self.tz = ZoneInfo(tz)

    def tick(self, now: float) -> None:
        when = datetime.fromtimestamp(now, tz=timezone.utc)
        day = when.astimezone(self.tz).date().isoformat()
        memory = self.memory
        if memory.get("day") != day:
            memory["day"], memory["calls"] = day, 0
        council = self.peer("investigadora").get("detail") or {}
        topics = council.get("topics") or []
        opinions = memory.setdefault("opinions", {})
        if not topics:
            self.status("Esperando a que la Investigadora traiga mercados", mood="sleep", opinions=opinions)
            self.doing("consejo", "espera los mercados")
            return
        if memory["calls"] >= self.max_calls:
            self.status(
                f"Ya he pensado {self.max_calls} mercados hoy: descanso hasta mañana",
                mood="sleep",
                opinions=opinions,
                calls=memory["calls"],
            )
            self.doing("consejo", "descansa: límite de hoy")
            return
        t = self._pick(topics, when)
        if t is None:
            self.status("Nada nuevo que pensar ahora", mood="ok", opinions=opinions, calls=memory["calls"])
            self.doing("consejo", "escucha al Consejo")
            return
        self.doing("consejo", f"piensa en «{t['name']}»")
        nube = {r["key"]: r for r in (self.peer("nube").get("detail") or {}).get("cities") or []}
        names = {m.id: m.name for m in MEMBERS}
        others = {names.get(k, k): v for k, v in (t.get("fairs") or {}).items() if k != self.id}
        memory["calls"] += 1
        memory.setdefault("asked", {})[t["ticker"]] = now
        result = self.forecaster.forecast(prompt_for(t, {"cities": nube}, others))
        if result is None:
            self.status("Ese mercado prefiero no opinarlo", mood="ok", opinions=opinions, calls=memory["calls"])
            return
        opinions[t["ticker"]] = {"fair": result["fair"], "reason": result["reason"], "at": iso(when), "name": t["name"]}
        for ticker in sorted(opinions, key=lambda k: opinions[k]["at"])[:-40]:
            del opinions[ticker]
        mid = t.get("mid")
        if t.get("held") or (mid is not None and abs(result["fair"] - mid) >= 0.08):
            self.say(
                f"«{t['name']}»: le doy un {pct(result['fair'])}. {result['reason']}", kind="info", ticker=t["ticker"]
            )
        self.status(
            f"«{t['name']}»: {pct(result['fair'])} · {plural(memory['calls'], 'pregunta', 'preguntas')} hoy",
            mood="happy",
            opinions=opinions,
            calls=memory["calls"],
            limit=self.max_calls,
        )
        self.doing("consejo", f"cree {pct(result['fair'])} · {t['name']}")

    def _pick(self, topics: list, when: datetime) -> Optional[dict]:
        asked = self.memory.setdefault("asked", {})
        now = when.timestamp()

        def fresh(t, hours):
            return now - asked.get(t["ticker"], 0) >= hours * 3600

        held = [t for t in topics if t.get("held") and fresh(t, 6)]
        if held:
            return held[0]

        def gap(t):
            values = list((t.get("fairs") or {}).values())
            return max(values) - min(values) if len(values) >= 2 else 0

        debated = sorted((t for t in topics if fresh(t, 4) and gap(t) >= 0.10), key=gap, reverse=True)
        if debated:
            return debated[0]
        rest = sorted((t for t in topics if fresh(t, 12)), key=lambda t: -(t.get("volume") or 0))
        return rest[0] if rest else None

    def talk(self) -> str:
        opinions = self.memory.get("opinions") or {}
        if not opinions:
            return "Todavía no he pensado ningún mercado. En cuanto la Investigadora traiga los de hoy, me pongo."
        ticker, o = max(opinions.items(), key=lambda kv: kv[1]["at"])
        return f"Lo último que he pensado: «{o.get('name', ticker)}», le doy un {pct(o['fair'])}. {o['reason']}"
