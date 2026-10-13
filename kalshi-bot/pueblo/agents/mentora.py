"""La Mentora, en la Incubadora: prueba de mentira otras maneras de ganar y dice si alguna vale.

Kali compra favoritos y espera. En la Incubadora, tres estrategias distintas (la Notaria, la
Tendera y la Contable: ver `incubator.strategies`) juegan cada una con 25 $ de mentira y las mismas
reglas de riesgo que el bot. Cada 5 minutos la Mentora:

  1. trae de Kalshi los mercados de temperatura de las 7 ciudades y los de los partidos, y de Nube
     lo que ya marcan los termómetros;
  2. cobra las posiciones de mentira cuyos mercados ya se han decidido;
  3. deja que cada estrategia haga lo suyo;
  4. lleva el marcador y el veredicto de cada una: ¿gana de verdad o es suerte? (`incubator.verdict`).

Cuando una pasa todas las pruebas, te lo dice. Ninguna opera con dinero real: para eso tendría que
ponerse a operar en paralelo con Kali, y eso solo si tú lo apruebas y le das un presupuesto.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from zoneinfo import ZoneInfo

from ..incubator.ledger import START, Ledger, dec, describe, new_account
from ..incubator.strategies import STRATEGIES, Data, short
from ..incubator.verdict import MAX_LUCK, MIN_DAYS, MIN_OPS, luck, summary
from ..names import cents, money, plural
from ..sources.kalshi_public import KalshiPublic
from ..sources.nws import CITIES
from .base import Agent
from .base import short as short_error

log = logging.getLogger(__name__)

GAME_SERIES = ("KXMLBGAME", "KXNFLGAME", "KXNHLGAME", "KXNBAGAME", "KXNCAAFGAME")
SUMMARY_HOUR = 21  # el resumen del día, a partir de las 9 de la noche (tu hora)
FILLS_EVERY = 1800.0  # la Tendera cuenta lo que le llenan como mucho cada media hora


class Mentora(Agent):
    id = "mentora"
    name = "Mentora"
    role = "Dirige la Incubadora: prueba otras estrategias de mentira"
    about = (
        "En la Incubadora, tres estrategias distintas a la de Kali juegan cada una con 25 $ de mentira y las mismas "
        "reglas de riesgo que el bot. La Mentora lleva sus cuentas y dice, con números, si alguna gana de verdad o "
        "solo ha tenido suerte. Ninguna toca dinero real: para operar en paralelo con Kali haría falta tu permiso "
        "y un presupuesto."
    )
    home = "incubadora"
    color = "#a3e635"
    interval = 300.0

    def __init__(
        self, world, kalshi: KalshiPublic, tz: str = "America/Chicago", clock=time.time, strategies=STRATEGIES
    ):
        self.strategies = tuple(strategies)
        self.voices = tuple(s.id for s in self.strategies)
        super().__init__(world, clock)
        self.kalshi = kalshi
        self.tz = ZoneInfo(tz)
        for s in self.strategies:
            world.register(s.id, name=s.name, role=s.role, about=s.about, home="incubadora", color=s.color)

    # --- la vuelta ---------------------------------------------------------------------

    def tick(self, now: float) -> None:
        when = datetime.fromtimestamp(now, tz=timezone.utc)
        today = when.astimezone(self.tz).date().isoformat()
        self.doing("incubadora", "trae los mercados a la Incubadora")
        data = self._data(when, today)
        accounts = self.memory.setdefault("accounts", {})
        notes = self.memory.setdefault("notes", {})
        ledgers = {}
        for s in self.strategies:
            if not accounts.get(s.id):
                accounts[s.id] = new_account(today)
            ledgers[s.id] = Ledger(accounts[s.id])
            ledgers[s.id].new_day(today)
        results = self._results(ledgers, when)
        turns = []
        for s in self.strategies:
            ledger = ledgers[s.id]
            closed = ledger.settle(results, when)  # primero se cobra: deja dinero libre para lo nuevo
            mem = notes.setdefault(s.id, {})
            try:
                events = s.step(ledger, data, mem)
                mem.pop("error", None)
            except Exception as exc:  # noqa: BLE001 - una estrategia que falla no para a las demás
                log.warning("La estrategia %s falló: %s", s.name, exc, exc_info=True)
                events = []
                mem["error"] = short_error(exc)
            turns.append((s, events, closed))
        rows = [self._row(s, accounts[s.id], today) for s in self.strategies]
        self._speak(turns, rows, when, today)
        self._publish(turns, rows, ledgers, when)

    # --- los datos ---------------------------------------------------------------------

    def _data(self, when: datetime, today: str) -> Data:
        weather, games, errors, tries = [], [], 0, 0
        for key in CITIES:
            tries += 1
            try:
                weather.extend(self.kalshi.series_markets(f"KXHIGH{key}"))
            except Exception:  # noqa: BLE001 - una serie que falla no para a las demás
                errors += 1
        for series in GAME_SERIES:
            tries += 1
            try:
                games.extend(self.kalshi.series_markets(series, max_pages=2))
            except Exception:  # noqa: BLE001
                errors += 1
        if errors == tries:
            raise RuntimeError("Kalshi no contesta")
        rows = (self.peer("nube").get("detail") or {}).get("cities") or []
        cities = {r["key"]: r for r in rows if r.get("key")}
        return Data(now=when, today=today, weather=weather, games=games, cities=cities, trades=self._trades)

    def _trades(self, ticker: str, min_ts: int, max_ts: int) -> list:
        return self.kalshi.trades(ticker, min_ts=min_ts, max_ts=max_ts, max_pages=2)

    def _results(self, ledgers: dict, when: datetime) -> dict:
        due = sorted({t for ledger in ledgers.values() for t in ledger.due(when)})
        results: dict = {}
        if not due:
            return results
        try:
            markets = self.kalshi.markets_by_ticker(due[:100])
        except Exception as exc:  # noqa: BLE001 - se cobrará en la próxima vuelta
            log.warning("La Mentora no pudo ver los resultados: %s", exc)
            return results
        for m in markets:
            if m.get("result") in ("yes", "no"):
                results[m["ticker"]] = m["result"]
        return results

    def _row(self, s, account: dict, today: str) -> dict:
        """Cómo va una estrategia (la «suerte» solo se recalcula cuando se decide algo nuevo)."""
        settled = account.get("settled") or []
        key = f"{(account.get('totals') or {}).get('ops')}:{settled[-1]['settled_at'] if settled else ''}"
        cache = self.memory.setdefault("luck", {})
        hit = cache.get(s.id)
        if not hit or hit.get("key") != key:
            hit = {"key": key, "value": luck(settled)}
            cache[s.id] = hit
        row = summary(account, today, hit["value"])
        return {"id": s.id, "name": s.name, "color": s.color, "rule": s.rule, **row}

    def _benchmarks(self) -> list:
        """Con qué comparar: lo que gana Kali de verdad y su estrategia jugada de mentira."""
        rows = []
        totals = (self.peer("herrera").get("detail") or {}).get("totals")
        if totals and totals.get("markets"):
            rows.append(
                {
                    "id": "kali",
                    "name": "Kali con dinero real (30 días)",
                    "ops": totals["markets"],
                    "wins": totals.get("wins", 0),
                    "pnl": totals.get("net", 0),
                }
            )
        variants = (self.peer("probadora").get("detail") or {}).get("variants") or []
        same = next((v for v in variants if v.get("id") == "tal_cual"), None)
        if same and same.get("trades"):
            rows.append(
                {
                    "id": "probadora",
                    "name": "Kali tal cual, de mentira (Pruebas)",
                    "ops": same["trades"],
                    "wins": same.get("wins", 0),
                    "pnl": same.get("pnl", 0),
                }
            )
        return rows

    # --- lo que se cuenta ---------------------------------------------------------------------

    def _speak(self, turns: list, rows: list, when: datetime, today: str) -> None:
        memory = self.memory
        said = memory.setdefault("said_at", {})
        for s, events, closed in turns:
            buys = [e for e in events if e["kind"] == "buy"]
            fills = [e for e in events if e["kind"] == "fill"]
            if buys:
                e = buys[0]
                if s.id == "contable":
                    text = (
                        f"¡Dinero gratis! En «{e['name']}» {e['why']}. "
                        f"Compro {plural(e['count'], 'juego', 'juegos')} de mentira."
                    )
                else:
                    paid = cents(Decimal(str(e["price"])))
                    text = f"Apunta: compro {e['count']} {e['side']} de «{e['name']}» a {paid}: {e['why']}. De mentira."
                self.world.say(s.id, text, to=self.id, kind="info", ticker=e["ticker"], paper=True)
                if len(buys) > 1:
                    self.world.say(
                        s.id, f"Y {plural(len(buys) - 1, 'compra más', 'compras más')} de mentira.", to=self.id
                    )
            if fills and when.timestamp() - float(said.get(s.id, 0)) >= FILLS_EVERY:
                said[s.id] = when.timestamp()
                parts = [
                    f"{e['count']} {e['side']} a {cents(Decimal(str(e['price'])))} en «{short(e['name'])}»"
                    for e in fills[:3]
                ]
                self.world.say(
                    s.id, "Me habrían llenado órdenes: " + "; ".join(parts) + ". De mentira.", to=self.id, paper=True
                )
            for op in sorted(closed, key=lambda o: -abs(o["pnl"]))[:1]:
                pnl = Decimal(str(op["pnl"]))
                if op["result"] == "anulada":
                    text = f"«{op['name']}» no se decidió: se anula y se devuelve lo pagado."
                elif pnl > 0:
                    text = f"Cobrado de mentira: «{op['name']}», {money(pnl, sign=True)}."
                else:
                    text = f"Perdido de mentira: «{op['name']}», {money(pnl, sign=True)}."
                self.world.say(s.id, text, kind="trade", ticker=op["key"], net=str(op["pnl"]), paper=True)
            if s.id == "notaria":
                for op in closed:
                    if op["result"] == "perdida":
                        self.say(
                            f"Ojo: la Notaria ha perdido «{op['name']}», que el termómetro daba por decidido "
                            f"({op.get('note') or 'sin nota'}). El informe oficial no coincidió con lo medido: "
                            "esto hay que mirarlo antes de fiarse de ella.",
                            kind="alert",
                        )
        # Cuando cambia el veredicto de una estrategia.
        states = memory.setdefault("states", {})
        for r in rows:
            before, states[r["id"]] = states.get(r["id"]), r["state"]
            if before is None or before == r["state"]:
                continue
            if r["state"] == "lista":
                self.say(f"«{r['name']}» ha pasado todas las pruebas de mentira. {r['text']}", to="tu", kind="alert")
            elif r["state"] == "descartada":
                self.say(f"«{r['name']}» queda descartada: {r['text']} Sigue probándose por si cambia.")
            elif r["state"] in ("dudosa", "arriesgada"):
                self.say(f"«{r['name']}»: {r['text']}")
        # El resumen del día.
        if when.astimezone(self.tz).hour >= SUMMARY_HOUR and memory.get("summary_day") != today:
            memory["summary_day"] = today
            parts = [
                f"{r['name']} {money(Decimal(str(r['pnl'])), sign=True)} en "
                f"{plural(r['ops'], 'operación', 'operaciones')}"
                for r in rows
            ]
            ready = [r["name"] for r in rows if r["state"] == "lista"]
            tail = (
                f" Lista para operar en paralelo: {', '.join(ready)} (solo si tú lo apruebas)."
                if ready
                else " Ninguna está lista todavía: hacen falta semanas de pruebas para no fiarse de la suerte."
            )
            self.say("Así va la Incubadora (de mentira): " + "; ".join(parts) + "." + tail)

    def _publish(self, turns: list, rows: list, ledgers: dict, when: datetime) -> None:
        by_id = {r["id"]: r for r in rows}
        notes = self.memory.get("notes") or {}
        for s, events, _closed in turns:
            r = by_id[s.id]
            ledger = ledgers[s.id]
            mem = notes.get(s.id) or {}
            positions = sorted(ledger.open.values(), key=lambda p: p.get("opened_at") or "", reverse=True)
            open_rows = [
                {
                    "key": p["key"],
                    "name": p["name"],
                    "held": describe(p),
                    "note": p.get("note", ""),
                    "opened_at": p.get("opened_at"),
                    "ends": p.get("ends"),
                    "realized": float(round(dec(p.get("realized")), 4)),
                    "fills": len(p.get("fills") or []),
                }
                for p in positions[:10]
            ]
            keep = ("key", "name", "result", "pnl", "held", "note", "settled_at", "fills")
            last = [{k: op.get(k) for k in keep} for op in reversed((ledger.a.get("settled") or [])[-8:])]
            pnl = Decimal(str(r["pnl"]))
            if mem.get("error"):
                status, mood = f"Tengo problemas: {mem['error']}", "sick"
            elif r["paused"]:
                status, mood = "En pausa hasta mañana: hoy ha perdido más del 25 %", "sleep"
            elif r["ops"]:
                status = f"{r['label']} · {money(pnl, sign=True)} en {plural(r['ops'], 'operación', 'operaciones')}"
                mood = "happy" if pnl > 0 and r["ops"] >= 3 else "sad" if pnl < 0 and r["ops"] >= 3 else "ok"
            else:
                bets = len(ledger.open)
                status = (
                    f"Aprendiendo · {plural(bets, 'apuesta abierta', 'apuestas abiertas')} de mentira"
                    if bets
                    else "Aprendiendo · todavía sin operaciones"
                )
                mood = "ok"
            detail = {
                "strategy": True,
                "summary": r,
                "rule": s.rule,
                "open": open_rows,
                "last": last,
                "research": s.research(mem),
            }
            if s.id == "tendera":
                detail["quotes"] = list((mem.get("quotes") or {}).values())[:6]
            self.world.update(s.id, status=status, mood=mood, detail=detail)
            self.doing("incubadora", s.doing(mem, events), bot_id=s.id)
        ready = [r for r in rows if r["state"] == "lista"]
        if ready:
            status = f"Incubadora · {plural(len(ready), 'estrategia lista', 'estrategias listas')} para operar"
        else:
            status = f"Incubadora · {plural(len(rows), 'estrategia', 'estrategias')} aprendiendo de mentira"
        self.status(
            status,
            mood="happy" if ready else "ok",
            board=rows,
            benchmarks=self._benchmarks(),
            criteria={"ops": MIN_OPS, "days": MIN_DAYS, "luck": MAX_LUCK},
            start=float(START),
            at=when.isoformat(timespec="seconds"),
        )
        played = [r for r in rows if r["ops"]]
        if played:
            best = max(played, key=lambda r: r["pnl"])
            lead = money(Decimal(str(best["pnl"])), sign=True)
            self.doing("incubadora", f"lleva el marcador · va mejor {best['name']} ({lead})")
        else:
            self.doing("incubadora", f"lleva el marcador de {plural(len(rows), 'estrategia', 'estrategias')}")

    # --- hablar --------------------------------------------------------------------------

    def talk(self, who: Optional[str] = None) -> str:
        if who and who != self.id:
            detail = self.world.bot(who).get("detail") or {}
            r = detail.get("summary") or {}
            parts = [r.get("text") or "Acabo de empezar: dame unas horas."]
            parts += (detail.get("research") or [])[:1]
            return " ".join(parts)
        rows = (self.world.bot(self.id).get("detail") or {}).get("board") or []
        if not rows:
            return "Acabo de abrir la Incubadora: en unos minutos empiezan las pruebas de mentira."
        parts = [f"{r['name']} {money(Decimal(str(r['pnl'])), sign=True)} ({r['label'].lower()})" for r in rows]
        return (
            "Así van las pruebas de mentira: " + "; ".join(parts) + ". Para que una opere de verdad tiene que ganar "
            "durante semanas, no por suerte, y tú tienes que aprobarlo y darle un presupuesto."
        )
