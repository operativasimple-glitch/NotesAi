from datetime import datetime, timezone

from pueblo.agents.herrera import Herrera, stats
from pueblo.agents.kali import Kali
from pueblo.world import World

from .fakes import FakePanel

NOW = datetime(2026, 10, 11, 21, 0, tzinfo=timezone.utc).timestamp()


def closed(ticker, price, won, contracts=5, day="2026-10-10", side="yes", title="", subtitle=""):
    cost = round(price * contracts, 2)
    fees = 0.01
    net = round((contracts if won else 0) - cost - fees, 2)
    return {
        "ticker": ticker,
        "title": title or ticker,
        "subtitle": subtitle,
        "side": side,
        "contracts": str(contracts),
        "cost": str(cost),
        "fees": str(fees),
        "net": str(net),
        "closed_at": f"{day}T20:00:00+00:00",
        "date": day,
        "result": "yes" if won == (side == "yes") else "no",
    }


def rows():
    out = []
    # Universitario: 12 cerrados a 90¢, 5 perdidos → pierde.
    for i in range(12):
        out.append(closed(f"KXNCAAFGAME-26OCT10T{i}-A", 0.90, won=i >= 5, subtitle=f"Equipo {i}"))
    # Clima: 12 cerrados a 94¢, todos ganados → gana.
    for i in range(12):
        out.append(closed(f"KXHIGHNY-26OCT{i + 1:02d}-B72.5", 0.94, won=True, side="no", subtitle="72° to 73°"))
    # Béisbol: 3 cerrados → pocos datos.
    for i in range(3):
        out.append(closed(f"KXMLBGAME-26OCT09X{i}-LAD", 0.92, won=True, subtitle="Los Angeles D"))
    return out


def test_a_group_needs_to_win_more_often_than_it_pays():
    from decimal import Decimal as D

    def m(won):
        return {"net": D("0.39") if won else D("-4.61"), "cost": D("4.60"), "fees": D("0.01"), "contracts": D("5")}

    assert stats([m(True)] * 3)["verdict"] == "pocos datos"
    lucky = stats([m(True)] * 13 + [m(False)])
    assert lucky["verdict"] == "va ganando" and lucky["need"] == 0.922 and lucky["rate"] > lucky["need"]
    good = stats([m(True)] * 200 + [m(False)] * 2)  # con muchos mercados ya se puede decir
    assert good["verdict"] == "gana"
    # Un fallo entre siete deja el grupo en negativo, pero no basta para decir que pierde.
    unlucky = stats([m(True)] * 6 + [m(False)])
    assert unlucky["verdict"] == "va perdiendo" and unlucky["net"] < 0
    bad = stats([m(True)] * 10 + [m(False)] * 5)
    assert bad["verdict"] == "pierde"


def test_the_smith_finds_what_loses_real_money_and_tells_you():
    world = World(None)
    panel = FakePanel()
    panel.result_rows = rows()
    panel.curve_rows = [{"date": "2026-10-10", "total": "1.20"}, {"date": "2026-10-11", "total": "-3.10"}]
    kali = Kali(world, panel, clock=lambda: NOW)
    kali.tick(NOW)  # para que la Herrera sepa el saldo
    smith = Herrera(world, panel, clock=lambda: NOW)
    smith.tick(NOW)
    detail = world.bot("herrera")["detail"]
    series = {g["key"]: g for g in detail["groups"]["series"]}
    assert series["KXNCAAFGAME"]["verdict"] == "pierde" and series["KXNCAAFGAME"]["losses"] == 5
    assert series["KXHIGHNY"]["verdict"] == "va ganando" and series["KXHIGHNY"]["label"] == "Clima · Nueva York"
    assert series["KXMLBGAME"]["verdict"] == "pocos datos"
    assert detail["groups"]["series"][0]["key"] == "KXNCAAFGAME"  # lo que más pierde, primero
    bands = {b["key"]: b for b in detail["groups"]["bands"]}
    assert bands["90–91¢"]["markets"] == 12 and bands["94¢ o más"]["markets"] == 12
    keys = [a["key"] for a in detail["advice"]]
    assert "series:KXNCAAFGAME" in keys and "price:92" in keys and "size" in keys
    assert detail["curve"][-1]["total"] == -3.10
    assert detail["losses"][0]["label"] == "Fútbol americano universitario"
    to_you = [m["text"] for m in world.since() if m["from"] == "herrera" and m["to"] == "tu"]
    assert any("KXNCAAFGAME en Ajustes" in t for t in to_you)
    assert any("0,92" in t for t in to_you)
    to_kali = [m["text"] for m in world.since() if m["from"] == "herrera" and m["to"] == "kali"]
    assert to_kali and "Puedes" not in to_kali[0]
    assert world.bot("herrera")["activity"]["room"] == "forja"
    # La misma tarde no lo repite.
    before = len(world.since())
    smith.tick(NOW + 900)
    assert len([m for m in world.since()[before:] if m["from"] == "herrera"]) == 0
    assert "Fútbol americano universitario" in smith.talk()


def test_without_panel_it_waits():
    world = World(None)
    smith = Herrera(world, None, clock=lambda: NOW)
    smith.tick(NOW)
    assert world.bot("herrera")["detail"]["connected"] is False
    assert "PANEL_URL" in smith.talk()
