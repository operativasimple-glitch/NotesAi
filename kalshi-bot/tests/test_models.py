from decimal import Decimal as D

from kalshi_bot.models import (
    ASK,
    BID,
    Balance,
    Order,
    OrderBook,
    Position,
    PriceRange,
    ceil_to_tick,
    floor_to_tick,
    snap_price,
)

from .fakes import make_book, make_market

SUBPENNY = (
    PriceRange(D("0.001"), D("0.100"), D("0.001")),
    PriceRange(D("0.100"), D("0.900"), D("0.010")),
    PriceRange(D("0.900"), D("0.999"), D("0.001")),
)


def test_snap_default_ticks():
    assert floor_to_tick(D("0.456")) == D("0.45")
    assert ceil_to_tick(D("0.451")) == D("0.46")
    assert snap_price(D("0.456"), BID) == D("0.45")
    assert snap_price(D("0.456"), ASK) == D("0.46")
    assert floor_to_tick(D("0.005")) is None  # por debajo del mínimo
    assert ceil_to_tick(D("0.995")) is None  # por encima del máximo
    assert floor_to_tick(D("1.5")) == D("0.99")


def test_snap_subpenny_ranges():
    assert floor_to_tick(D("0.0567"), SUBPENNY) == D("0.056")
    assert ceil_to_tick(D("0.0561"), SUBPENNY) == D("0.057")
    assert floor_to_tick(D("0.4567"), SUBPENNY) == D("0.45")
    assert ceil_to_tick(D("0.4501"), SUBPENNY) == D("0.46")
    assert ceil_to_tick(D("0.9001"), SUBPENNY) == D("0.901")
    assert floor_to_tick(D("0.10005"), SUBPENNY) == D("0.100")


def test_orderbook_from_api_converts_no_bids_to_yes_asks():
    payload = {
        "orderbook_fp": {
            # desordenado a propósito y con un nivel repetido
            "yes_dollars": [["0.4300", "10.00"], ["0.4500", "5.00"], ["0.4500", "2.00"]],
            "no_dollars": [["0.5000", "7.00"], ["0.5200", "3.00"]],
        }
    }
    book = OrderBook.from_api("T", payload)
    assert [(lvl.price, lvl.size) for lvl in book.bids] == [(D("0.45"), D("7")), (D("0.43"), D("10"))]
    # bid NO a 0.52 = ask YES a 0.48 (el mejor), bid NO a 0.50 = ask YES a 0.50
    assert [(lvl.price, lvl.size) for lvl in book.asks] == [(D("0.48"), D("3")), (D("0.50"), D("7"))]
    assert book.best_bid == D("0.45")
    assert book.best_ask == D("0.48")
    assert book.mid == D("0.465")
    assert book.spread == D("0.03")
    assert book.ask_size_up_to(D("0.49")) == D("3")
    assert book.bid_size_down_to(D("0.43")) == D("17")


def test_orderbook_empty_sides():
    book = OrderBook.from_api("T", {"orderbook_fp": {"yes_dollars": None, "no_dollars": []}})
    assert book.best_bid is None and book.best_ask is None and book.mid is None and book.spread is None


def test_orderbook_without_own_orders():
    book = make_book("T", bids=[("0.45", 7), ("0.43", 10)], asks=[("0.48", 3)])
    own = [Order("o1", "kb-1", "T", BID, D("0.45"), D("7")), Order("o2", "kb-2", "T", ASK, D("0.48"), D("1"))]
    ex = book.without_orders(own)
    assert ex.best_bid == D("0.43")  # nuestro bid era todo el nivel 0.45
    assert ex.asks[0].size == D("2")


def test_market_from_api():
    m = make_market(yes_bid_dollars="0.0000", yes_ask_dollars="1.0000")
    assert m.yes_bid is None and m.yes_ask is None  # sin bid / sin ask
    assert m.is_active
    assert m.volume_24h == D("1500")
    assert m.price_ranges[0].step == D("0.01")
    assert make_market(status="closed").is_active is False


def test_order_from_api_v2_and_legacy():
    v2 = Order.from_api(
        {
            "order_id": "o1",
            "client_order_id": "kb-abc",
            "ticker": "T",
            "book_side": "ask",
            "yes_price_dollars": "0.6100",
            "remaining_count_fp": "3.00",
            "fill_count_fp": "1.00",
            "status": "resting",
        }
    )
    assert (v2.side, v2.price, v2.remaining, v2.filled) == (ASK, D("0.61"), D("3"), D("1"))
    legacy = Order.from_api(
        {"order_id": "o2", "ticker": "T", "action": "sell", "side": "no", "yes_price": 40, "remaining_count": 2}
    )
    assert (legacy.side, legacy.price, legacy.remaining) == (BID, D("0.4"), D("2"))
    assert legacy.collateral() == D("0.8")
    assert v2.collateral() == D("1.17")  # vender YES a 0.61 bloquea 0.39 por contrato


def test_position_and_balance():
    p = Position.from_api(
        {"ticker": "T", "position_fp": "-4.00", "market_exposure_dollars": "2.4000", "realized_pnl_dollars": "1.5"}
    )
    assert p.position == D("-4") and p.exposure == D("2.4")
    b = Balance.from_api({"balance": 12345, "balance_dollars": "123.4500", "portfolio_value": 5000})
    assert b.cash == D("123.45") and b.portfolio_value == D("50") and b.equity == D("173.45")
    assert Balance.from_api({"balance": 250, "portfolio_value": 0}).cash == D("2.5")
