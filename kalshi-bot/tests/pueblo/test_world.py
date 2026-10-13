import time

from pueblo.agents.base import Agent
from pueblo.runtime import Town
from pueblo.world import World

from .fakes import Clock


def test_messages_inbox_and_memory_survive_a_restart(tmp_path):
    clock = Clock()
    world = World(tmp_path, clock)
    world.register("kali", name="Kali", home="mercado")
    world.update("kali", status="Trabajando", detail={"a": 1})
    world.update("kali", detail={"b": 2})
    assert world.bot("kali")["detail"] == {"a": 1, "b": 2}
    first = world.say("nube", "Hola a todos")
    to_vigia = world.say("nube", "Ojo con NY", to="vigia", kind="alert")
    world.say("vigia", "Para ti", to="tu")
    assert [m["text"] for m in world.inbox("vigia")] == ["Hola a todos", "Ojo con NY"]
    assert [m["id"] for m in world.inbox("vigia", after=first["id"])] == [to_vigia["id"]]
    assert world.inbox("nube") == []  # lo suyo no se lo lee a sí mismo
    world.remember("kali")["seen"] = [1, 2]
    world.save()

    again = World(tmp_path, clock)
    assert [m["text"] for m in again.since()] == ["Hola a todos", "Ojo con NY", "Para ti"]
    assert again.memory["kali"]["seen"] == [1, 2] and again.bot("kali")["status"] == "Trabajando"
    assert again.say("kali", "Sigo aquí")["id"] == 4  # los ids no se repiten tras reiniciar
    snap = again.snapshot(after=2)
    assert [m["id"] for m in snap["messages"]] == [3, 4] and snap["last_id"] == 4


def test_a_broken_state_file_starts_fresh(tmp_path):
    (tmp_path / "pueblo.json").write_text("{roto", encoding="utf-8")
    assert World(tmp_path).since() == []


class Counter(Agent):
    id = "contador"
    name = "Contador"
    interval = 0.05

    def tick(self, now):
        self.memory["vueltas"] = self.memory.get("vueltas", 0) + 1
        self.status(f"Vuelta {self.memory['vueltas']}")


def test_each_bot_keeps_its_memory_and_works_in_its_own_thread(tmp_path):
    world = World(tmp_path)
    town = Town(world, [Counter(world)], save_every=0.05)
    town.start()
    time.sleep(0.9)
    town.stop()
    turns = World(tmp_path).memory["contador"]["vueltas"]
    assert turns >= 2
    again = World(tmp_path)
    counter = Counter(again)  # al volver, sigue contando por donde iba
    counter.run_if_due(time.time())
    assert again.memory["contador"]["vueltas"] == turns + 1
