"""El reloj del pueblo: despierta a cada bot cuando le toca y guarda la memoria.

Cada vecino trabaja en su propio hilo: si uno se queda esperando a la red (el servicio del
tiempo o Kalshi tardan en contestar), los demás siguen a lo suyo.
"""

from __future__ import annotations

import logging
import threading
import time

from .world import World

log = logging.getLogger(__name__)


class Town:
    def __init__(self, world: World, agents: list, clock=time.time, save_every: float = 20.0):
        self.world = world
        self.agents = {a.id: a for a in agents}
        self.clock = clock
        self.save_every = save_every
        self._stop = threading.Event()
        self._threads: list = []
        self._saver = None
        self._last_save = 0.0

    def step(self) -> None:
        """Una vuelta para todos, uno detrás de otro (para las pruebas)."""
        now = self.clock()
        for agent in self.agents.values():
            if self._stop.is_set():
                return
            agent.run_if_due(now)
        if now - self._last_save >= self.save_every:
            self.world.save()
            self._last_save = now

    def talk(self, bot_id: str) -> dict:
        agent = self.agents.get(bot_id) or next((a for a in self.agents.values() if bot_id in a.voices), None)
        if agent is None:
            raise KeyError(bot_id)
        try:
            text = agent.talk(bot_id) if bot_id != agent.id else agent.talk()
        except Exception as exc:  # noqa: BLE001
            log.warning("%s no pudo contestar: %s", agent.name, exc)
            text = "Ahora mismo no puedo hablar, estoy liado."
        return self.world.say(bot_id, text, to="tu", kind="chat")

    def start(self) -> None:
        for agent in self.agents.values():
            thread = threading.Thread(target=self._work, args=(agent,), name=f"pueblo-{agent.id}", daemon=True)
            thread.start()
            self._threads.append(thread)
        self._saver = threading.Thread(target=self._keep, name="pueblo-memoria", daemon=True)
        self._saver.start()

    def _work(self, agent) -> None:
        while not self._stop.is_set():
            try:
                agent.run_if_due(self.clock())
            except Exception:  # noqa: BLE001 - run_if_due ya se cuida; esto es por si acaso
                log.exception("Error en la vuelta de %s", agent.name)
            self._stop.wait(max(0.25, min(5.0, agent.next_run - self.clock())))

    def _keep(self) -> None:
        while not self._stop.wait(self.save_every):
            self.world.save()
        self.world.save(force=True)

    def stop(self, timeout: float = 10.0) -> None:
        # Los hilos de los bots pueden estar esperando a la red: no se les espera, pero la
        # memoria se guarda antes de salir.
        self._stop.set()
        if self._saver is not None:
            self._saver.join(timeout)
