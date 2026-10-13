"""Lo común a todos los bots del pueblo."""

from __future__ import annotations

import logging
import time
from typing import Optional

from ..world import EVERYONE, World

log = logging.getLogger(__name__)

# Las salas de la base. Cada bot va a la sala de lo que está haciendo de verdad.
ROOMS = ("mercado", "laboratorio", "observatorio", "consejo", "boveda", "puente", "archivo", "pruebas", "forja")


class Agent:
    """Un vecino del pueblo. Cada `interval` segundos hace su trabajo (`tick`).

    Para hablar usa `say` (a otro bot, a "tu" o a "todos"); para contar qué está haciendo,
    `status`. Lo que tenga que recordar entre vueltas va en `self.memory`: es solo suya (trabaja
    en su propio hilo) y al acabar cada vuelta deja una copia en el mundo, que es la que se guarda.
    """

    id = ""
    name = ""
    role = ""  # una línea: qué hace
    about = ""  # unas frases para su ficha
    home = ""  # su sala en la base (donde está cuando no hace nada)
    color = ""  # color del personaje
    interval = 60.0
    voices: tuple = ()  # otros vecinos que habla este (los miembros del Consejo)

    def __init__(self, world: World, clock=time.time):
        self.world = world
        self.clock = clock
        self.next_run = 0.0
        self.failures = 0
        self._memory = world.recall(self.id)
        world.register(self.id, name=self.name, role=self.role, about=self.about, home=self.home, color=self.color)

    @property
    def memory(self) -> dict:
        return self._memory

    def say(self, text: str, to: str = EVERYONE, kind: str = "info", **data) -> dict:
        return self.world.say(self.id, text, to=to, kind=kind, **data)

    def status(self, text: str, mood: str = "ok", **detail) -> None:
        self.world.update(self.id, status=text, mood=mood, detail=detail)

    def doing(self, room: str, text: str, bot_id: Optional[str] = None) -> None:
        """Cuenta qué está haciendo ahora y en qué sala: la web lo lleva hasta allí."""
        self.world.act(bot_id or self.id, room if room in ROOMS else self.home, text)

    def inbox(self) -> list:
        """Mensajes nuevos para este bot (cada uno se lee una vez)."""
        after = int(self.memory.get("read_until", 0))
        messages = self.world.inbox(self.id, after)
        if messages:
            self.memory["read_until"] = messages[-1]["id"]
        return messages

    def peer(self, bot_id: str) -> dict:
        """Lo que otro bot enseña de sí mismo (su estado y sus datos)."""
        return self.world.bot(bot_id)

    # --- lo que cambia en cada bot ----------------------------------------------------

    def tick(self, now: float) -> None:  # pragma: no cover - cada bot tiene el suyo
        raise NotImplementedError

    def talk(self, who: Optional[str] = None) -> str:
        """Lo que contesta cuando le hablas desde la web (`who`: a quién de sus voces)."""
        bot = self.world.bot(who or self.id)
        return bot.get("status") or "Aquí estoy, trabajando."

    # --- la vuelta ---------------------------------------------------------------------

    def run_if_due(self, now: float) -> bool:
        if now < self.next_run:
            return False
        try:
            self.tick(now)
            self.failures = 0
            self.next_run = now + self.interval
        except Exception as exc:  # noqa: BLE001 - un bot que falla no tumba al pueblo
            self.failures += 1
            log.warning("%s falló (%d seguidas): %s", self.name, self.failures, exc, exc_info=self.failures == 1)
            if self.failures == 1:
                self.status(f"Tengo problemas: {short(exc)}", mood="sick")
            # Cada vez espera más (hasta 30 min) para no machacar a nadie.
            self.next_run = now + min(self.interval * 2 ** min(self.failures, 5), 1800.0)
        finally:
            self.world.publish(self.id, self._memory)
        return True


def short(exc: Exception, limit: int = 140) -> str:
    text = str(exc) or exc.__class__.__name__
    return text if len(text) <= limit else text[: limit - 1] + "…"


def first(items, default: Optional[dict] = None):
    return next(iter(items), default)
