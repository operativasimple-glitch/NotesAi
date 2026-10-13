"""La Herrera, en la Forja: mira el dinero real de Kali y dice qué funciona y qué no.

Cada 15 minutos pide al panel (solo lectura) los mercados que el bot ha cerrado en los últimos
30 días y los agrupa:

  - por tipo (partidos o clima),
  - por liga y por ciudad,
  - por precio de compra (88–89¢, 90–91¢, 92–93¢, 94–97¢).

De cada grupo cuenta los aciertos, lo que ha ganado o perdido y el acierto que necesita para no
perder con el precio que pagó. Con eso te avisa de lo que está perdiendo dinero de verdad y de
qué ajuste del panel lo cambiaría. Ella no cambia nada: los ajustes los decides tú en el panel.
"""

from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from ..names import CITIES, market_name, money, plural
from ..sources.panel import PanelClient
from .base import Agent
from .kali import dec

DAYS = 30
MIN_SAMPLE = 6  # con menos mercados no se dice si un grupo gana o pierde
LEAGUES = {
    "KXNCAAFGAME": "Fútbol americano universitario",
    "KXNFLGAME": "NFL",
    "KXMLBGAME": "Béisbol (MLB)",
    "KXNHLGAME": "Hockey (NHL)",
    "KXNBAGAME": "Baloncesto (NBA)",
    "KXWNBAGAME": "WNBA",
    "KXMLSGAME": "Fútbol (MLS)",
}
BANDS = (
    (Decimal("0"), Decimal("0.90"), "menos de 90¢"),
    (Decimal("0.90"), Decimal("0.92"), "90–91¢"),
    (Decimal("0.92"), Decimal("0.94"), "92–93¢"),
    (Decimal("0.94"), Decimal("1"), "94¢ o más"),
)


def series_of(ticker: str) -> str:
    return ticker.split("-", 1)[0].upper()


def kind_of(series: str) -> str:
    if series.startswith("KXHIGH") or series.startswith("KXLOW"):
        return "clima"
    if series.endswith("GAME") or series.endswith("MATCH"):
        return "partidos"
    return "otros"


def label_of(series: str) -> str:
    if series in LEAGUES:
        return LEAGUES[series]
    if series.startswith("KXHIGH"):
        return f"Clima · {CITIES.get(series[6:], series[6:])}"
    return series


def wilson(wins: int, n: int, z: float = 1.96) -> tuple:
    """Margen del 95 % del acierto: con pocos mercados es ancho y no se da nada por seguro."""
    if not n:
        return 0.0, 1.0
    p = wins / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return centre - half, centre + half


def pct(value: float) -> str:
    return f"{value * 100:.1f}".replace(".", ",") + " %"


def stats(items: list) -> dict:
    """Aciertos, dinero y acierto necesario de un grupo de mercados cerrados.

    «Pierde» o «gana» solo cuando el margen de error del acierto queda entero por debajo o por
    encima del acierto que hace falta; si no, «va perdiendo» o «va ganando».
    """
    n = len(items)
    wins = sum(1 for m in items if m["net"] > 0)
    losses = sum(1 for m in items if m["net"] < 0)
    cost = sum((m["cost"] for m in items), Decimal(0))
    fees = sum((m["fees"] for m in items), Decimal(0))
    contracts = sum((m["contracts"] for m in items), Decimal(0))
    net = sum((m["net"] for m in items), Decimal(0))
    rate = Decimal(wins) / n if n else None
    need = (cost + fees) / contracts if contracts else None
    lo, hi = wilson(wins, n)
    if n < MIN_SAMPLE:
        verdict = "pocos datos"
    elif need is not None and hi < need:
        verdict = "pierde"
    elif need is not None and lo > need:
        verdict = "gana"
    elif net < 0:
        verdict = "va perdiendo"
    elif net > 0:
        verdict = "va ganando"
    else:
        verdict = "justo"
    return {
        "markets": n,
        "wins": wins,
        "losses": losses,
        "net": float(round(net, 2)),
        "cost": float(round(cost, 2)),
        "avg_price": float(round(cost / contracts, 4)) if contracts else None,
        "rate": float(round(rate, 4)) if rate is not None else None,
        "need": float(round(need, 4)) if need is not None else None,
        "verdict": verdict,
    }


class Herrera(Agent):
    id = "herrera"
    name = "Herrera"
    role = "Mira qué funciona y qué no en el dinero real de Kali"
    about = (
        "Repasa los mercados que Kali ha cerrado en los últimos 30 días y los separa por liga, por ciudad y "
        "por el precio que pagó. Te dice qué está perdiendo dinero de verdad y qué ajuste del panel lo "
        "cambiaría. No toca nada: los ajustes los decides tú."
    )
    home = "forja"
    color = "#ff8a3d"
    interval = 900.0

    def __init__(
        self,
        world,
        panel: Optional[PanelClient],
        tz_minutes: Callable[[], int] = lambda: 0,
        tz: str = "America/Chicago",
        clock=time.time,
    ):
        super().__init__(world, clock)
        self.panel = panel
        self.tz_minutes = tz_minutes
        self.tz = ZoneInfo(tz)

    # --- la vuelta ---------------------------------------------------------------------

    def tick(self, now: float) -> None:
        if self.panel is None:
            self.status("Necesito ver el panel de Kali (PANEL_URL y PANEL_PASSWORD)", mood="sleep", connected=False)
            self.doing("forja", "espera a ver el panel")
            return
        when = datetime.fromtimestamp(now, tz=timezone.utc)
        self.doing("forja", "repasa los resultados reales de Kali")
        report = self.panel.results(days=DAYS, tz_minutes=self.tz_minutes())
        markets = [m for m in (self._market(r) for r in report.get("recent") or []) if m]
        if not markets:
            self.status("Kali todavía no ha cerrado mercados en estos 30 días", connected=True, totals=None)
            self.doing("forja", "espera los primeros cierres")
            return
        groups = self._groups(markets)
        totals = stats(markets)
        advice = self._advice(groups, totals, markets)
        losses = sorted((m for m in markets if m["net"] < 0), key=lambda m: m["closed_at"], reverse=True)
        curve = [
            {"date": p.get("date"), "total": float(dec(p.get("total"), Decimal(0)))}
            for p in (report.get("curve") or [])
        ][-150:]
        self._speak(advice, groups, when)
        worst = min(groups["series"], key=lambda g: g["net"]) if groups["series"] else None
        net = money(Decimal(str(totals["net"])), sign=True)
        text = (
            f"30 días: {net} en {plural(totals['markets'], 'mercado', 'mercados')}"
            f" · {plural(totals['losses'], 'perdido', 'perdidos')}"
        )
        mood = "sad" if totals["net"] < 0 else "happy" if totals["verdict"] == "gana" else "ok"
        self.status(
            text,
            mood="alert" if any(a["level"] == "alert" for a in advice) else mood,
            connected=True,
            totals=totals,
            groups=groups,
            advice=advice,
            losses=[self._loss_row(m) for m in losses[:12]],
            curve=curve,
            days=DAYS,
            at=when.isoformat(),
        )
        if worst and worst["net"] < 0:
            self.doing("forja", f"lo que más pierde: {worst['label']} {money(Decimal(str(worst['net'])), sign=True)}")
        else:
            self.doing("forja", f"repasa {plural(totals['markets'], 'mercado', 'mercados')}: nada pierde")

    # --- las cuentas --------------------------------------------------------------------

    @staticmethod
    def _market(row: dict) -> Optional[dict]:
        ticker = row.get("ticker")
        contracts = dec(row.get("contracts"), Decimal(0))
        cost = dec(row.get("cost"), Decimal(0))
        if not ticker or contracts <= 0:
            return None
        series = series_of(ticker)
        title = row.get("title", "") if row.get("title") != ticker else ""
        return {
            "ticker": ticker,
            "series": series,
            "kind": kind_of(series),
            "name": market_name(ticker, title, row.get("subtitle", "")),
            "side": "SÍ" if row.get("side") == "yes" else "NO" if row.get("side") == "no" else "",
            "contracts": contracts,
            "cost": cost,
            "fees": dec(row.get("fees"), Decimal(0)),
            "net": dec(row.get("net"), Decimal(0)),
            "price": cost / contracts,
            "closed_at": row.get("closed_at") or "",
            "date": row.get("date") or "",
        }

    def _groups(self, markets: list) -> dict:
        kinds = []
        for key, label in (("partidos", "Partidos"), ("clima", "Clima"), ("otros", "Otros")):
            items = [m for m in markets if m["kind"] == key]
            if items:
                kinds.append({"key": key, "label": label, **stats(items)})
        series = []
        for code in sorted({m["series"] for m in markets}):
            items = [m for m in markets if m["series"] == code]
            series.append({"key": code, "label": label_of(code), "kind": kind_of(code), **stats(items)})
        series.sort(key=lambda g: g["net"])
        bands = []
        for lo, hi, label in BANDS:
            items = [m for m in markets if lo <= m["price"] < hi]
            if items:
                bands.append({"key": label, "label": label, **stats(items)})
        return {"kinds": kinds, "series": series, "bands": bands}

    def _advice(self, groups: dict, totals: dict, markets: list) -> list:
        """Lo que conviene cambiar en el panel, solo cuando los datos lo dicen claro."""
        out = []
        for g in groups["series"]:
            if g["verdict"] == "pierde" and g["markets"] >= MIN_SAMPLE:
                out.append(
                    {
                        "key": f"series:{g['key']}",
                        "level": "alert",
                        "text": (
                            f"«{g['label']}» pierde dinero: {plural(g['markets'], 'mercado', 'mercados')}, "
                            f"{plural(g['losses'], 'perdido', 'perdidos')}, "
                            f"{money(Decimal(str(g['net'])), sign=True)}. Puedes quitar {g['key']} en Ajustes → "
                            "Mercados → Series."
                        ),
                    }
                )
        low = [b for b in groups["bands"] if b["key"] in ("menos de 90¢", "90–91¢")]
        high = [b for b in groups["bands"] if b["key"] in ("92–93¢", "94¢ o más")]
        low_net = sum(b["net"] for b in low)
        low_n = sum(b["markets"] for b in low)
        low_losses = sum(b["losses"] for b in low)
        if low_n >= 8 and low_losses >= 3 and low_net < 0 and sum(b["net"] for b in high) > 0:
            out.append(
                {
                    "key": "price:92",
                    "level": "alert",
                    "text": (
                        "Lo que compra por debajo de 92¢ pierde: "
                        f"{money(Decimal(str(round(low_net, 2))), sign=True)} en "
                        f"{plural(low_n, 'mercado', 'mercados')}. Sube el precio mínimo del favorito a 0,92 en "
                        "Ajustes → Estrategia."
                    ),
                }
            )
        balance = (self.peer("kali").get("detail") or {}).get("balance") or {}
        equity = dec(balance.get("equity"))
        if equity and equity > 0 and markets:
            avg_cost = sum((m["cost"] for m in markets), Decimal(0)) / len(markets)
            share = avg_cost / equity
            if share >= Decimal("0.15"):
                avg_price = totals["avg_price"] or 0.93
                per_loss = int(round(avg_price / max(1 - avg_price, 0.01)))
                out.append(
                    {
                        "key": "size",
                        "level": "info",
                        "text": (
                            f"Cada apuesta es un {round(share * 100)} % de tu saldo y un fallo se come unos "
                            f"{per_loss} aciertos. Con menos contratos por orden (Ajustes → Estrategia) los días "
                            "malos duelen mucho menos."
                        ),
                    }
                )
        if totals["markets"] >= 30 and totals["rate"] is not None and totals["need"] is not None:
            if totals["rate"] < totals["need"]:
                out.append(
                    {
                        "key": "total",
                        "level": "alert",
                        "text": (
                            f"En total acierta un {pct(totals['rate'])} y necesita un {pct(totals['need'])} "
                            "con los precios que paga: ahora mismo no gana."
                        ),
                    }
                )
        return out

    def _speak(self, advice: list, groups: dict, when: datetime) -> None:
        """Cada consejo se dice una vez al día como mucho; lo que pierde, también a Kali."""
        day = when.astimezone(self.tz).date().isoformat()
        told = self.memory.setdefault("told", {})
        for a in advice:
            if told.get(a["key"]) == day:
                continue
            told[a["key"]] = day
            self.say(a["text"], to="tu", kind="alert" if a["level"] == "alert" else "info")
            if a["key"].startswith("series:"):
                self.say(f"Kali, {a['text'].split('. Puedes')[0]}.", to="kali", kind="info")
        for key in [k for k, d in told.items() if d < day and not any(a["key"] == k for a in advice)]:
            del told[key]

    @staticmethod
    def _loss_row(m: dict) -> dict:
        return {
            "ticker": m["ticker"],
            "name": m["name"],
            "label": label_of(m["series"]),
            "side": m["side"],
            "price": float(round(m["price"], 3)),
            "net": float(round(m["net"], 2)),
            "date": m["date"],
        }

    def talk(self, who: Optional[str] = None) -> str:
        detail = self.world.bot(self.id).get("detail") or {}
        totals = detail.get("totals")
        if not detail.get("connected"):
            return "No veo el panel de Kali. Cuando pongas PANEL_URL y PANEL_PASSWORD te cuento qué funciona y qué no."
        if not totals:
            return "Kali todavía no ha cerrado mercados en estos 30 días."
        series = (detail.get("groups") or {}).get("series") or []
        parts = [
            f"En 30 días Kali lleva {money(Decimal(str(totals['net'])), sign=True)} en "
            f"{plural(totals['markets'], 'mercado', 'mercados')} ({plural(totals['losses'], 'perdido', 'perdidos')})."
        ]
        if series:
            worst, best = series[0], series[-1]
            if worst["net"] < 0:
                parts.append(f"Lo que peor va: {worst['label']}, {money(Decimal(str(worst['net'])), sign=True)}.")
            if best["net"] > 0:
                parts.append(f"Lo que mejor: {best['label']}, {money(Decimal(str(best['net'])), sign=True)}.")
        advice = detail.get("advice") or []
        if advice:
            parts.append(advice[0]["text"])
        return " ".join(parts)
