from datetime import datetime, timedelta, timezone

from pueblo.agents.herrera import Herrera
from pueblo.autopsy import autopsy
from pueblo.world import World

from .fakes import FakeKalshiPublic, FakePanel

LT = "KXNCAAFGAME-26OCT10LTFIU-LT"
END = datetime(2026, 10, 11, 2, 48, tzinfo=timezone.utc)  # el partido acaba a las 21:48 de Missouri


def at(minutes_before_end: float) -> str:
    return (END - timedelta(minutes=minutes_before_end)).isoformat()


def public(minutes_before_end: float, yes: str) -> dict:
    return {"created_time": at(minutes_before_end), "yes_price_dollars": yes, "count_fp": "5.00"}


def bought(minutes_before_end: float, side: str, price: str, count: str = "5") -> dict:
    leg = {"action": "buy", "outcome": side, "count": count, "price": price, "owner": "bot"}
    return {"time": at(minutes_before_end), "legs": [leg], "bot": True, "reason": "favorito NO"}


LOSS = {"ticker": LT, "name": "Gana Louisiana Tech", "side": "NO", "net": "-4.41", "closed_at": END.isoformat()}
MARKET = {"ticker": LT, "close_time": END.isoformat(), "result": "yes"}


def test_autopsy_tells_a_late_comeback():
    # Compró NO a 88¢ 1 h y 10 min antes del final; llegó a valer 97¢ y a 1 h del final valía 80¢.
    trades = [bought(70, "no", "0.88")]
    prices = [public(65, "0.03"), public(62, "0.20"), public(30, "0.60"), public(5, "0.95")]
    result = autopsy(LOSS, trades, MARKET, prices)
    assert result["side"] == "NO" and result["price"] == 0.88 and result["hours_before"] == 1.17
    assert result["top"] == 0.97 and result["hour_before"] == 0.8
    assert result["text"] == (
        "«Gana Louisiana Tech» (NO a 88¢, 1,2 h antes del final): una hora antes del final aún valía 80¢: "
        "se dio la vuelta al final. A 88¢, el mercado le daba un 12 % de fallar (1 de cada 8)."
    )


def test_autopsy_of_a_slow_slide_and_of_a_last_hour_buy():
    trades = [bought(200, "no", "0.90")]
    slide = autopsy(LOSS, trades, MARKET, [public(150, "0.30"), public(90, "0.70"), public(20, "0.90")])
    assert "se fue torciendo: una hora antes del final ya valía 30¢" in slide["text"]
    late = autopsy(LOSS, [bought(30, "no", "0.88")], MARKET, [public(20, "0.02"), public(2, "0.99")])
    assert "la compró en la última hora; llegó a valer 98¢ y se dio la vuelta al final" in late["text"]
    # Sin compra suya en ese lado no hay autopsia.
    assert autopsy(LOSS, [bought(30, "yes", "0.12")], MARKET, []) is None


def test_herrera_writes_the_autopsy_of_each_recent_loss_once():
    world = World(None)
    panel = FakePanel()
    panel.result_rows = [
        {
            "ticker": LT,
            "title": "Louisiana Tech at FIU Winner?",
            "subtitle": "Louisiana Tech",
            "side": "no",
            "contracts": "5",
            "cost": "4.40",
            "fees": "0.01",
            "net": "-4.41",
            "closed_at": END.isoformat(),
            "date": "2026-10-10",
            "result": "yes",
        }
    ]
    panel.trade_rows = {LT: [bought(70, "no", "0.88")]}
    kalshi = FakeKalshiPublic()
    kalshi.by_ticker[LT] = MARKET
    kalshi.public_trades = {LT: [public(65, "0.03"), public(62, "0.20"), public(5, "0.95")]}
    now = (END + timedelta(hours=10)).timestamp()
    herrera = Herrera(world, panel, kalshi=kalshi, clock=lambda: now)
    herrera.run_if_due(now)
    detail = world.bot("herrera")["detail"]
    (found,) = detail["autopsies"]
    assert found["ticker"] == LT and "se dio la vuelta al final" in found["text"]
    told = [m["text"] for m in world.since() if m["from"] == "herrera" and m["text"].startswith("Autopsia")]
    assert len(told) == 1
    herrera.next_run = 0
    herrera.run_if_due(now + 900)
    told = [m["text"] for m in world.since() if m["from"] == "herrera" and m["text"].startswith("Autopsia")]
    assert len(told) == 1  # no la repite
    assert sum(1 for a in kalshi.asked if a == ("trades", LT)) == 1
