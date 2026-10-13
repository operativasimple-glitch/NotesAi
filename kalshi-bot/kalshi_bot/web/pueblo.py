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


class LocalPanel:
    """Lo mismo que lee el pueblo del panel por la red (PanelClient), pero del propio bot.

    Cada respuesta pasa por JSON igual que por la red, para que los vecinos reciban exactamente
    los mismos datos (los importes como texto, las fechas en ISO...).
    """

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
        if path == "/api/talk" and method == "POST":
            bot = str(body.get("bot") or "")
            if not BOT_ID.fullmatch(bot):
                raise ApiError(400, "Bot no válido")
            try:
                return self.town.talk(bot)
            except KeyError:
                raise ApiError(404, "Ese bot no vive aquí") from None
        raise ApiError(404, "No existe")
