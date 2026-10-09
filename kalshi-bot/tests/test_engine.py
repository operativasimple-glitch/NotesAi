import json
import logging
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D

import pytest

from kalshi_bot.client import KalshiAPIError
from kalshi_bot.engine import Bot, DryRunExecutor, EngineConfig, Journal, LiveExecutor, reconcile
from kalshi_bot.models import ASK, BID, GTC, IOC, Balance, Order, OrderIntent
from kalshi_bot.risk import RiskLimits, RiskManager
from kalshi_bot.strategies.fair_value import FairValueStrategy
from kalshi_bot.strategies.favorites import FavoritesStrategy
from kalshi_bot.strategies.market_maker import MarketMakerStrategy

from .fakes import NOW, FakeKalshi, make_book, make_market

T = "KXTEST-26OCT08-B50"


class Clock:
    def __init__(self):
        self.t = 0.0

    def monotonic(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds

    def now(self):
        return NOW + timedelta(seconds=self.t)


@pytest.fixture(autouse=True)
def no_signal_handlers(monkeypatch):
    monkeypatch.setattr(Bot, "_install_signal_handlers", lambda self: None)


def market_maker():
    return MarketMakerStrategy({"half_spread": 0.02, "quote_size": 2, "max_position": 10, "skew_per_contract": 0})


def setup(strategy=None, *, dry_run=False, limits=None, journal=None, authenticated=True, **cfg):
    fake = FakeKalshi(
        markets=[make_market(T)],
        books={T: make_book(T, bids=[("0.40", 10)], asks=[("0.50", 10)])},
        authenticated=authenticated,
    )
    clock = Clock()
    journal = journal or Journal(None)
    if dry_run:
        executor = DryRunExecutor("kb", journal)
    else:
        executor = LiveExecutor(fake, "kb", journal, ttl_seconds=cfg.pop("ttl", 0), clock=lambda: 1_000_000)
    config = EngineConfig(tickers=[T], **cfg)
    bot = Bot(
        fake,
        strategy or market_maker(),
        RiskManager(limits or RiskLimits()),
        executor,
        config,
        journal=journal,
        now=clock.now,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    bot.startup()
    return bot, fake, clock


def resting(fake):
    return sorted((o.side, o.price, o.count if hasattr(o, "count") else o.remaining) for o in fake.orders.values())


def test_reconcile():
    existing = [Order("1", "kb-1", T, BID, D("0.43"), D("2")), Order("2", "kb-2", T, ASK, D("0.47"), D("2"))]
    desired = [OrderIntent(T, BID, D("0.43"), D("2")), OrderIntent(T, ASK, D("0.48"), D("2"))]
    keep, cancel, place = reconcile(existing, desired)
    assert [o.order_id for o in keep] == ["1"] and [o.order_id for o in cancel] == ["2"]
    assert [(i.side, i.price) for i in place] == [(ASK, D("0.48"))]
    keep, cancel, place = reconcile(existing, desired, tolerance=D("0.01"))
    assert len(keep) == 2 and cancel == [] and place == []
    # Si alguien le vendió solo una fracción (faltan menos de 1), la orden se queda en la cola.
    nibbled = [Order("3", "kb-3", T, BID, D("0.43"), D("1.85"))]
    assert reconcile(nibbled, desired[:1])[0] == nibbled
    assert reconcile([Order("4", "kb-4", T, BID, D("0.43"), D("0.85"))], desired[:1])[2] == desired[:1]


def test_dry_run_never_calls_the_exchange_and_only_logs_changes():
    bot, fake, clock = setup(dry_run=True)
    bot.tick()
    sim = bot.executor.resting_orders()
    assert sorted((o.side, o.price) for o in sim) == [(ASK, D("0.47")), (BID, D("0.43"))]
    ids = sorted(o.order_id for o in sim)
    clock.sleep(10)
    bot.tick()
    assert sorted(o.order_id for o in bot.executor.resting_orders()) == ids  # nada que cambiar
    assert fake.created == [] and fake.cancelled == []


def test_dry_run_without_credentials_uses_paper_cash():
    bot, fake, _ = setup(dry_run=True, authenticated=False)
    assert bot.risk.start_equity == D("1000")
    bot.tick()  # FakeKalshi falla si se piden saldo/posiciones sin credenciales
    assert len(bot.executor.resting_orders()) == 2


def test_live_places_then_requotes_when_book_moves():
    bot, fake, clock = setup()
    bot.tick()
    assert sorted((c["intent"].side, c["intent"].price) for c in fake.created) == [
        (ASK, D("0.47")),
        (BID, D("0.43")),
    ]
    assert all(c["client_order_id"].startswith("kb-") for c in fake.created)

    clock.sleep(10)
    bot.tick()
    assert len(fake.created) == 2 and fake.cancelled == []  # mismo libro: no se toca nada

    fake.books[T] = make_book(T, bids=[("0.50", 10)], asks=[("0.60", 10)])
    clock.sleep(10)
    bot.tick()
    assert len(fake.cancelled) == 2
    assert sorted((o.side, o.price) for o in fake.orders.values()) == [(ASK, D("0.57")), (BID, D("0.53"))]


def test_gtc_orders_get_ttl_and_ioc_do_not():
    strategy = FairValueStrategy({"fair_values": {T: 0.60}, "min_edge": 0.04, "order_size": 2})
    bot, fake, _ = setup(strategy, ttl=600)
    bot.tick()
    assert [(c["intent"].time_in_force, c["expiration_ts"]) for c in fake.created] == [(IOC, None)]

    bot, fake, _ = setup(ttl=600)
    bot.tick()
    assert {c["expiration_ts"] for c in fake.created} == {1_000_600}


def test_manual_orders_are_never_touched():
    bot, fake, _ = setup()
    manual = fake.add_manual_order(T, BID, "0.30", 5)
    bot.tick()
    bot.shutdown()
    assert manual in fake.orders and manual not in fake.cancelled
    assert [o.order_id for o in fake.orders.values()] == [manual]  # las del bot se cancelaron al salir


def test_bot_orders_on_unfollowed_markets_are_cancelled():
    bot, fake, _ = setup()
    fake.orders["old"] = Order("old", "kb-viejo", "KXOTRO-1", BID, D("0.2"), D("1"))
    bot.tick()
    assert "old" in fake.cancelled


def test_market_about_to_close_gets_cancelled():
    bot, fake, clock = setup()
    bot.tick()
    assert len(fake.orders) == 2
    clock.sleep(6 * 3600 - 10 * 60)  # faltan 10 min para el cierre
    bot.tick()
    assert len(fake.cancelled) == 2 and fake.orders == {}


def test_session_loss_halts_and_cancels():
    bot, fake, clock = setup(limits=RiskLimits(max_session_loss=D("20")))
    bot.tick()
    assert len(fake.orders) == 2
    fake.balance = Balance(D("75"), D("0"))  # empezó con $100
    bot.tick()
    assert bot.halted_reason and "pérdida" in bot.halted_reason
    assert fake.orders == {} and bot._stop


def test_paused_exchange_does_nothing():
    bot, fake, _ = setup()
    fake.trading_active = False
    bot.tick()
    assert fake.created == []


def test_risk_limits_are_applied():
    bot, fake, _ = setup(limits=RiskLimits(max_order_contracts=D("1")))
    bot.tick()
    assert {c["intent"].count for c in fake.created} == {D("1")}


def test_each_risk_note_is_logged_once_not_every_tick(caplog):
    bot, fake, _ = setup(limits=RiskLimits(max_order_contracts=D("1")))
    notes = lambda: [r.getMessage() for r in caplog.records if "riesgo:" in r.getMessage()]  # noqa: E731
    with caplog.at_level(logging.INFO, logger="kalshi_bot.engine"):
        bot.tick()
        first = notes()
        bot.tick()
        bot.tick()
        assert first and notes() == first  # mismo aviso: no se repite
        fake.books[T] = make_book(T, bids=[("0.30", 10)], asks=[("0.50", 10)])
        bot.tick()
    assert len(notes()) > len(first)  # cambia el precio: aviso nuevo


def test_existing_position_limits_quotes():
    # La estrategia permitiría 50 contratos; el límite global de riesgo (20) es el que manda.
    strategy = MarketMakerStrategy({"quote_size": 2, "max_position": 50, "skew_per_contract": 0})
    bot, fake, _ = setup(strategy, limits=RiskLimits(max_position_per_market=D("20")))
    fake.set_position(T, 19, exposure="8")
    bot.tick()
    bids = [c["intent"] for c in fake.created if c["intent"].side == BID]
    assert [b.count for b in bids] == [D("1")]


def test_taker_cooldown():
    strategy = FairValueStrategy({"fair_values": {T: 0.60}, "min_edge": 0.04, "order_size": 2})
    bot, fake, clock = setup(strategy, taker_cooldown_seconds=30)
    bot.tick()
    assert len(fake.created) == 1
    clock.sleep(10)
    bot.tick()
    assert len(fake.created) == 1  # en enfriamiento
    clock.sleep(25)
    bot.tick()
    assert len(fake.created) == 2


def test_fills_are_logged_once_and_attributed_to_the_bot(tmp_path):
    journal = Journal(tmp_path / "journal.jsonl")
    bot, fake, clock = setup(journal=journal)
    told = []  # el panel recibe solo los llenados del bot (para avisar al móvil)
    bot.on_fill = lambda fill: told.append(fill["fill_id"]) or 1 / 0  # aunque el aviso falle, el bot sigue
    bot.tick()
    bot_order_id = next(iter(fake.orders))
    fill = {
        "ticker": T,
        "book_side": "bid",
        "count_fp": "2.00",
        "yes_price_dollars": "0.4300",
        "is_taker": False,
        "fee_cost": "0.0100",
        "ts": 1_800_000_000,
    }
    fake.fills = [
        {**fill, "fill_id": "f1", "order_id": bot_order_id},
        {**fill, "fill_id": "f2", "order_id": "orden-manual"},
    ]
    for _ in range(2):
        clock.sleep(10)
        bot.tick()
    records = [json.loads(line) for line in (tmp_path / "journal.jsonl").read_text().splitlines()]
    fills = [(r["fill"]["fill_id"], r["from_bot"]) for r in records if r["event"] == "fill"]
    assert sorted(fills) == [("f1", True), ("f2", False)]
    assert bot._fills_since == 1_800_000_000 - 1
    assert told == ["f1"]


def test_intents_for_other_markets_are_ignored():
    class Wandering(MarketMakerStrategy):
        def on_market(self, ctx):
            return [OrderIntent("KXOTRO-1", BID, D("0.10"), D("1"))] + super().on_market(ctx)

    bot, fake, _ = setup(Wandering({"half_spread": 0.02, "quote_size": 2, "skew_per_contract": 0}))
    bot.tick()
    assert {c["intent"].ticker for c in fake.created} == {T}


def test_series_discovery_filters_and_ranks(monkeypatch):
    bot, fake, _ = setup()
    bot.cfg.tickers = []
    bot.cfg.series = ["KXSER"]
    bot.cfg.max_markets = 2
    bot.cfg.min_hours_to_close = 1
    fake.markets = {
        m.ticker: m
        for m in [
            make_market("KXSER-A", volume_24h_fp="10"),
            make_market("KXSER-B", volume_24h_fp="500"),
            make_market("KXSER-C", volume_24h_fp="300"),
            make_market("KXSER-D", volume_24h_fp="900", close_time=(NOW + timedelta(minutes=30)).isoformat()),
            make_market("KXSER-E", volume_24h_fp="999", status="closed"),
        ]
    }
    assert list(bot.load_markets(NOW)) == ["KXSER-B", "KXSER-C"]


def test_auth_error_halts_the_run_loop():
    bot, fake, _ = setup()
    fake.failures["get_positions"] = KalshiAPIError(401, "unauthorized", "bad key")
    bot.run(max_ticks=5)
    assert "credenciales" in bot.halted_reason


def test_repeated_errors_trip_the_breaker():
    bot, fake, clock = setup(max_consecutive_errors=3)
    bot.tick()
    assert len(fake.orders) == 2
    fake.failures["get_positions"] = KalshiAPIError(500, "", "caído")
    # Un corte de menos de 3 minutos no frena el bot: espera cada vez más y sigue.
    bot.run(max_ticks=4)
    assert bot.halted_reason is None and bot._consecutive_errors == 4 and clock.t < 180
    bot._stop = False
    bot.run(max_ticks=10)
    assert "vueltas seguidas con errores durante" in bot.halted_reason
    assert fake.orders == {}


def test_errors_back_off_and_recover():
    bot, fake, clock = setup()
    fake.failures["get_positions"] = KalshiAPIError(500, "", "caído")
    bot.run(max_ticks=3)
    poll = bot.cfg.poll_interval
    assert clock.t == poll + 2 * poll  # entre intentos espera el doble cada vez
    assert bot._next_delay() == 4 * poll
    bot._consecutive_errors = 50
    assert bot._next_delay() == 60  # como mucho un minuto
    fake.failures.clear()
    bot._stop = False
    bot.run(max_ticks=1)
    assert bot._consecutive_errors == 0 and bot.halted_reason is None


def test_one_bet_per_event():
    # Dos tramos del mismo día con favorito: solo se apuesta en uno.
    strategy = FavoritesStrategy({})
    a, b = "KXHIGHNY-26OCT08-B66.5", "KXHIGHNY-26OCT08-B68.5"
    bot, fake, _ = setup(strategy)
    for t in (a, b):
        fake.markets[t] = make_market(t, event_ticker="KXHIGHNY-26OCT08")
        fake.books[t] = make_book(t, bids=[("0.05", 50)], asks=[("0.08", 50)])  # NO a 92-95¢
    bot.cfg.tickers = [a, b]
    bot._markets_refreshed_at = None
    bot.tick()
    assert {c["intent"].ticker for c in fake.created} == {a}
    # Con dinero ya en un tramo, el otro no abre nada; pero el que tiene posición puede salir.
    fake.orders.clear()
    fake.set_position(a, -5, exposure="4.65")
    fake.created.clear()
    bot.tick()
    assert all(c["intent"].ticker != b for c in fake.created)
    bot.risk.limits.max_positions_per_event = 0  # sin límite
    bot.tick()
    assert any(c["intent"].ticker == b for c in fake.created)


def test_run_cancels_bot_orders_on_exit():
    bot, fake, _ = setup()
    bot.run(max_ticks=2)
    assert bot.halted_reason is None
    assert fake.orders == {} and len(fake.cancelled) == 2


def test_strategy_crash_does_not_touch_orders():
    class Broken(MarketMakerStrategy):
        fail = False

        def on_market(self, ctx):
            if self.fail:
                raise RuntimeError("bug")
            return super().on_market(ctx)

    strategy = Broken({"half_spread": 0.02, "quote_size": 2, "skew_per_contract": 0})
    bot, fake, _ = setup(strategy)
    bot.tick()
    strategy.fail = True
    bot.tick()
    assert len(fake.orders) == 2 and fake.cancelled == []


def test_intent_helpers_round_to_ticks():
    intent = OrderIntent(T, BID, D("0.43"), D("2"), GTC, post_only=True)
    assert intent.describe() == f"COMPRA YES 2.00 @ 0.4300 [GTC post-only] {T}"
    assert OrderIntent(T, ASK, D("0.6"), D("1"), IOC).cost_per_contract() == D("0.4")


def favorites_with_exits():
    return FavoritesStrategy({"stop_loss": "0.50", "take_profit": "0.99"})


def test_exits_still_work_in_the_last_minutes():
    bot, fake, clock = setup(favorites_with_exits())
    fake.books[T] = make_book(T, bids=[("0.90", 50)], asks=[("0.92", 50)])
    bot.tick()
    assert [(c["intent"].side, c["intent"].price) for c in fake.created] == [(BID, D("0.91"))]
    clock.sleep(6 * 3600 - 10 * 60)  # faltan 10 min: ya no compra, pero puede salir
    fake.set_position(T, 5, exposure="4.55")
    fake.books[T] = make_book(T, bids=[("0.40", 50)], asks=[("0.45", 50)])
    bot.tick()
    sell = fake.created[-1]["intent"]
    assert (sell.side, sell.price, sell.count, sell.time_in_force) == (ASK, D("0.40"), D("5"), IOC)
    assert fake.orders == {}  # la compra en reposo se canceló antes de vender

    quiet, fake2, clock2 = setup()  # sin salidas (creador de mercado): solo cancela, como siempre
    fake2.set_position(T, 5, exposure="4.55")
    quiet.tick()
    clock2.sleep(6 * 3600 - 10 * 60)
    created = len(fake2.created)
    quiet.tick()
    assert len(fake2.created) == created and fake2.orders == {}


def test_positions_outside_the_list_are_watched_only_to_exit():
    bot, fake, clock = setup(favorites_with_exits(), series=["KXGAME"], max_hours_to_close=6)
    far = make_market("KXGAME-26OCT09-LAD", close_time=(NOW + timedelta(hours=30)).isoformat())
    fake.markets[far.ticker] = far
    fake.books[far.ticker] = make_book(far.ticker, bids=[("0.90", 50)], asks=[("0.92", 50)])
    fake.set_position(far.ticker, 5, exposure="4.60")
    fake.set_position("KXOTRA-1", 5, exposure="4.60")  # serie que el bot no opera: no se vigila
    bot.tick()
    assert list(bot.exit_markets) == [far.ticker] and far.ticker not in bot.markets
    assert all(c["intent"].ticker != far.ticker for c in fake.created)  # ahí no compra
    fake.books[far.ticker] = make_book(far.ticker, bids=[("0.30", 50)], asks=[("0.35", 50)])
    clock.sleep(1)
    bot.tick()
    sells = [c["intent"] for c in fake.created if c["intent"].ticker == far.ticker]
    assert [(i.side, i.price, i.count, i.time_in_force) for i in sells] == [(ASK, D("0.30"), D("5"), IOC)]
    fake.positions.pop(far.ticker)
    clock.sleep(1)
    bot.tick()
    assert bot.exit_markets == {}


def test_weather_take_profit_watches_only_weather_positions(caplog):
    strategy = FavoritesStrategy({"take_profit": "0.99"})  # cobrar a 99¢, por defecto solo en el clima
    bot, fake, clock = setup(strategy, series=["KXHIGHNY", "KXMLBGAME"], max_hours_to_close=6)
    tomorrow = (NOW + timedelta(hours=30)).isoformat()
    weather = make_market("KXHIGHNY-26OCT09-B66.5", close_time=tomorrow)
    game = make_market("KXMLBGAME-26OCT09LADSD-LAD", close_time=tomorrow)
    for market in (weather, game):
        fake.markets[market.ticker] = market
        fake.books[market.ticker] = make_book(market.ticker, bids=[("0.99", 50)])
        fake.set_position(market.ticker, 5, exposure="4.60")
    bot.tick()
    # Solo se vigila (y se lee el libro de) lo que se puede cobrar: el partido espera al final.
    assert list(bot.exit_markets) == [weather.ticker]
    sells = [c["intent"] for c in fake.created if c["intent"].ticker in (weather.ticker, game.ticker)]
    assert [(i.ticker, i.side, i.price, i.count, i.time_in_force) for i in sells] == [
        (weather.ticker, ASK, D("0.99"), D("5"), IOC)
    ]
    assert sells[0].closes and sells[0].reason == "cobrar antes: el SÍ ya se paga a 99¢"
    # Su llenado es una venta, no una compra de NO a 1¢: así lo cuentan la actividad y el aviso.
    (exit_id,) = bot.executor.closing_ids
    told = []
    bot.on_fill = told.append
    fake.fills = [
        {
            "ticker": weather.ticker,
            "book_side": "ask",
            "count_fp": "5.00",
            "yes_price_dollars": "0.9900",
            "is_taker": True,
            "fee_cost": "0.0100",
            "ts": 1_800_000_000,
            "fill_id": "f-exit",
            "order_id": exit_id,
        }
    ]
    with caplog.at_level(logging.INFO, logger="kalshi_bot.engine"):
        for _ in range(2):
            clock.sleep(10)
            bot.tick()
    assert [(f["fill_id"], f["closing"]) for f in told] == [("f-exit", True)]
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("LLENADO")]
    assert lines == [f"LLENADO (salida) VENDE YES 5.00 @ 0.9900 {weather.ticker} (taker, comisión $0.0100)"]


def test_take_profit_sells_fractional_leftovers():
    # Kalshi deja operar fracciones: una venta a 99¢ llenada a medias deja, p. ej., 0,85 NO.
    bot, fake, _ = setup(FavoritesStrategy({"take_profit": "0.99"}), series=["KXHIGHMIA"], max_hours_to_close=6)
    crumb = make_market("KXHIGHMIA-26OCT09-B87.5", close_time=(NOW + timedelta(hours=30)).isoformat())
    fake.markets[crumb.ticker] = crumb
    fake.books[crumb.ticker] = make_book(crumb.ticker, asks=[("0.01", 50)])
    fake.set_position(crumb.ticker, "-0.85", exposure="0.82")
    bot.tick()
    sells = [c["intent"] for c in fake.created if c["intent"].ticker == crumb.ticker]
    assert [(i.side, i.price, i.count, i.time_in_force) for i in sells] == [(BID, D("0.01"), D("0.85"), IOC)]


def cancel_reasons(caplog):
    return [r.getMessage().split(" | ", 1)[1] for r in caplog.records if r.getMessage().startswith("CANCELADA")]


def test_each_cancel_says_why(caplog):
    # El panel enseña el motivo junto a «Cancelada»: retirar una orden no cuesta nada, pero hay que entenderlo.
    bot, fake, clock = setup(FavoritesStrategy({}))
    fake.books[T] = make_book(T, bids=[("0.90", 50)], asks=[("0.93", 50)])
    with caplog.at_level(logging.INFO, logger="kalshi_bot.engine"):
        bot.tick()  # compra SÍ a 91¢
        fake.books[T] = make_book(T, bids=[("0.91", 50)], asks=[("0.93", 50)])  # alguien se pone delante
        clock.sleep(10)
        bot.tick()
        fake.books[T] = make_book(T, bids=[("0.60", 50)], asks=[("0.70", 50)])  # ya no es un favorito
        clock.sleep(10)
        bot.tick()
        fake.books[T] = make_book(T, bids=[("0.90", 50)], asks=[("0.93", 50)])
        clock.sleep(10)
        bot.tick()
        clock.sleep(6 * 3600 - 10 * 60 - clock.t)  # faltan 10 min para el cierre
        bot.tick()
    assert cancel_reasons(caplog) == [
        "la mueve a 92¢",
        "ya no cumple las condiciones para comprar",
        "cierra en 10 min (mínimo 15)",
    ]


def test_a_fractional_fill_keeps_the_order_in_the_queue():
    bot, fake, clock = setup(FavoritesStrategy({}))
    fake.books[T] = make_book(T, bids=[("0.90", 50)], asks=[("0.93", 50)])
    bot.tick()
    ((order_id, order),) = fake.orders.items()
    # Alguien le vende 0,15 de los 5: rehacerla la mandaría al final de la cola, así que se deja.
    fake.orders[order_id] = replace(order, remaining=D("4.85"))
    fake.set_position(T, "0.15", exposure="0.14")
    clock.sleep(10)
    bot.tick()
    assert list(fake.orders) == [order_id] and fake.cancelled == []
    # Con contratos enteros llenados se rehace al tamaño de siempre, como antes.
    fake.orders[order_id] = replace(order, remaining=D("2"))
    fake.set_position(T, "3", exposure="2.73")
    clock.sleep(10)
    bot.tick()
    assert fake.cancelled == [order_id] and [o.remaining for o in fake.orders.values()] == [D("5")]


def test_after_you_cancel_a_bot_order_it_stops_buying_there():
    bot, fake, clock = setup(FavoritesStrategy({}))
    fake.books[T] = make_book(T, bids=[("0.90", 50)], asks=[("0.93", 50)])
    bot.tick()
    (order_id,) = fake.orders
    fake.cancel_order(order_id)  # el botón «Cancelar» del panel
    bot.stop_buying(T)
    created = len(fake.created)
    clock.sleep(10)
    bot.tick()
    assert len(fake.created) == created and fake.orders == {}  # no la vuelve a poner


def test_markets_on_another_exchange_shard_are_skipped_after_one_refusal(caplog):
    # Kalshi reparte los mercados en partes (shards) con saldo propio; por la API no lo mueve solo.
    bot, fake, clock = setup(FavoritesStrategy({}))
    fake.books[T] = make_book(T, bids=[("0.90", 50)], asks=[("0.93", 50)])
    tries = []

    def refuse(intent, client_order_id, expiration_ts=None):
        tries.append(intent.ticker)
        raise KalshiAPIError(
            404, "insufficient_shard_balance", "insufficient shard balance", details="Exchange user not found"
        )

    fake.create_order = refuse
    with caplog.at_level(logging.INFO, logger="kalshi_bot.engine"):
        for _ in range(3):
            bot.tick()
            clock.sleep(10)
    assert tries == [T] and bot.other_shard == {"KXTEST"}  # una vez, no en cada vuelta
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "otra parte del exchange" in warnings[0]


def test_each_loop_says_why_it_is_not_buying():
    # El panel enseña, bajo «Bot activo», dónde tiene dinero el bot y por qué no compra en el resto.
    bot, fake, clock = setup(FavoritesStrategy({}))
    decided, unsure, wide, soon = "KXTEST-A", "KXTEST-B", "KXTEST-C", "KXTEST-D"
    books = {
        T: make_book(T, bids=[("0.90", 50)], asks=[("0.93", 50)]),  # favorito: compra
        decided: make_book(decided, bids=[("0.99", 50)]),  # ya casi decidido
        unsure: make_book(unsure, bids=[("0.45", 50)], asks=[("0.48", 50)]),  # sin favorito
        wide: make_book(wide, bids=[("0.80", 50)], asks=[("0.95", 50)]),  # poca liquidez
    }
    for ticker in (decided, unsure, wide):
        fake.markets[ticker] = make_market(ticker, event_ticker=ticker)
    fake.markets[soon] = make_market(soon, event_ticker=soon, close_time=(NOW + timedelta(minutes=5)).isoformat())
    fake.books.update(books)
    bot.cfg.tickers = [T, decided, unsure, wide, soon]
    bot._markets_refreshed_at = None
    bot.tick()
    scan = bot.last_scan
    assert scan["total"] == len(bot.markets)
    assert scan["reasons"] == {"active": 1, "decided": 1, "no_favorite": 1, "wide_spread": 1, "closing": 1}

    fake.trading_active = False  # Kalshi en pausa
    clock.sleep(10)
    bot.tick()
    assert bot.last_scan["reasons"] == {"exchange_paused": 1}
