from datetime import datetime, timezone

import pytest

from pueblo.agents.consejo import ClaudeMember, Investigadora
from pueblo.agents.cronista import Cronista
from pueblo.agents.kali import log_activity, scan_words
from pueblo.council import book
from pueblo.council.markets import topic, weather_day
from pueblo.council.models import bracket_probability, favorites, fear, greed, momentum, weather
from pueblo.runtime import Town
from pueblo.world import World

from .fakes import Clock, FakeKalshiPublic, weather_market

NY_B72 = "KXHIGHNY-26OCT09-B72.5"
NY_B74 = "KXHIGHNY-26OCT09-B74.5"
NY_T76 = "KXHIGHNY-26OCT09-T76"
# 9 oct 2026, 18:00 en Nueva York (22:00 UTC): la máxima de hoy casi está decidida.
EVENING = datetime(2026, 10, 9, 22, 0, tzinfo=timezone.utc).timestamp()


def texts(world, **filters):
    return [m["text"] for m in world.since() if all(m.get(k) == v for k, v in filters.items())]


def test_temperature_probabilities_follow_the_forecast_and_what_was_measured():
    between = ["between", 72, 73]
    p = bracket_probability(between, 73, 2.0, None)
    assert 0.3 < p < 0.45  # [71,5; 73,5) alrededor de 73 con 2 °F de error
    assert bracket_probability(between, 73, 2.0, 74) == 0  # ya se pasó: imposible
    assert bracket_probability(["above", 76], 73, 2.0, None) < 0.15  # 75,5 o más
    assert bracket_probability(["below", 75], 73, 2.0, None) > 0.75
    late = bracket_probability(["between", 74, 75], 73, 0.9, 74)
    assert late > 0.9  # ya van 74° a última hora: casi seguro en 74–75
    total = sum(bracket_probability(["between", lo, lo + 1], 73, 2.0, None) for lo in range(50, 100, 2))
    assert total == pytest.approx(1, abs=1e-6)


def test_each_member_reads_a_market_its_own_way():
    assert weather_day(NY_B72) == ("NY", datetime(2026, 10, 9).date())
    t = topic(
        weather_market(NY_B72, "72° to 73°", "0.30", "0.34", previous_price_dollars="0.20", last_price_dollars="0.33")
    )
    assert t["kind"] == "weather" and t["bracket"] == ["between", 72, 73] and t["mid"] == 0.32
    assert t["name"] == "Máxima en Nueva York · 72° a 73°"
    rows = {"NY": {"key": "NY", "today": 73, "tomorrow": 70, "max_so_far": 71}}
    ctx = {"now": datetime(2026, 10, 9, 15, 0, tzinfo=timezone.utc), "cities": rows}  # 11:00 en NY
    assert 0.3 < weather(t, ctx) < 0.5
    assert weather(t, {**ctx, "now": datetime(2026, 10, 12, 15, 0, tzinfo=timezone.utc)}) is None  # ya pasó
    assert momentum(t, ctx) == pytest.approx(0.32 + 0.5 * 0.13)
    assert favorites({"mid": 0.9}, ctx) == pytest.approx(0.94) and favorites({"mid": 0.5}, ctx) == 0.5
    assert fear({"mid": 0.9}, ctx) == pytest.approx(0.78) and greed({"mid": 0.7}, ctx) > 0.78
    assert topic({"ticker": "X", "yes_bid_dollars": "0", "yes_ask_dollars": "1"}) is None  # sin ofertas


def test_paper_bets_pay_kalshi_fees_and_settle():
    assert book.fee(0.5) == 0.02 and book.fee(0.9) == 0.01 and book.fee(0.99) == 0.01
    t = {"bid": 0.45, "ask": 0.5}
    assert book.decide(0.6, t) == ("SÍ", 0.5)
    assert book.decide(0.3, t) == ("NO", 0.55)
    assert book.decide(0.52, t) is None  # no compensa
    yes = {"side": "SÍ", "price": 0.5, "fee": 0.02}
    assert book.pnl(yes, "yes") == 0.48 and book.pnl(yes, "no") == -0.52
    no = {"side": "NO", "price": 0.55, "fee": 0.02}
    assert book.pnl(no, "no") == 0.43
    rows = book.board(
        [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}],
        {"b": {"x": {}}},
        {"a": [{"pnl": 0.48, "price": 0.5, "fee": 0.02}, {"pnl": -0.52, "price": 0.5, "fee": 0.02}]},
    )
    assert [r["id"] for r in rows] == ["b", "a"] and rows[1]["wins"] == 1 and rows[0]["open"] == 1


def council_town(clock):
    world = World(None, clock)
    kalshi = FakeKalshiPublic()
    kalshi.series["KXHIGHNY"] = [
        weather_market(NY_B72, "72° to 73°", "0.30", "0.33"),
        weather_market(NY_B74, "74° to 75°", "0.40", "0.43"),
        weather_market(NY_T76, "76° or above", "0.02", "0.04"),
    ]
    world.update(
        "nube",
        detail={
            "cities": [{"key": "NY", "city": "Nueva York", "today": 75, "tomorrow": 70, "max_so_far": 74, "now": 73}]
        },
    )
    return world, kalshi, Investigadora(world, kalshi, clock=clock)


def test_the_council_bets_and_keeps_the_score():
    clock = Clock(EVENING)
    world, kalshi, investigadora = council_town(clock)
    investigadora.run_if_due(clock())
    detail = world.bot("investigadora")["detail"]
    tickers = [t["ticker"] for t in detail["topics"]]
    assert tickers == [NY_B72, NY_B74, NY_T76][: len(tickers)] and NY_B74 in tickers
    b74 = next(t for t in detail["topics"] if t["ticker"] == NY_B74)
    assert b74["fairs"]["tormenta"] > 0.8  # ya van 74° a las seis de la tarde
    books = investigadora.memory["books"]
    assert books["tormenta"][NY_B74]["side"] == "SÍ" and books["tormenta"][NY_B72]["side"] == "NO"
    said = texts(world, to="investigadora")
    assert len(said) == 1 and said[0].startswith("Apunta: compro el ")
    tormenta = world.bot("tormenta")
    assert tormenta["activity"]["room"] == "consejo" and tormenta["detail"]["member"] is True
    assert tormenta["status"] == "Aún sin apuestas decididas"

    # Se cierran los mercados: la máxima fue 74°.
    for ticker, result in ((NY_B72, "no"), (NY_B74, "yes"), (NY_T76, "no")):
        kalshi.by_ticker[ticker] = {"ticker": ticker, "result": result, "status": "finalized"}
    clock.advance(8 * 3600)  # ya cerraron (a medianoche de Nueva York)
    investigadora.run_if_due(clock())
    board = world.bot("investigadora")["detail"]["board"]
    leader = board[0]
    assert leader["id"] == "tormenta" and leader["trades"] == 2 and leader["wins"] == 2 and leader["pnl"] > 0.8
    assert world.bot("tormenta")["mood"] == "happy"
    assert any(t.startswith("¡Acerté con «Máxima en Nueva York · 74° a 75°»!") for t in texts(world, kind="trade"))
    assert any(t.startswith("Se han decidido ") for t in texts(world))
    assert not investigadora.memory["books"].get("tormenta")

    # El diario de la noche cuenta quién va ganando.
    cronista = Cronista(world, "America/Chicago", clock=clock)
    world.update("kali", detail={"connected": True, "today": {"net": "1", "markets": 1, "wins": 1, "losses": 0}})
    assert "En el Consejo va ganando Cazatormentas" in cronista.evening()

    # Hablar con un miembro: contesta la Investigadora con su voz.
    town = Town(world, [investigadora])
    reply = town.talk("tormenta")
    assert reply["from"] == "tormenta" and reply["text"].startswith("Llevo 2 de 2 acertadas")


def test_the_council_warns_kali_when_her_bet_looks_expensive():
    clock = Clock(EVENING)
    world, kalshi, investigadora = council_town(clock)
    kalshi.by_ticker[NY_B72] = weather_market(NY_B72, "72° to 73°", "0.30", "0.33")
    world.update(
        "kali",
        detail={
            "positions": [
                {"ticker": NY_B72, "name": "Máxima en Nueva York · 72° a 73°", "side": "SÍ", "avg_price": "0.60"}
            ]
        },
    )
    investigadora.run_if_due(clock())
    warning = texts(world, to="kali")
    assert len(warning) == 1 and "vale" in warning[0] and "lo pagaste a 60¢" in warning[0]
    assert world.bot("investigadora")["detail"]["topics"][0]["held"] is True


class FakeForecaster:
    def __init__(self):
        self.prompts = []

    def forecast(self, prompt):
        self.prompts.append(prompt)
        return {"fair": 0.91, "reason": "Ya van 74° y queda poco día."}


def test_claude_thinks_a_few_markets_a_day():
    clock = Clock(EVENING)
    world, kalshi, investigadora = council_town(clock)
    forecaster = FakeForecaster()
    claude = ClaudeMember(world, forecaster, max_calls=2, clock=clock)
    claude.run_if_due(clock())
    assert world.bot("claude")["mood"] == "sleep"  # aún no hay mercados
    investigadora.run_if_due(clock())
    claude.next_run = 0
    claude.run_if_due(clock())
    assert len(forecaster.prompts) == 1 and "Servicio Meteorológico" in forecaster.prompts[0]
    opinions = world.bot("claude")["detail"]["opinions"]
    assert list(opinions.values())[0]["fair"] == 0.91
    claude.next_run = 0
    claude.run_if_due(clock())
    claude.next_run = 0
    claude.run_if_due(clock())
    assert len(forecaster.prompts) == 2 and "descanso hasta mañana" in world.bot("claude")["status"]
    clock.advance(600)
    investigadora.next_run = 0
    investigadora.run_if_due(clock())
    assert "claude" in investigadora.memory["books"] or any(
        r["id"] == "claude" for r in world.bot("investigadora")["detail"]["board"]
    )


def test_kali_walks_to_the_room_of_what_she_really_does():
    names = {"KXHIGHNY-26OCT09-B72.5": "Máxima en Nueva York · 72° a 73°"}
    order = (
        "ORDEN COMPRA YES 5.00 @ 0.9300 [GTC post-only] KXHIGHNY-26OCT09-B72.5 | id=abc llenado=0 "
        "pendiente=5 | favorito YES (bid 0.92, ask 0.94)"
    )
    assert log_activity(order, names) == ("mercado", "orden: 5 SÍ a 93¢ · Máxima en Nueva York · 72° a 73°")
    exit_ = (
        "ORDEN VENDE YES 5.00 @ 0.9900 (= COMPRA NO @ 0.0100) [IOC] KXHIGHNY-26OCT09-B72.5 | id=x "
        "llenado=5 pendiente=0 | cobrar antes: el SÍ ya se paga a 99¢"
    )
    assert log_activity(exit_, names) == ("mercado", "vende su SÍ · Máxima en Nueva York · 72° a 73°")
    fill = "LLENADO VENDE YES 5.00 @ 0.1000 KXHIGHNY-26OCT09-B72.5 (maker, comisión $0.0200)"
    assert log_activity(fill, names)[1].startswith("compra 5 NO a 90¢")
    assert log_activity("CANCELADA KXHIGHNY-26OCT09-B72.5 yes 5 @ 0.93 | precio", names)[0] == "mercado"
    assert log_activity("FRENO DE EMERGENCIA: pérdida", names) == ("puente", "¡freno de emergencia!")
    assert log_activity("Mercados seguidos (35): A, B", names) == ("laboratorio", "sigue 35 mercados")
    assert log_activity("Algo que no importa", names) is None
    assert scan_words({"total": 35, "reasons": {"decided": 33, "active": 2}}) == "mira 35 mercados · 33 ya decididos"


def test_the_web_only_gets_the_bots_again_when_they_change():
    world = World(None)
    world.register("kali", name="Kali")
    first = world.snapshot()
    assert "bots" in first
    assert "bots" not in world.snapshot(version=first["version"])
    world.act("kali", "mercado", "compra")
    again = world.snapshot(version=first["version"])
    assert again["bots"][0]["activity"]["room"] == "mercado"
    world.act("kali", "mercado", "compra")  # lo mismo otra vez: no cuenta como cambio
    assert "bots" not in world.snapshot(version=again["version"])


class FakeClaudeClient:
    """Imita client.beta.messages.create y apunta con qué se le llamó."""

    def __init__(self, stop_reason="end_turn", text='{"probabilidad": 87, "razon": "Ya van 74°."}'):
        self.calls = []
        self.stop_reason = stop_reason
        self.text = text
        self.beta = self
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        block = type("Block", (), {"type": "text", "text": self.text})()
        return type("Message", (), {"stop_reason": self.stop_reason, "content": [block]})()


def test_claude_is_asked_for_a_json_answer_with_a_server_fallback():
    from pueblo.council.claude import ClaudeForecaster

    client = FakeClaudeClient()
    answer = ClaudeForecaster("clave", "claude-opus-5-5", client=client).forecast("Mercado: ...")
    assert answer == {"fair": 0.87, "reason": "Ya van 74°."}
    call = client.calls[0]
    assert call["model"] == "claude-opus-5-5" and call["fallbacks"] == "default"
    assert call["betas"] == ["server-side-fallback-2026-07-01"]
    assert call["output_config"]["format"]["type"] == "json_schema" and call["output_config"]["effort"] == "medium"
    assert ClaudeForecaster("clave", "claude-opus-5-5", client=FakeClaudeClient("refusal")).forecast("x") is None
    haiku = FakeClaudeClient()
    ClaudeForecaster("clave", "claude-haiku-5-5", client=haiku).forecast("x")
    assert "fallbacks" not in haiku.calls[0]
