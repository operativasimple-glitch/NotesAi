from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

from kalshi_bot.models import ASK, BID, Fill, Settlement
from kalshi_bot.results import EXPECTED_RETURN, build_results, category_of, close_markets, summarize

from .fakes import FakeKalshi, make_market

NOW = datetime(2026, 10, 7, 15, 0, tzinfo=timezone.utc)


def fill(ticker, side, price, count, hours_ago, fee="0.01"):
    return Fill(ticker, side, D(price), D(count), D(fee), NOW - timedelta(hours=hours_ago))


def settlement(ticker, result, yes=0, no=0, cost="0", revenue_cents=0, fee="0.01", hours_ago=1):
    return Settlement.from_api(
        {
            "ticker": ticker,
            "event_ticker": ticker.rsplit("-", 1)[0],
            "market_result": result,
            "yes_count_fp": f"{yes}.00",
            "no_count_fp": f"{no}.00",
            "yes_total_cost_dollars": cost if yes else "0",
            "no_total_cost_dollars": cost if no else "0",
            "revenue": revenue_cents,
            "fee_cost": fee,
            "settled_time": (NOW - timedelta(hours=hours_ago)).isoformat(),
        }
    )


def test_fill_and_settlement_from_api():
    f = Fill.from_api(
        {"ticker": "KXA-1", "book_side": "ask", "yes_price_dollars": "0.0600", "count_fp": "5.00", "fee_cost": "0.01"}
    )
    assert (f.side, f.price, f.count, f.fee) == (ASK, D("0.06"), D("5"), D("0.01"))
    legacy = Fill.from_api({"market_ticker": "KXA-1", "action": "sell", "side": "no", "yes_price": 94, "count": 3})
    assert (legacy.ticker, legacy.side, legacy.price, legacy.count) == ("KXA-1", BID, D("0.94"), D("3"))
    assert Fill.from_api({"ticker": "KXA-1", "yes_price_dollars": "0.5", "count_fp": "1"}) is None  # sin lado
    s = Settlement.from_api(
        {
            "ticker": "KXA-1",
            "market_result": "no",
            "no_count_fp": "5.00",
            "no_total_cost": 470,
            "revenue": 500,
            "fee_cost": "0.02",
            "settled_time": "2026-10-07T03:00:00Z",
        }
    )
    assert (s.position, s.cost, s.payout, s.fees, s.yes_payout()) == (D("-5"), D("4.70"), D("5"), D("0.02"), 0)
    assert Settlement.from_api({"ticker": "KXB-1", "market_result": "scalar", "value": 37}).yes_payout() == D("0.37")


def test_close_markets_follows_the_money_in_each_market():
    fills = [
        fill("KXMLBGAME-A-LAD", BID, "0.92", 5, 5),  # compra YES a 92¢ y gana
        fill("KXHIGHNY-B-T84", ASK, "0.06", 5, 20),  # compra NO a 94¢ y gana
        fill("KXNFLGAME-C-KC", BID, "0.93", 5, 6),  # compra YES a 93¢...
        fill("KXNFLGAME-C-KC", ASK, "0.45", 5, 4),  # ...y la vende antes a 45¢
        fill("KXNHLGAME-D-BOS", BID, "0.90", 5, 2),  # sigue abierta
    ]
    settlements = [
        settlement("KXMLBGAME-A-LAD", "yes", yes=5, cost="4.60", revenue_cents=500, hours_ago=1),
        settlement("KXHIGHNY-B-T84", "no", no=5, cost="4.70", revenue_cents=500, hours_ago=10),
        # Comprado antes del periodo descargado: no hay llenados, se usa la liquidación tal cual.
        settlement("KXHIGHMIA-E-T90", "yes", no=4, cost="3.80", revenue_cents=0, fee="0.01", hours_ago=30),
    ]
    closed, still_open = close_markets(fills, settlements)
    by_ticker = {m.ticker: m for m in closed}
    assert still_open == {"KXNHLGAME-D-BOS"}
    game = by_ticker["KXMLBGAME-A-LAD"]
    assert (game.side, game.cost, game.payout, game.net) == ("yes", D("4.60"), D("5"), D("0.39"))
    assert not game.sold_early
    weather = by_ticker["KXHIGHNY-B-T84"]
    assert (weather.side, weather.cost, weather.payout, weather.net) == ("no", D("4.70"), D("5"), D("0.29"))
    sold = by_ticker["KXNFLGAME-C-KC"]
    assert (sold.sold_early, sold.cost, sold.payout, sold.net) == (True, D("4.65"), D("2.25"), D("-2.42"))
    assert sold.closed_at == NOW - timedelta(hours=4)
    old = by_ticker["KXHIGHMIA-E-T90"]
    assert (old.side, old.contracts, old.net) == ("no", D("4"), D("-3.81"))
    assert [m.ticker for m in closed][0] == "KXMLBGAME-A-LAD"  # lo más reciente primero


def test_summarize_groups_by_local_day_and_market_type():
    closed, _ = close_markets(
        [fill("KXMLBGAME-A-LAD", BID, "0.92", 5, 5), fill("KXHIGHNY-B-T84", ASK, "0.06", 5, 20)],
        [
            settlement("KXMLBGAME-A-LAD", "yes", yes=5, cost="4.60", revenue_cents=500, hours_ago=1),
            # 03:00 UTC del día 7 = 22:00 del día 6 en Missouri (UTC−5).
            settlement("KXHIGHNY-B-T84", "no", no=5, cost="4.70", revenue_cents=500, hours_ago=12),
            settlement("KXHIGHMIA-E-T90", "yes", no=4, cost="3.80", revenue_cents=0, hours_ago=24 * 10),
        ],
    )
    positions = FakeKalshi()
    positions.set_position("KXNHLGAME-D-BOS", 5, exposure="4.50")
    report = summarize(closed, now=NOW, days=7, tz_offset_minutes=300, open_positions=positions.positions)
    assert report["today"] == "2026-10-07" and report["first_day"] == "2026-10-01"
    assert [d["date"] for d in report["by_day"]][-2:] == ["2026-10-06", "2026-10-07"] and len(report["by_day"]) == 7
    days = {d["date"]: d for d in report["by_day"]}
    assert days["2026-10-07"]["net"] == D("0.39") and days["2026-10-06"]["net"] == D("0.29")
    assert days["2026-10-05"] == {"date": "2026-10-05", "net": D("0"), "markets": 0, "wins": 0, "losses": 0}
    totals = report["totals"]  # el de hace 10 días queda fuera del periodo
    assert (totals["markets"], totals["wins"], totals["losses"], totals["net"]) == (2, 2, 0, D("0.68"))
    assert totals["return"] == D("0.0731") and totals["cost"] == D("9.30") and totals["fees"] == D("0.02")
    assert report["today_totals"]["net"] == D("0.39") and report["yesterday_totals"]["net"] == D("0.29")
    assert [c["key"] for c in report["by_category"]] == ["partidos", "clima"]
    assert report["recent"][0]["category"] == "partidos" and report["recent"][1]["side"] == "no"
    assert report["open"] == {"markets": 1, "exposure": D("4.50")}
    assert report["expected_return"] == EXPECTED_RETURN
    empty = summarize([], now=NOW, days=1)
    assert empty["totals"]["return"] is None and empty["yesterday_totals"] is None and len(empty["by_day"]) == 1


def test_category_of():
    assert [category_of(t) for t in ("KXNBAGAME-X", "KXHIGHDEN-X", "KXINXU-X", "KXFED-X")] == [
        "partidos",
        "clima",
        "bolsa",
        "otros",
    ]


def test_build_results_adds_market_titles():
    fake = FakeKalshi(markets=[make_market("KXMLBGAME-A-LAD", title="Dodgers vs Padres", yes_sub_title="Dodgers")])
    fake.fill_history = [fill("KXMLBGAME-A-LAD", BID, "0.92", 5, 5)]
    fake.settlements = [settlement("KXMLBGAME-A-LAD", "yes", yes=5, cost="4.60", revenue_cents=500, hours_ago=1)]
    report = build_results(fake, now=NOW, days=30)
    assert report["recent"][0]["title"] == "Dodgers vs Padres" and report["recent"][0]["subtitle"] == "Dodgers"

    def broken(**kwargs):
        raise RuntimeError("caída")

    fake.get_markets = broken  # sin títulos se ve el ticker
    assert build_results(fake, now=NOW, days=30)["recent"][0]["title"] == "KXMLBGAME-A-LAD"
