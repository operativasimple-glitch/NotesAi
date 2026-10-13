"""El mundo del pueblo: quién vive en él, qué está haciendo cada uno y lo que se dicen.

Los bots no se llaman entre sí: se escriben mensajes (a otro bot, a "tu" o a "todos") y
cada uno lee los que le llegan. La web enseña los mensajes como bocadillos y en el diario.
Todo se guarda en disco para que un reinicio no borre la historia.
"""

from __future__ import annotations

import collections
import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger(__name__)

STATE_FILE = "pueblo.json"
MAX_MESSAGES = 400
KINDS = ("info", "alert", "trade", "chat", "diary")
EVERYONE, YOU = "todos", "tu"


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


class World:
    def __init__(self, data_dir: Optional[Path] = None, clock: Callable[[], float] = time.time):
        self.data_dir = Path(data_dir) if data_dir else None
        self.clock = clock
        self.lock = threading.RLock()
        self.bots: dict = {}  # id -> lo que se enseña de cada bot
        self.messages: collections.deque = collections.deque(maxlen=MAX_MESSAGES)
        self.memory: dict = {}  # id -> lo que cada bot recuerda entre vueltas (se guarda)
        self.next_id = 1
        self.version = 0  # sube cada vez que cambia algo de un bot (la web solo los pide si cambian)
        self._dirty = False
        self._load()

    # --- quién vive aquí ---------------------------------------------------------------

    def register(self, bot_id: str, **public) -> None:
        with self.lock:
            current = self.bots.get(bot_id, {})
            self.bots[bot_id] = {
                "id": bot_id,
                "status": "",
                "mood": "ok",
                "detail": {},
                "updated_at": None,
                **current,
                **public,
            }
            self.version += 1

    def update(self, bot_id: str, **fields) -> None:
        with self.lock:
            bot = self.bots.setdefault(bot_id, {"id": bot_id, "detail": {}})
            detail = fields.pop("detail", None)
            if detail is not None:
                bot["detail"] = {**bot.get("detail", {}), **detail}
            bot.update(fields)
            bot["updated_at"] = _iso(self.clock())
            self.version += 1

    def act(self, bot_id: str, room: str, text: str) -> None:
        """Qué está haciendo un bot ahora mismo y en qué sala de la base."""
        with self.lock:
            bot = self.bots.setdefault(bot_id, {"id": bot_id, "detail": {}})
            now = _iso(self.clock())
            if (bot.get("activity") or {}).get("text") != text or (bot.get("activity") or {}).get("room") != room:
                bot["activity"] = {"room": room, "text": text, "at": now}
                self.version += 1

    def bot(self, bot_id: str) -> dict:
        with self.lock:
            return json.loads(json.dumps(self.bots.get(bot_id, {}), default=str))

    def remember(self, bot_id: str) -> dict:
        """Memoria de un bot: un dict que puede cambiar y que se guarda en disco."""
        with self.lock:
            self._dirty = True
            return self.memory.setdefault(bot_id, {})

    def recall(self, bot_id: str) -> dict:
        """Una copia de lo que un bot recordaba (al arrancar)."""
        with self.lock:
            return json.loads(json.dumps(self.memory.get(bot_id) or {}, default=str))

    def publish(self, bot_id: str, memory: dict) -> None:
        """Guarda una copia de la memoria de un bot; se escribe en disco en la próxima vuelta."""
        copy = json.loads(json.dumps(memory, default=str))
        with self.lock:
            self.memory[bot_id] = copy
            self._dirty = True

    # --- lo que se dicen ----------------------------------------------------------------

    def say(self, sender: str, text: str, to: str = EVERYONE, kind: str = "info", **data) -> dict:
        if kind not in KINDS:
            kind = "info"
        with self.lock:
            message = {
                "id": self.next_id,
                "ts": _iso(self.clock()),
                "from": sender,
                "to": to,
                "text": text,
                "kind": kind,
            }
            if data:
                message["data"] = data
            self.next_id += 1
            self.messages.append(message)
            self._dirty = True
        log.info("[%s → %s] %s", sender, to, text)
        return message

    def since(self, after: int = 0, limit: int = 120) -> list:
        with self.lock:
            return [m for m in self.messages if m["id"] > after][-limit:]

    def inbox(self, bot_id: str, after: int = 0) -> list:
        """Mensajes para un bot (o para todos) que no ha leído todavía."""
        with self.lock:
            return [
                m for m in self.messages if m["id"] > after and m["to"] in (bot_id, EVERYONE) and m["from"] != bot_id
            ]

    def snapshot(self, after: int = 0, version: Optional[int] = None) -> dict:
        """Lo que ve la web. Si ya tiene la versión actual de los bots, no se le mandan otra vez."""
        with self.lock:
            snap = {
                "now": _iso(self.clock()),
                "messages": self.since(after),
                "last_id": self.next_id - 1,
                "version": self.version,
            }
            if version != self.version:
                snap["bots"] = [json.loads(json.dumps(b, default=str)) for b in self.bots.values()]
            return snap

    # --- memoria en disco --------------------------------------------------------------

    def _path(self) -> Optional[Path]:
        return self.data_dir / STATE_FILE if self.data_dir else None

    def _load(self) -> None:
        path = self._path()
        if path is None or not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("No se pudo leer %s (se empieza de cero): %s", path, exc)
            return
        self.messages.extend(m for m in data.get("messages", []) if isinstance(m, dict) and "id" in m)
        self.memory = data.get("memory") if isinstance(data.get("memory"), dict) else {}
        self.next_id = max([int(data.get("next_id") or 1)] + [m["id"] + 1 for m in self.messages])
        for bot_id, public in (data.get("bots") or {}).items():
            if isinstance(public, dict):
                self.bots[bot_id] = public

    def save(self, force: bool = False) -> None:
        path = self._path()
        if path is None:
            return
        with self.lock:
            if not (self._dirty or force):
                return
            data = {
                "next_id": self.next_id,
                "messages": list(self.messages),
                "memory": self.memory,
                "bots": self.bots,
            }
            text = json.dumps(data, ensure_ascii=False, default=str)
            self._dirty = False
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            log.warning("No se pudo guardar el pueblo: %s", exc)
