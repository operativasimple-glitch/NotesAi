import os
import sys
from decimal import Decimal as D

import pytest

from kalshi_bot.models import ASK, BID, GTC, IOC, Order
from kalshi_bot.strategies import build_strategy
from kalshi_bot.strategies.base import MarketContext
from kalshi_bot.strategies.fair_value import FairValueStrategy, load_fair_values_csv, parse_probability
from kalshi_bot.strategies.market_maker import MarketMakerStrategy

from .fakes import NOW, make_book, make_market

TICKER = "KXTEST-26OCT08-B50"


def ctx_for(book, position=0, own=()):
    return MarketContext(
        market=make_market(TICKER),
        book=book,
        book_ex_own=book.without_orders(own),
        position=D(str(position)),
        own_orders=list(own),
        now=NOW,
        cash=D("100"),
    )


def summary(intents):
    return [(i.side, i.price, i.count, i.time_in_force, i.post_only) for i in intents]


# --- valor justo -------------------------------------------------------------


def fair(**params):
    base = {"min_edge": 0.04, "order_size": 2, "max_position": 10, "fair_values": {TICKER: 0.60}}
    base.update(params)
    return FairValueStrategy(base)


def test_parse_probability():
    assert parse_probability("0.35") == D("0.35")
    assert parse_probability("35%") == D("0.35")
    assert parse_probability(62) == D("0.62")
    assert parse_probability(1) == D("1")
    assert parse_probability("abc") is None
    assert parse_probability("150") is None


def test_taker_buys_yes_when_cheap_after_fees():
    book = make_book(TICKER, bids=[("0.40", 5)], asks=[("0.50", 3), ("0.53", 10)])
    intents = fair().on_market(ctx_for(book))
    # Con valor justo 0.60, 4¢ de ventaja y comisión taker, el máximo a pagar es 0.54.
    assert summary(intents) == [(BID, D("0.54"), D("2"), IOC, False)]


def test_taker_sells_yes_when_rich():
    book = make_book(TICKER, bids=[("0.40", 4)], asks=[("0.45", 5)])
    intents = fair(fair_values={TICKER: "30%"}).on_market(ctx_for(book))
    assert summary(intents) == [(ASK, D("0.36"), D("2"), IOC, False)]


def test_taker_does_nothing_without_edge_or_without_fair_value():
    book = make_book(TICKER, bids=[("0.45", 5)], asks=[("0.48", 5)])
    assert fair(fair_values={TICKER: 0.47}).on_market(ctx_for(book)) == []
    assert fair(fair_values={}).on_market(ctx_for(book)) == []


def test_taker_respects_max_position_and_book_size():
    book = make_book(TICKER, bids=[("0.40", 5)], asks=[("0.50", 1)])
    assert fair().on_market(ctx_for(book, position=10)) == []
    intents = fair(order_size=5).on_market(ctx_for(book, position=0))
    assert intents[0].count == D("1")  # solo hay 1 contrato a precio con ventaja


def test_maker_quotes_without_crossing():
    book = make_book(TICKER, bids=[("0.40", 5)], asks=[("0.50", 3)])
    intents = fair(mode="maker").on_market(ctx_for(book))
    # El bid con ventaja sería 0.55, pero cruzaría el ask de 0.50: se queda en 0.49.
    assert summary(intents) == [(BID, D("0.49"), D("2"), GTC, True), (ASK, D("0.65"), D("2"), GTC, True)]


def test_invalid_mode_rejected():
    with pytest.raises(ValueError):
        fair(mode="yolo")


def test_fair_values_csv_and_hot_reload(tmp_path):
    path = tmp_path / "fv.csv"
    path.write_text("ticker,probabilidad\n# comentario\nT1,0.35\nT2,45%\nT3,62\nT4,abc\n\n", encoding="utf-8")
    assert load_fair_values_csv(path) == {"T1": D("0.35"), "T2": D("0.45"), "T3": D("0.62")}

    strategy = FairValueStrategy({"fair_values": {"T1": 0.1, "T9": 0.9}, "fair_values_file": str(path)})
    assert strategy.fair_value("T1") == D("0.35")  # el archivo manda sobre config.toml
    assert strategy.fair_value("T9") == D("0.9")
    assert strategy.suggested_tickers() == ["T1", "T2", "T3", "T9"]

    path.write_text("T1,0.80\n", encoding="utf-8")
    later = path.stat().st_mtime + 5
    os.utime(path, (later, later))
    strategy.refresh()
    assert strategy.fair_value("T1") == D("0.80")
    assert strategy.fair_value("T2") is None


def test_missing_fair_values_file_is_not_fatal(tmp_path):
    strategy = FairValueStrategy({"fair_values_file": str(tmp_path / "no-existe.csv")})
    assert strategy.suggested_tickers() == []


# --- creador de mercado ------------------------------------------------------


def maker(**params):
    base = {"half_spread": 0.02, "quote_size": 2, "max_position": 10, "skew_per_contract": 0.002}
    base.update(params)
    return MarketMakerStrategy(base)


def test_market_maker_quotes_around_mid():
    book = make_book(TICKER, bids=[("0.40", 10)], asks=[("0.50", 10)])
    assert summary(maker().on_market(ctx_for(book))) == [
        (BID, D("0.43"), D("2"), GTC, True),
        (ASK, D("0.47"), D("2"), GTC, True),
    ]


def test_market_maker_skews_with_inventory():
    book = make_book(TICKER, bids=[("0.40", 10)], asks=[("0.50", 10)])
    intents = maker().on_market(ctx_for(book, position=5))
    assert [(i.side, i.price) for i in intents] == [(BID, D("0.42")), (ASK, D("0.46"))]


def test_market_maker_never_crosses_the_book():
    book = make_book(TICKER, bids=[("0.40", 10)], asks=[("0.44", 10)])
    intents = maker(skew_per_contract=0.02).on_market(ctx_for(book, position=9))
    assert summary(intents) == [(BID, D("0.22"), D("1"), GTC, True), (ASK, D("0.41"), D("2"), GTC, True)]


def test_market_maker_ignores_its_own_quotes():
    book = make_book(TICKER, bids=[("0.43", 2), ("0.40", 10)], asks=[("0.50", 10)])
    own = [Order("o1", "kb-1", TICKER, BID, D("0.43"), D("2"))]
    intents = maker().on_market(ctx_for(book, own=own))
    assert [(i.side, i.price) for i in intents] == [(BID, D("0.43")), (ASK, D("0.47"))]


def test_market_maker_stays_out_of_tight_or_one_sided_books():
    assert maker().on_market(ctx_for(make_book(TICKER, bids=[("0.45", 5)], asks=[("0.47", 5)]))) == []
    assert maker().on_market(ctx_for(make_book(TICKER, bids=[("0.45", 5)]))) == []


def test_market_maker_stops_adding_at_max_position():
    book = make_book(TICKER, bids=[("0.40", 10)], asks=[("0.50", 10)])
    intents = maker(skew_per_contract=0).on_market(ctx_for(book, position=10))
    assert [i.side for i in intents] == [ASK]


# --- registro ----------------------------------------------------------------


def test_build_strategy(tmp_path, monkeypatch):
    assert isinstance(build_strategy("fair_value", {}), FairValueStrategy)
    with pytest.raises(ValueError, match="desconocida"):
        build_strategy("nope", {})

    (tmp_path / "mi_estrategia_test.py").write_text(
        "from kalshi_bot.strategies.base import Strategy\n"
        "class MiEstrategia(Strategy):\n"
        "    name = 'mia'\n"
        "    def on_market(self, ctx):\n"
        "        return []\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "path", list(sys.path))
    try:
        strategy = build_strategy("mi_estrategia_test:MiEstrategia", {"x": 1})
        assert strategy.name == "mia" and strategy.params == {"x": 1}
    finally:
        sys.modules.pop("mi_estrategia_test", None)
