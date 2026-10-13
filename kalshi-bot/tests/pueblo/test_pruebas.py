from datetime import datetime, timedelta, timezone
from decimal import Decimal

from pueblo.agents.probadora import Probadora, maker_fee
from pueblo.world import World

from .fakes import FakeKalshiPublic

NOW = datetime(2026, 10, 10, 18, 0, tzinfo=timezone.utc)


def game(ticker, bid, ask, ends_in_hours, **extra):
    end = NOW + timedelta(hours=ends_in_hours)
    return {
        "ticker": ticker,
        "event_ticker": ticker.rsplit("-", 1)[0],
        "title": f"{ticker} Winner?",
        "yes_sub_title": ticker.rsplit("-", 1)[1],
        "yes_bid_dollars": bid,
        "yes_ask_dollars": ask,
        "close_time": (end + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expected_expiration_time": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": "active",
        **extra,
    }


def board(world):
    return {r["id"]: r for r in world.bot("probadora")["detail"]["variants"]}


def test_the_tester_plays_each_option_on_paper_and_scores_it():
    world = World(None)
    kalshi = FakeKalshiPublic()
    games = [
        game("KXNCAAFGAME-26OCT10AB-A", "0.90", "0.92", 2),  # favorito a 90¢: solo desde 92¢ no entra
        game("KXNCAAFGAME-26OCT10CD-C", "0.93", "0.95", 2),
        game("KXNCAAFGAME-26OCT10EF-E", "0.95", "0.96", 2.5),
        game("KXNCAAFGAME-26OCT10GH-G", "0.04", "0.06", 3),  # el favorito es el NO, a 94¢
        game("KXNCAAFGAME-26OCT10IJ-I", "0.89", "0.91", 3.5),
        game("KXNCAAFGAME-26OCT10KL-K", "0.93", "0.95", 0.1),  # acaba en 6 min: tarde
        game("KXNCAAFGAME-26OCT10MN-M", "0.85", "0.95", 2),  # spread enorme
        game("KXNCAAFGAME-26OCT10OP-O", "0.93", "0.95", 9),  # acaba dentro de 9 h: pronto
    ]
    kalshi.series["KXNCAAFGAME"] = games
    tester = Probadora(world, kalshi, clock=lambda: NOW.timestamp())
    tester.tick(NOW.timestamp())
    books = tester.memory["books"]
    assert len(books["tal_cual"]) == 5
    assert len(books["max3"]) == 3  # los tres que acaban antes
    assert {t["count"] for t in books["menos"].values()} == {3}
    assert sorted(books["desde92"]) == [
        "KXNCAAFGAME-26OCT10CD-C",
        "KXNCAAFGAME-26OCT10EF-E",
        "KXNCAAFGAME-26OCT10GH-G",
    ]
    assert books["tal_cual"]["KXNCAAFGAME-26OCT10GH-G"]["side"] == "NO"

    # Tres horas después: A da la sorpresa y pierde; los demás ganan.
    for g in games:
        kalshi.by_ticker[g["ticker"]] = {**g, "status": "finalized", "result": "yes"}
    kalshi.by_ticker["KXNCAAFGAME-26OCT10AB-A"]["result"] = "no"
    kalshi.by_ticker["KXNCAAFGAME-26OCT10GH-G"]["result"] = "no"
    later = NOW + timedelta(hours=4)
    kalshi.series["KXNCAAFGAME"] = []
    tester.tick(later.timestamp())
    rows = board(world)
    loss = -Decimal("0.90") * 5 - maker_fee(5, Decimal("0.90"))
    assert rows["tal_cual"]["trades"] == 5 and rows["tal_cual"]["wins"] == 4
    assert rows["desde92"]["trades"] == 3 and rows["desde92"]["wins"] == 3
    assert rows["desde92"]["pnl"] > 0 > rows["tal_cual"]["pnl"]
    assert rows["tal_cual"]["worst_day"] == rows["tal_cual"]["pnl"]
    assert float(loss) < rows["tal_cual"]["pnl"] < 0  # una pérdida se come los cuatro aciertos
    said = [m["text"] for m in world.since()]
    assert any("Partido perdido de mentira" in t and "Solo desde 92¢ no habría entrado" in t for t in said)
    assert any(t.startswith("Se han decidido 5 partidos") for t in said)
    assert "Kali tal cual" in tester.talk()
    assert world.bot("probadora")["activity"]["room"] == "pruebas"


def test_with_the_limit_it_opens_new_games_when_others_finish():
    world = World(None)
    kalshi = FakeKalshiPublic()
    first = [game(f"KXNFLGAME-26OCT10X{i}-A", "0.93", "0.95", h) for i, h in enumerate((1, 1.2, 1.4, 4))]
    kalshi.series["KXNFLGAME"] = first
    tester = Probadora(world, kalshi, clock=lambda: NOW.timestamp())
    tester.tick(NOW.timestamp())
    assert len(tester.memory["books"]["max3"]) == 3
    for g in first[:3]:
        kalshi.by_ticker[g["ticker"]] = {**g, "status": "finalized", "result": "yes"}
    later = NOW + timedelta(hours=1, minutes=50)
    kalshi.series["KXNFLGAME"] = [first[3]]
    tester.tick(later.timestamp())
    assert list(tester.memory["books"]["max3"]) == [first[3]["ticker"]]
    assert board(world)["max3"]["wins"] == 3
