"""El pueblo de bots dentro del panel: misma web (en /pueblo/), misma contraseña y sin configurar nada.

Los vecinos del pueblo (paquete `pueblo`) leen el bot directamente, en el mismo proceso, en vez de
entrar por la red en su panel. Así no hace falta un segundo servicio en Railway ni copiar la
contraseña, y el pueblo ve lo mismo que el panel en cada momento.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

from pueblo.agents import build_agents
from pueblo.config import AI_MODEL, KALSHI_API, Config
from pueblo.runtime import Town
from pueblo.sources.panel import PanelError
from pueblo.web.server import static_file
from pueblo.world import World

from ..controller import BotController
from .server import ApiError, to_json

log = logging.getLogger(__name__)

BOT_ID = re.compile(r"[a-z]{2,20}")
# Lo único que el Puente puede cambiar en los ajustes, con sus márgenes (nunca lo que mande la web sin más).
CHANGES = {
    ("strategy", "min_price"): (Decimal("0.88"), Decimal("0.97")),
    ("strategy", "max_position"): (Decimal("1"), Decimal("50")),
    ("strategy", "order_size"): (Decimal("1"), Decimal("50")),
    ("risk", "max_market_pct"): (Decimal("0.01"), Decimal("0.5")),
    ("risk", "max_exposure_pct"): (Decimal("0.05"), Decimal("1")),
    ("risk", "max_daily_loss_pct"): (Decimal("0.05"), Decimal("0.5")),
}
WHOLE = {"max_position", "order_size"}


class LocalPanel:
    """Lo mismo que lee el pueblo del panel por la red (PanelClient), pero del propio bot.

    Cada respuesta pasa por JSON igual que por la red, para que los vecinos reciban exactamente
    los mismos datos (los importes como texto, las fechas en ISO...).
    """

    can_act = True  # dentro del bot, el Puente puede pararlo, venderlo todo o aplicar ajustes

    def __init__(self, controller: BotController):
        self.controller = controller

    def _call(self, fn, *args):
        try:
            data = fn(*args)
        except Exception as exc:  # noqa: BLE001 - para el pueblo, cualquier fallo es "el bot no contesta"
            raise PanelError(f"El bot no contesta: {exc}") from exc
        return json.loads(to_json(data))

    def status(self) -> dict:
        return self._call(self.controller.status)

    def positions(self) -> list:
        return self._call(self.controller.positions)

    def results(self, days: int = 2, tz_minutes: int = 0) -> dict:
        return self._call(self.controller.results, days, tz_minutes, "bot")

    def logs(self, after: int = 0) -> list:
        return self._call(self.controller.logs.since, after)

    def labels(self, tickers: list) -> dict:
        if not tickers:
            return {}
        return self._call(self.controller.labels, ",".join(tickers))

    def settings(self) -> dict:
        return self._call(self.controller.settings_payload)

    def trades(self, ticker: str) -> list:
        return (self._call(self.controller.market_trades, ticker) or {}).get("trades") or []


def embedded_config(data_dir: Path, tz: str, env=None) -> Config:
    """La configuración del pueblo cuando vive dentro del bot (lo opcional, por variables)."""
    env = os.environ if env is None else env
    try:
        ai_max_calls = max(0, int(env.get("PUEBLO_AI_MAX_CALLS", "12") or 0))
    except ValueError:
        ai_max_calls = 12
    return Config(
        password="",  # no se usa: entra quien tenga la sesión del panel
        panel_url="",
        panel_password="",
        data_dir=data_dir,
        host="",
        port=0,
        tz=tz,
        contact=env.get("PUEBLO_CONTACT", "pueblo-de-bots"),
        kalshi_api=env.get("KALSHI_PUBLIC_API", KALSHI_API).rstrip("/"),
        anthropic_api_key=env.get("ANTHROPIC_API_KEY", "").strip(),
        ai_model=env.get("PUEBLO_AI_MODEL", "").strip() or AI_MODEL,
        ai_max_calls=ai_max_calls,
    )


def close_summary(rows: list) -> str:
    """Cuánto se vendió de verdad: una venta inmediata se queda a medias si no hay compradores bastantes."""
    if not rows:
        return "No había apuestas abiertas."
    sold, partial, unsold, failed = [], [], [], []
    for r in rows:
        if not r.get("ok"):
            failed.append(r)
            continue
        filled, held = r.get("filled"), r.get("contracts")
        if filled is None or held is None or filled >= held:
            sold.append(r)
        elif filled > 0:
            partial.append(r)
        else:
            unsold.append(r)
    parts = [f"{len(sold)} de {len(rows)} apuestas vendidas"]
    if partial:
        parts.append(f"{len(partial)} vendidas a medias (el resto sigue abierto)")
    if unsold:
        parts.append(f"{len(unsold)} sin comprador en ese momento")
    if failed:
        parts.append(
            "no se pudieron vender: " + "; ".join(f"{r['ticker']} ({r.get('error') or 'error'})" for r in failed[:4])
        )
    text = ", ".join(parts) + "."
    if partial or unsold or failed:
        text += " Mira las que quedan en el Puente o en la app de Kalshi."
    return text


class PuebloMount:
    """El pueblo montado en el panel: sus vecinos (hilos) y las rutas /pueblo/..."""

    def __init__(self, controller: BotController, data_dir: Path, tz: str, env=None, agents=None, clock=time.time):
        self.controller = controller
        self.world = World(data_dir, clock=clock)
        config = embedded_config(data_dir, tz, env)
        if agents is None:
            agents = build_agents(self.world, config, panel=LocalPanel(controller))
        self.town = Town(self.world, agents, clock=clock)

    def start(self) -> None:
        self.town.start()
        log.info("Pueblo de bots abierto en /pueblo/ (%d vecinos)", len(self.town.agents))

    def stop(self) -> None:
        self.town.stop()

    # --- la web ----------------------------------------------------------------------

    @staticmethod
    def static(path: str, accept_gzip: bool) -> Optional[tuple]:
        """Un fichero de la web del pueblo; `path` sin el /pueblo del principio."""
        return static_file(path, accept_gzip)

    def dispatch(self, method: str, path: str, query: dict, body: dict):
        """Las rutas /pueblo/api/... (ya con la sesión del panel comprobada)."""
        if path == "/api/world" and method == "GET":
            try:
                after = int(query.get("after", "0") or 0)
            except ValueError:
                after = 0
            try:
                version = int(query["v"]) if query.get("v") else None
            except ValueError:
                version = None
            return self.world.snapshot(after, version)
        if path == "/api/act" and method == "POST":
            return self.act(body)
        if path == "/api/talk" and method == "POST":
            bot = str(body.get("bot") or "")
            if not BOT_ID.fullmatch(bot):
                raise ApiError(400, "Bot no válido")
            try:
                return self.town.talk(bot)
            except KeyError:
                raise ApiError(404, "Ese bot no vive aquí") from None
        raise ApiError(404, "No existe")

    # --- el Puente: lo que se hace de verdad sobre el bot ---------------------------------

    def act(self, body: dict) -> dict:
        """Parar, arrancar, freno, cerrar todas las apuestas o aplicar un ajuste propuesto.

        Lo que mueve dinero pide una confirmación escrita (como en el panel). Cada acción queda
        en el diario del pueblo, contada por la Vigía.
        """
        action = str(body.get("action") or "")
        controller = self.controller
        result = None
        if action == "parar":
            controller.stop()
            text = "Has parado el bot desde el Puente: ya no compra. Lo apostado sigue hasta el final."
            kind = "alert"
        elif action == "freno":
            result = controller.kill()
            cancelled = result.get("cancelled", 0)
            text, kind = f"Freno desde el Puente: bot parado y {cancelled} órdenes suyas canceladas.", "alert"
        elif action == "arrancar":
            mode = "sim" if body.get("mode") == "sim" else "live"
            if mode == "live" and controller.settings().is_production and body.get("confirm") != "REAL":
                raise ApiError(400, "Para arrancar con dinero real escribe REAL")
            controller.start(mode)
            how = "con dinero real" if mode == "live" else "en simulación"
            text, kind = f"Has arrancado el bot desde el Puente ({how}).", "info"
        elif action == "cerrar_todo":
            if body.get("confirm") != "CERRAR":
                raise ApiError(400, "Para vender todas las apuestas escribe CERRAR")
            result = controller.close_all_positions()
            text = "Cerrar todo desde el Puente: bot parado. " + close_summary(result.get("closed") or [])
            kind = "alert"
        elif action == "aplicar":
            if body.get("confirm") is not True:
                raise ApiError(400, "Confirma el cambio")
            section, key, value = self._change(body.get("change"))
            settings = controller.settings()
            if section == "strategy":
                params = {**settings.strategy_params, key: value}
                controller.save_settings({"strategy": {"name": settings.strategy_name, "params": params}})
            else:
                controller.save_settings({section: {key: value}})
            restarted = controller.is_running()
            if restarted:
                controller.restart()  # para que lo use ya
            text = f"Ajuste aplicado desde el Puente: {key} = {value}"
            text += " (el bot se ha reiniciado para usarlo ya)." if restarted else "."
            kind, result = "info", {"section": section, "key": key, "value": value, "restarted": restarted}
        else:
            raise ApiError(400, "Acción no válida")
        log.warning("PUEBLO: %s", text)
        self.world.say("vigia", text, to="tu", kind=kind)
        return {"ok": True, "text": text, "result": result, "status": json.loads(to_json(controller.status()))}

    @staticmethod
    def _change(change) -> tuple:
        if not isinstance(change, dict):
            raise ApiError(400, "Cambio no válido")
        section, key = str(change.get("section") or ""), str(change.get("key") or "")
        bounds = CHANGES.get((section, key))
        if bounds is None:
            raise ApiError(400, "Ese ajuste no se puede cambiar desde el pueblo")
        try:
            value = Decimal(str(change.get("value")))
        except (InvalidOperation, ValueError):
            raise ApiError(400, "Valor no válido") from None
        if not (bounds[0] <= value <= bounds[1]) or (key in WHOLE and value != value.to_integral_value()):
            raise ApiError(400, f"{key} debe estar entre {bounds[0]} y {bounds[1]}")
        return section, key, str(int(value)) if key in WHOLE else str(value)
