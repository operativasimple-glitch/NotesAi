"""La Incubadora: estrategias distintas a la de Kali, con 25 $ de mentira y el veredicto de la Mentora."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from pueblo.agents.mentora import Mentora
from pueblo.incubator import strategies as st
from pueblo.incubator.ledger import MAKER, TAKER, Ledger, fee, new_account
from pueblo.incubator.verdict import luck, summary
from pueblo.runtime import Town
from pueblo.world import World

from .fakes import FakeKalshiPublic

# 13 oct 2026, 16:00 en Chicago (21:00 UTC): la máxima de hoy ya está casi hecha.
NOW = datetime(2026, 10, 13, 21, 0, tzinfo=timezone.utc)
TODAY_CLOSE = "2026-10-14T05:59:00Z"
TOMORROW_CLOSE = "2026-10-15T05:59:00Z"


def wx(ticker, sub, bid, ask, close=TODAY_CLOSE, volume="300"):
    return {
        "ticker": ticker,
        "event_ticker": ticker.rsplit("-", 1)[0],
        "title": "Highest temperature in Chicago?",
        "yes_sub_title": sub,
        "yes_bid_dollars": bid,
        "yes_ask_dollars": ask,
        "close_time": close,
        "status": "active",
        "volume_24h_fp": volume,
    }


def chicago_today():
    return [
        wx("KXHIGHCHI-26OCT13-T77", "77° or below", "0.01", "0.02"),
        wx("KXHIGHCHI-26OCT13-B78.5", "78° to 79°", "0.03", "0.05"),
        wx("KXHIGHCHI-26OCT13-B80.5", "80° to 81°", "0.55", "0.60"),
        wx("KXHIGHCHI-26OCT13-T82", "82° or above", "0.35", "0.40"),
    ]


def cities(at="2026-10-13T20:51:00+00:00"):
    return {
        "CHI": {"key": "CHI", "city": "Chicago", "max_so_far": 82, "confirmed": 81, "at": at},
        "NY": {"key": "NY", "city": "Nueva York", "max_so_far": 76, "confirmed": 76, "at": at},
    }


def texts(world, **filters):
    return [m["text"] for m in world.since() if all(m.get(k) == v for k, v in filters.items())]


# --- la cuenta de mentira --------------------------------------------------------------------


def test_the_paper_account_pays_kalshi_fees_and_keeps_the_bot_risk_rules():
    assert fee(3, D("0.97"), TAKER) == D("0.01") and fee(1, D("0.30"), TAKER) == D("0.02")
    assert fee(2, D("0.41"), MAKER) == D("0.01") and fee(0, D("0.5"), TAKER) == 0
    ledger = Ledger(new_account("2026-10-13"))
    assert ledger.equity() == D("25")
    # Como mucho un 15 % del saldo en un mercado: 3,75 $ → 3 contratos a 98¢ con la comisión.
    assert ledger.affordable("A", D("0.98"), 10) == 3
    ledger.buy("A", "A", "no", 3, D("0.97"), D("0.01"), name="A", ends=TODAY_CLOSE, when=NOW)
    assert ledger.cash == D("22.08") and ledger.exposure() == D("2.91")
    assert ledger.affordable("A", D("0.98"), 10) == 0  # ese mercado ya está lleno
    # Y como mucho un 60 % del saldo en juego: 15 $ entre todos.
    for key in "BCDE":
        n = ledger.affordable(key, D("0.98"), 10)
        ledger.buy(key, key, "yes", n, D("0.97"), D("0.01"), name=key, ends=TODAY_CLOSE, when=NOW)
    assert ledger.exposure() <= D("15") and ledger.affordable("F", D("0.98"), 10) == 0
    # Lo decidido se cobra; lo que una semana después no tiene resultado se anula.
    closed = ledger.settle({"A": "no", "B": "yes", "C": "no"}, NOW + timedelta(hours=12))
    assert [op["result"] for op in closed] == ["ganada", "ganada", "perdida"]
    assert closed[0]["pnl"] == pytest.approx(3 - 2.91 - 0.01) and closed[0]["bets"] == [[3, 0.97]]
    assert ledger.settle({}, NOW + timedelta(days=9))[0]["result"] == "anulada"
    totals = ledger.a["totals"]
    assert totals["ops"] == 5 and totals["wins"] == 2 and totals["losses"] >= 1


def test_yes_and_no_in_the_same_market_cancel_out_like_in_kalshi():
    ledger = Ledger(new_account("2026-10-13"))
    ledger.buy("M", "M", "yes", 2, D("0.41"), D("0.01"), name="M", ends=TOMORROW_CLOSE, when=NOW)
    ledger.buy("M", "M", "no", 3, D("0.55"), D("0.01"), name="M", ends=TOMORROW_CLOSE, when=NOW)
    pos = ledger.position("M")
    leg = pos["legs"]["M"]
    assert (leg["yes"], leg["no"]) == (0, 1) and D(pos["realized"]) == D("0.08")  # 2 × (1 − 0,41 − 0,55)
    assert ledger.net("M", "M") == -1
    # 25 − 0,83 − 1,66 + 2 (las dos parejas se cobran ya)
    assert ledger.cash == D("24.51")
    op = ledger.settle({"M": "yes"}, NOW + timedelta(days=2))[0]
    assert op["pnl"] == pytest.approx(0.08 - 0.55 - 0.02) and op["bets"] == [[1, 0.55]]


def test_a_bad_day_pauses_buying_until_tomorrow():
    ledger = Ledger(new_account("2026-10-13"))
    for key in "ABCDE":
        ledger.buy(key, key, "yes", 3, D("0.95"), D("0.01"), name=key, ends=TODAY_CLOSE, when=NOW)
    ledger.settle({k: "no" for k in "ABCDE"}, NOW + timedelta(hours=1))  # −14,30 $: más del 25 %
    assert ledger.paused() and ledger.affordable("Z", D("0.5"), 3) == 0
    assert ledger.worst_day() == pytest.approx(D("-14.30"))
    ledger.new_day("2026-10-14")
    assert not ledger.paused() and ledger.affordable("Z", D("0.5"), 3) == 3
    assert ledger.a["curve"][-1]["date"] == "2026-10-13"
    assert ledger.drawdown() == pytest.approx(D("14.30") / 25)


# --- ¿gana o es suerte? ---------------------------------------------------------------------------


def test_winning_23_of_23_favorites_proves_nothing():
    ops = [{"pnl": 0.03, "bets": [[1, 0.97]]} for _ in range(23)]
    assert 0.4 < luck(ops) < 0.6  # 0,97^23 ≈ 0,5: pasa la mitad de las veces sin ventaja ninguna
    many = [{"pnl": 0.04, "bets": [[1, 0.96]]} for _ in range(120)]
    assert luck(many) < 0.05  # 120 de 120 a 96¢ ya casi no es suerte
    assert luck([{"pnl": 0.05, "bets": []}]) == 0.0  # lo asegurado al comprar no depende de la suerte
    assert luck([]) is None
    losing = [{"pnl": -0.97, "bets": [[1, 0.97]]}] + [{"pnl": 0.03, "bets": [[1, 0.97]]} for _ in range(30)]
    assert luck(losing) > 0.5


def test_the_verdict_waits_for_weeks_and_does_not_trust_luck():
    account = new_account("2026-10-13")
    row = summary(account, "2026-10-13", None)
    assert row["state"] == "aprendiendo" and "40 operaciones y 13 días" in row["text"]
    assert [c["ok"] for c in row["checks"]] == [False, False, False, False, True]
    account["since"] = "2026-09-25"
    account["settled"] = [{"pnl": 0.04, "bets": [[1, 0.96]]} for _ in range(120)]
    account["totals"] = {"ops": 120, "wins": 120, "losses": 0, "pnl": "4.80", "spent": "115.2"}
    account["cash"] = "29.80"
    ready = summary(account, "2026-10-13", luck(account["settled"]))
    assert ready["state"] == "lista" and "si tú lo apruebas" in ready["text"]
    assert all(c["ok"] for c in ready["checks"])
    lucky = summary(account, "2026-10-13", 0.3)
    assert lucky["state"] == "dudosa" and "30 %" in lucky["text"]
    account["totals"]["pnl"] = "-1.20"
    assert summary(account, "2026-10-13", 0.9)["state"] == "descartada"


# --- las estrategias --------------------------------------------------------------------------------


def test_the_notary_buys_only_what_the_thermometer_already_decided():
    assert st.decided(chicago_today()[1], cities(), NOW)[0] == "no"  # 81° ≥ 79 + 2
    assert st.decided(chicago_today()[2], cities(), NOW) is None  # 80°–81° sigue vivo
    assert st.decided(chicago_today()[3], cities(), NOW) is None  # 82° o más: aún no
    old = cities(at="2026-10-13T15:00:00+00:00")
    assert st.decided(chicago_today()[1], old, NOW) is None  # lecturas de hace 6 h: no decide nada
    tomorrow = wx("KXHIGHCHI-26OCT14-B78.5", "78° to 79°", "0.03", "0.05", close=TOMORROW_CLOSE)
    assert st.decided(tomorrow, cities(), NOW) is None  # lo de hoy no decide mañana
    ny = wx("KXHIGHNY-26OCT13-T75", "75° or above", "0.93", "0.96")
    assert st.decided(ny, cities(), NOW)[0] == "yes"  # ya van 76°
    ledger = Ledger(new_account("2026-10-13"))
    mem: dict = {}
    data = st.Data(now=NOW, today="2026-10-13", weather=chicago_today() + [ny], cities=cities())
    events = st.Notaria().step(ledger, data, mem)
    assert [(e["ticker"], e["side"], e["count"], e["price"]) for e in events] == [
        ("KXHIGHCHI-26OCT13-B78.5", "NO", 3, 0.97),
        ("KXHIGHNY-26OCT13-T75", "SÍ", 3, 0.96),
    ]
    assert mem["stats"]["seen"] == sorted(["KXHIGHCHI-26OCT13-T77", "KXHIGHCHI-26OCT13-B78.5", "KXHIGHNY-26OCT13-T75"])
    assert len(mem["stats"]["cheap"]) == 2  # el de 99¢ no compensa
    assert st.Notaria().step(ledger, data, mem) == []  # no repite
    assert "3 tramos" in st.Notaria().research(mem)[0]


def test_the_shopkeeper_quotes_inside_the_spread_and_fills_from_public_trades():
    market = wx("KXHIGHCHI-26OCT14-B70.5", "70° to 71°", "0.40", "0.46", close=TOMORROW_CLOSE, volume="200")
    narrow = wx("KXHIGHCHI-26OCT14-B72.5", "72° to 73°", "0.30", "0.32", close=TOMORROW_CLOSE, volume="900")
    today = wx("KXHIGHCHI-26OCT13-B80.5", "80° to 81°", "0.40", "0.46", volume="900")  # hoy, por la tarde
    assert st.quotable(market, NOW) and not st.quotable(narrow, NOW) and not st.quotable(today, NOW)
    ledger = Ledger(new_account("2026-10-13"))
    mem: dict = {}
    tendera = st.Tendera()
    data = st.Data(now=NOW, today="2026-10-13", weather=[market, narrow, today])
    assert tendera.step(ledger, data, mem) == []
    quote = mem["quotes"]["KXHIGHCHI-26OCT14-B70.5"]
    assert (quote["bid"], quote["ask"], quote["bid_size"], quote["ask_size"]) == ("0.41", "0.45", 2, 2)
    later = NOW + timedelta(minutes=5)

    def at(minutes):
        return (NOW + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")

    trades = [
        {"created_time": at(1), "yes_price_dollars": "0.40", "count_fp": "3.00", "taker_side": "no"},
        {"created_time": at(2), "yes_price_dollars": "0.47", "count_fp": "1.00", "taker_side": "yes"},
        {"created_time": at(3), "yes_price_dollars": "0.44", "count_fp": "5.00", "taker_side": "yes"},  # no llega
        {"created_time": at(3), "yes_price_dollars": "0.10", "count_fp": "50", "taker_side": "no", "is_block_trade": 1},
        {"created_time": at(-3), "yes_price_dollars": "0.30", "count_fp": "9", "taker_side": "no"},  # antes
    ]
    asked = []

    def public(ticker, min_ts, max_ts):
        asked.append((ticker, min_ts, max_ts))
        return trades

    data = st.Data(now=later, today="2026-10-13", weather=[market], trades=public)
    events = tendera.step(ledger, data, mem)
    assert asked == [("KXHIGHCHI-26OCT14-B70.5", int(NOW.timestamp()), int(later.timestamp()) + 1)]
    assert [(e["side"], e["count"], e["price"]) for e in events] == [("SÍ", 2, 0.41), ("NO", 1, 0.55)]
    pos = ledger.position("KXHIGHCHI-26OCT14-B70.5")
    assert D(pos["realized"]) == D("0.04") and ledger.net("KXHIGHCHI-26OCT14-B70.5", "KXHIGHCHI-26OCT14-B70.5") == 1
    assert "KXHIGHCHI-26OCT14-B70.5" in mem["quotes"]  # sigue con órdenes
    # Si el pueblo estuvo parado más de 15 minutos, lo de en medio no cuenta.
    data = st.Data(now=later + timedelta(minutes=30), today="2026-10-13", weather=[], trades=public)
    assert tendera.step(ledger, data, mem) == [] and mem["quotes"] == {}


def test_the_accountant_buys_every_bracket_only_when_they_add_up_to_less_than_a_dollar():
    miami = [
        wx("KXHIGHMIA-26OCT14-T85", "85° or below", "0.28", "0.30", close=TOMORROW_CLOSE),
        wx("KXHIGHMIA-26OCT14-B86.5", "86° to 87°", "0.28", "0.30", close=TOMORROW_CLOSE),
        wx("KXHIGHMIA-26OCT14-T88", "88° or above", "0.28", "0.30", close=TOMORROW_CLOSE),
    ]
    assert st.complete(miami) and st.complete(chicago_today())
    assert not st.complete(miami[:2]) and not st.complete([miami[0], miami[2]])
    game = {"event_ticker": "KXNFLGAME-26OCT13KCLV", "title": "Kansas City at Las Vegas Winner?"}
    nfl = [
        {**game, "ticker": "KXNFLGAME-26OCT13KCLV-KC", "yes_bid_dollars": "0.30", "yes_ask_dollars": "0.32"},
        {**game, "ticker": "KXNFLGAME-26OCT13KCLV-LV", "yes_bid_dollars": "0.30", "yes_ask_dollars": "0.32"},
    ]
    ledger = Ledger(new_account("2026-10-13"))
    mem: dict = {}
    data = st.Data(now=NOW, today="2026-10-13", weather=chicago_today() + miami, games=nfl)
    events = st.Contable().step(ledger, data, mem)
    # Miami: el SÍ de los 3 tramos cuesta 0,90 + 0,06 de comisiones y se cobra 1 $ seguro.
    # NFL: el SÍ de los dos no vale (puede haber empate) y el NO de los dos cuesta 1,40 por 1 $.
    assert [(e["ticker"], e["side"], e["count"]) for e in events] == [("KXHIGHMIA-26OCT14", "SÍ", 3)]
    pos = ledger.position("KXHIGHMIA-26OCT14")
    assert pos["sure"] and len(pos["legs"]) == 3 and D(pos["fees"]) == D("0.15")
    assert pos["name"] == "Máxima en Miami (14 oct)"
    assert st.Contable().step(ledger, data, mem) == []  # una vez por evento
    results = {"KXHIGHMIA-26OCT14-T85": "no", "KXHIGHMIA-26OCT14-B86.5": "yes", "KXHIGHMIA-26OCT14-T88": "no"}
    assert ledger.settle(results, NOW + timedelta(days=2))[0]["pnl"] == pytest.approx(3 - 2.70 - 0.15)
    # Sin dinero gratis, cuenta lo más cerca que estuvo.
    mem = {}
    data = st.Data(now=NOW, today="2026-10-13", weather=chicago_today())
    st.Contable().step(Ledger(new_account("2026-10-13")), data, mem)
    assert "faltaban 12¢" in " ".join(st.Contable().research(mem))


# --- la Mentora y su sala ------------------------------------------------------------------------------


def test_the_mentor_runs_the_incubator_and_keeps_the_score():
    world = World(None)
    kalshi = FakeKalshiPublic()
    kalshi.series["KXHIGHCHI"] = chicago_today()
    world.update("nube", detail={"cities": list(cities().values())})
    world.update("herrera", detail={"totals": {"markets": 71, "wins": 66, "net": 2.49}})
    world.update("probadora", detail={"variants": [{"id": "tal_cual", "trades": 31, "wins": 29, "pnl": 1.4}]})
    clock = {"t": NOW.timestamp()}
    mentora = Mentora(world, kalshi, clock=lambda: clock["t"])
    town = Town(world, [mentora], clock=lambda: clock["t"])
    assert {"mentora", "notaria", "tendera", "contable"} <= set(world.bots)
    assert all(world.bot(b)["home"] == "incubadora" for b in ("mentora", "notaria", "tendera", "contable"))
    mentora.tick(clock["t"])
    said = texts(world, **{"from": "notaria", "to": "mentora"})
    assert said and "compro 3 NO de «Máxima en Chicago · 78° a 79°» a 97¢" in said[0]
    board = {r["id"]: r for r in world.bot("mentora")["detail"]["board"]}
    assert board["notaria"]["open"] == 1 and board["notaria"]["state"] == "aprendiendo"
    assert [b["id"] for b in world.bot("mentora")["detail"]["benchmarks"]] == ["kali", "probadora"]
    notaria = world.bot("notaria")
    assert notaria["detail"]["open"][0]["held"] == [
        {"ticker": "KXHIGHCHI-26OCT13-B78.5", "side": "NO", "count": 3, "price": 0.97}
    ]
    assert notaria["activity"]["room"] == "incubadora"
    # Al día siguiente se decide: la Notaria cobra.
    clock["t"] = (NOW + timedelta(hours=15)).timestamp()
    kalshi.series["KXHIGHCHI"] = []
    kalshi.by_ticker["KXHIGHCHI-26OCT13-B78.5"] = {"ticker": "KXHIGHCHI-26OCT13-B78.5", "result": "no"}
    mentora.tick(clock["t"])
    board = {r["id"]: r for r in world.bot("mentora")["detail"]["board"]}
    assert board["notaria"]["ops"] == 1 and board["notaria"]["pnl"] == pytest.approx(0.08)
    assert any("Cobrado de mentira" in t for t in texts(world, **{"from": "notaria"}))
    # Hablar con la Mentora y con cada estrategia.
    assert "Así van las pruebas de mentira" in town.talk("mentora")["text"]
    assert "Aún no se puede juzgar" in town.talk("notaria")["text"]
    # El resumen del día, una vez, a partir de las 9 de la noche.
    clock["t"] = datetime(2026, 10, 15, 2, 30, tzinfo=timezone.utc).timestamp()  # 21:30 en Chicago
    mentora.tick(clock["t"])
    mentora.tick(clock["t"] + 300)
    assert len([t for t in texts(world, **{"from": "mentora"}) if t.startswith("Así va la Incubadora")]) == 1


def test_a_strategy_that_breaks_does_not_stop_the_others():
    class Broken:
        id, name, color, role, about, rule = "rota", "Rota", "#000", "", "", ""

        def step(self, ledger, data, mem):
            raise ValueError("se ha roto")

        def research(self, mem):
            return []

        def doing(self, mem, events):
            return "nada"

    world = World(None)
    kalshi = FakeKalshiPublic()
    kalshi.series["KXHIGHCHI"] = chicago_today()
    world.update("nube", detail={"cities": list(cities().values())})
    mentora = Mentora(world, kalshi, clock=lambda: NOW.timestamp(), strategies=(Broken(), st.Notaria()))
    mentora.tick(NOW.timestamp())
    assert world.bot("rota")["mood"] == "sick" and "se ha roto" in world.bot("rota")["status"]
    assert world.bot("notaria")["detail"]["summary"]["open"] == 1
