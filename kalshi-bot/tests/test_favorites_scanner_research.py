from datetime import timedelta
from decimal import Decimal as D

import pytest

from kalshi_bot.discovery import MarketFilter, discover
from kalshi_bot.models import ASK, BID, GTC
from kalshi_bot.research import Research, band_note, bucket_index, run_research, run_sweep, verdict, wilson
from kalshi_bot.risk import RiskLimits, RiskManager
from kalshi_bot.scanner import ScanParams, event_arbitrage, run_scan, scan_markets
from kalshi_bot.strategies.favorites import FavoritesStrategy

from .fakes import NOW, FakeKalshi, make_book, make_market, market_payload
from .test_strategies import ctx_for, summary

T = "KXTEST-26OCT08-B50"


# --- estrategia favoritos ---------------------------------------------------------


def fav(**params):
    return FavoritesStrategy(params)


def test_buys_yes_favorite_improving_one_tick():
    book = make_book(T, bids=[("0.90", 50)], asks=[("0.92", 50)])
    assert summary(fav().on_market(ctx_for(book))) == [(BID, D("0.91"), D("10"), GTC, True)]


def test_joins_the_bid_when_improving_would_cross():
    book = make_book(T, bids=[("0.91", 50)], asks=[("0.92", 50)])
    assert summary(fav().on_market(ctx_for(book)))[0][1] == D("0.91")
    assert summary(fav(improve=False).on_market(ctx_for(make_book(T, bids=[("0.90", 5)], asks=[("0.92", 5)]))))[0][
        1
    ] == D("0.90")


def test_buys_no_favorite_by_selling_yes():
    book = make_book(T, bids=[("0.05", 50)], asks=[("0.08", 50)])
    # NO cotiza a 92¢ de compra: el bot ofrece comprar NO a 93¢ = vender YES a 7¢.
    assert summary(fav().on_market(ctx_for(book))) == [(ASK, D("0.07"), D("10"), GTC, True)]


def test_never_pays_more_than_max_price():
    book = make_book(T, bids=[("0.97", 50)], asks=[("0.99", 50)])
    assert summary(fav().on_market(ctx_for(book)))[0][1] == D("0.97")
    book = make_book(T, bids=[("0.01", 50)], asks=[("0.03", 50)])
    assert summary(fav().on_market(ctx_for(book)))[0][1] == D("0.03")  # NO a 97¢ como mucho


def test_skips_wide_spreads_and_prices_outside_the_band():
    assert fav().on_market(ctx_for(make_book(T, bids=[("0.90", 5)], asks=[("0.96", 5)]))) == []
    assert fav().on_market(ctx_for(make_book(T, bids=[("0.50", 5)], asks=[("0.52", 5)]))) == []
    assert fav().on_market(ctx_for(make_book(T, bids=[("0.98", 5)], asks=[("0.99", 5)]))) == []
    assert fav().on_market(ctx_for(make_book(T, bids=[("0.90", 5)]))) == []


def test_respects_max_position_on_each_side():
    yes_book = make_book(T, bids=[("0.90", 5)], asks=[("0.92", 5)])
    assert fav().on_market(ctx_for(yes_book, position=20)) == []
    assert fav().on_market(ctx_for(yes_book, position=15))[0].count == D("5")
    no_book = make_book(T, bids=[("0.05", 5)], asks=[("0.08", 5)])
    assert fav().on_market(ctx_for(no_book, position=-20)) == []


def test_invalid_band():
    with pytest.raises(ValueError):
        fav(min_price=0.4)
    with pytest.raises(ValueError):
        fav(min_price=0.95, max_price=0.9)


# --- búsqueda de mercados ----------------------------------------------------------


def closing_in(ticker, hours, volume="500", event=None):
    return make_market(
        ticker,
        close_time=(NOW + timedelta(hours=hours)).isoformat(),
        volume_24h_fp=volume,
        event_ticker=event or ticker.rsplit("-", 1)[0],
    )


def test_discover_markets_closing_soon_one_per_event():
    fake = FakeKalshi(
        markets=[
            closing_in("KXA-EV1-X", 10, "900", "KXA-EV1"),
            closing_in("KXA-EV1-Y", 12, "800", "KXA-EV1"),
            closing_in("KXB-EV2-X", 30, "100", "KXB-EV2"),
            closing_in("KXC-EV3-X", 60, "999", "KXC-EV3"),
            closing_in("KXD-EV4-X", 5, "700", "KXD-EV4"),
        ]
    )
    # Un partido: el cierre oficial es dentro de 70 h, pero termina (fin previsto) dentro de 3 h.
    game = closing_in("KXGAME-EV5-X", 70, "600", "KXGAME-EV5")
    game.expected_end = NOW + timedelta(hours=3)
    fake.markets[game.ticker] = game
    flt = MarketFilter(closing_within_hours=48, max_markets=10, max_markets_per_event=1, exclude_series=["KXD"])
    found = discover(fake, flt, NOW)
    assert [m.ticker for m in found] == ["KXA-EV1-X", "KXGAME-EV5-X", "KXB-EV2-X"]  # por volumen; KXC (60 h) fuera
    query = fake.market_queries[-1]
    # Se pide a la API un margen de 72 h para no perder partidos con cierre oficial tardío.
    assert query["status"] is None and query["max_close_ts"] == int((NOW + timedelta(hours=48 + 72)).timestamp())


def test_series_rules_give_weather_its_own_window_and_markets_per_event():
    fake = FakeKalshi(
        markets=[closing_in(f"KXHIGHNY-26OCT08-B{i}", 30, str(900 - i), "KXHIGHNY-26OCT08") for i in range(6)]
        + [
            closing_in("KXNFLGAME-26OCT08-DAL", 30, "5000", "KXNFLGAME-26OCT08"),  # termina en 30 h: aún no
            closing_in("KXNFLGAME-26OCT07-KC", 3, "4000", "KXNFLGAME-26OCT07"),
            closing_in("KXNFLGAME-26OCT07-BUF", 3, "3000", "KXNFLGAME-26OCT07"),  # mismo partido
        ]
    )
    flt = MarketFilter(
        series=["KXHIGHNY", "KXNFLGAME"],
        max_hours_to_close=6,
        max_markets=20,
        max_markets_per_event=1,
        series_rules={"KXHIGH": {"max_hours_to_close": 40, "max_markets_per_event": 4}},
    )
    found = [m.ticker for m in discover(fake, flt, NOW)]
    assert found == ["KXNFLGAME-26OCT07-KC"] + [f"KXHIGHNY-26OCT08-B{i}" for i in range(4)]
    assert flt.rule("KXHIGHNY", "max_hours_to_close") == 40 and flt.rule("KXNFLGAME", "max_hours_to_close") == 6


def test_markets_end_at_the_expected_end_when_it_comes_first():
    game = make_market(
        "KXMLBGAME-26OCT07-CWS",
        close_time=(NOW + timedelta(hours=75)).isoformat(),
        expected_expiration_time=(NOW + timedelta(hours=6)).isoformat(),
    )
    assert game.expected_end == NOW + timedelta(hours=6) and game.ends_at == game.expected_end
    assert round(game.hours_to_close(NOW), 2) == 6.0
    plain = make_market("KXOTHER-1", close_time=(NOW + timedelta(hours=2)).isoformat())
    assert plain.expected_end is None and plain.hours_to_close(NOW) == 2.0
    # El riesgo deja de operar 15 min antes del final previsto, no del cierre oficial.
    risk = RiskManager(RiskLimits())
    assert risk.market_block_reason(game, NOW + timedelta(hours=5, minutes=50)) is not None
    assert risk.market_block_reason(game, NOW + timedelta(hours=5)) is None


# --- escáner -------------------------------------------------------------------------


def test_scan_markets_finds_favorites_and_wide_spreads():
    markets = [
        make_market("FAV-YES", yes_bid_dollars="0.9100", yes_ask_dollars="0.9300"),
        make_market("FAV-NO", yes_bid_dollars="0.0500", yes_ask_dollars="0.0700"),
        make_market("WIDE", yes_bid_dollars="0.3000", yes_ask_dollars="0.4000"),
        make_market("THIN", yes_bid_dollars="0.9100", yes_ask_dollars="0.9300", volume_24h_fp="5"),
    ]
    result = scan_markets(markets, ScanParams(), NOW)
    assert [(f["ticker"], f["side"], f["bid"]) for f in result["favorites"]] == [
        ("FAV-YES", "yes", D("0.91")),
        ("FAV-NO", "no", D("0.93")),
    ]
    assert [s["ticker"] for s in result["spreads"]] == ["WIDE"]


def nested_event(ticker, bids_asks, mutually_exclusive=True):
    return {
        "event_ticker": ticker,
        "series_ticker": ticker.split("-")[0],
        "title": "Evento",
        "mutually_exclusive": mutually_exclusive,
        "markets": [
            market_payload(f"{ticker}-{i}", yes_bid_dollars=b, yes_ask_dollars=a) for i, (b, a) in enumerate(bids_asks)
        ],
    }


def test_event_arbitrage_buy_no_on_all_outcomes():
    events = [
        nested_event("KXARB-1", [("0.40", "0.45"), ("0.35", "0.40"), ("0.33", "0.38")]),
        nested_event("KXARB-2", [("0.40", "0.45"), ("0.35", "0.40"), ("0.33", "0.38")], mutually_exclusive=False),
        nested_event("KXNOARB-3", [("0.40", "0.42"), ("0.31", "0.33"), ("0.30", "0.32")]),
    ]
    found = event_arbitrage(events)
    assert [(a["event_ticker"], a["kind"]) for a in found] == [("KXARB-1", "buy_no_all")]
    # Suma de bids YES = 1.08; NO cuesta 1.92 y paga al menos 2; comisiones ~4.9¢.
    assert found[0]["profit"] == D("0.031")


def test_event_arbitrage_buy_yes_on_all_has_a_warning():
    found = event_arbitrage([nested_event("KXCHEAP-1", [("0.25", "0.30"), ("0.25", "0.30"), ("0.25", "0.30")])])
    assert [a["kind"] for a in found] == ["buy_yes_all"]
    assert found[0]["profit"] == D("0.055") and found[0]["warning"]


def test_run_scan_with_fake_client():
    fake = FakeKalshi(markets=[closing_in("KXA-EV1-X", 10, "900")])
    fake.markets["KXA-EV1-X"] = make_market(
        "KXA-EV1-X", yes_bid_dollars="0.9000", yes_ask_dollars="0.9200", volume_24h_fp="900"
    )
    fake.events = [nested_event("KXARB-1", [("0.40", "0.45"), ("0.35", "0.40"), ("0.33", "0.38")])]
    result = run_scan(fake, ScanParams(), NOW)
    assert result["markets_scanned"] == 1
    assert [f["ticker"] for f in result["favorites"]] == ["KXA-EV1-X"]
    assert [a["event_ticker"] for a in result["arbitrage"]] == ["KXARB-1"]


# --- investigación -------------------------------------------------------------------


def trade(price, count, taker, block=False):
    return {
        "yes_price_dollars": price,
        "count_fp": str(count),
        "taker_outcome_side": taker,
        "is_block_trade": block,
    }


def test_bucket_index():
    assert bucket_index(D("0.03")) == 0
    assert bucket_index(D("0.05")) == 1
    assert bucket_index(D("0.95")) == 9
    assert bucket_index(D("0.999")) == 9


def test_research_tallies_returns_by_role_and_price():
    r = Research()
    # Mercado que salió NO: el taker compró YES a 5¢ (longshot) y el maker se quedó NO a 95¢.
    r.add_market("no", [trade("0.0500", 10, "yes"), trade("0.0500", 99, "yes", block=True), {"bad": 1}])
    # Mercado que salió YES: el taker compró NO a 8¢ y el maker tiene YES a 92¢.
    r.add_market("yes", [trade("0.9200", 10, "no")])
    r.add_market("scalar", [trade("0.5000", 10, "yes")])  # se ignora
    report = r.report()
    assert report["markets"] == 2 and report["trades"] == 2
    rows = {row["range"]: row for row in report["buckets"]}
    assert rows["5–10¢"]["taker"]["return"] == D("-1.0000")  # longshots perdieron todo
    assert rows["95–100¢"]["maker"]["win_rate"] == D("1.0000")
    assert rows["95–100¢"]["maker"]["return"] == D("0.0526")  # 10 / 9.5 - 1
    assert rows["90–95¢"]["maker"]["return"] == D("0.0870")  # 10 / 9.2 - 1
    assert any("favoritos tiene base" in note for note in report["conclusions"])


def test_run_research_with_fake_client():
    fake = FakeKalshi()
    settled = make_market("KXOLD-1", status="finalized", result="no")
    fake.settled = {settled.ticker: settled}
    fake.trades = {"KXOLD-1": [trade("0.0400", 20, "yes"), trade("0.0600", 10, "yes")]}
    calls = []
    report = run_research(fake, series="KXOLD", progress=lambda done, total: calls.append((done, total)))
    assert report["markets"] == 1 and report["trades"] == 2 and calls == [(1, 1)]
    assert "Pocos mercados" in report["conclusions"][-1]


def test_run_research_over_several_series():
    fake = FakeKalshi()
    for series, result in (("KXAAA", "no"), ("KXBBB", "yes")):
        for i in range(3):
            m = make_market(f"{series}-{i}", status="finalized", result=result, event_ticker=f"{series}-E{i}")
            fake.settled[m.ticker] = m
            fake.trades[m.ticker] = [trade("0.0600", 10, "yes")]  # maker con NO a 94¢
    report = run_research(fake, series="kxaaa, KXBBB", max_markets=2)
    assert report["series"] == "KXAAA, KXBBB" and report["markets"] == 4  # 2 por serie
    by = report["by_series"]
    assert by["KXAAA"]["return_after_fees"] > 0 > by["KXBBB"]["return_after_fees"]
    assert report["strategy"]["groups"] == 4 and report["strategy"]["losing_groups"] == 2


def test_research_by_time_splits_trades_by_minutes_before_close():
    fake = FakeKalshi()
    for i in range(4):
        m = make_market(f"KXGAME-{i}", status="finalized", result="no", event_ticker=f"KXGAME-E{i}")
        fake.settled[m.ticker] = m
        close = m.close_time
        early = trade("0.0600", 10, "yes") | {"created_time": (close - timedelta(hours=5)).isoformat()}
        late = trade("0.0400", 10, "yes") | {"created_time": (close - timedelta(minutes=20)).isoformat()}
        fake.trades[m.ticker] = [early, late]
    report = run_research(fake, series="KXGAME", skip_last_minutes=15, by_time=True)
    windows = {row["window"]: row for row in report["by_time"]}
    assert list(windows) == ["15 min–30 min antes", "30 min–1 h antes", "1 h–3 h antes", "más de 3 h antes"]
    assert windows["más de 3 h antes"]["avg_price"] == D("0.94") and windows["más de 3 h antes"]["groups"] == 4
    assert windows["15 min–30 min antes"]["avg_price"] == D("0.96")
    assert windows["30 min–1 h antes"]["contracts"] == 0


# --- barrido de series ------------------------------------------------------------


def test_research_counts_favorite_markets_and_upsets():
    r = Research()
    r.add_market("no", [trade("0.0500", 10, "yes")])  # maker NO a 95¢: el favorito ganó
    r.add_market("yes", [trade("0.0400", 10, "yes")])  # maker NO a 96¢: el favorito perdió
    r.add_market("yes", [trade("0.5000", 10, "yes")])  # sin favoritos
    assert (r.favorite_markets, r.favorite_upsets) == (2, 1)


def sweep_fixture():
    fake = FakeKalshi(
        markets=[
            closing_in("KXGOOD-LIVE-1", 10, "5000", "KXGOOD-LIVE"),
            closing_in("KXBAD-LIVE-1", 10, "3000", "KXBAD-LIVE"),
            closing_in("KXTINY-LIVE-1", 10, "100", "KXTINY-LIVE"),
        ]
    )
    for i in range(12):
        good = make_market(f"KXGOOD-{i}", status="finalized", result="no", event_ticker=f"KXGOOD-E{i}")
        bad = make_market(f"KXBAD-{i}", status="finalized", result="yes" if i % 2 else "no", event_ticker=f"KXBAD-E{i}")
        fake.settled[good.ticker] = good
        fake.settled[bad.ticker] = bad
        # En ambas series un taker compra el longshot SÍ a 6¢ y un maker queda con NO a 94¢.
        fake.trades[good.ticker] = [trade("0.0600", 10, "yes")]
        fake.trades[bad.ticker] = [trade("0.0600", 10, "yes")]
    fake.settled["KXTINY-0"] = make_market("KXTINY-0", status="finalized", result="no")  # muy pocos mercados
    fake.series_info = {"KXGOOD": {"title": "Serie buena", "category": "Clima"}}
    return fake


def test_sweep_ranks_series_by_favorite_returns():
    fake = sweep_fixture()
    calls, trade_requests = [], []
    get_trades = fake.get_trades

    def recording_get_trades(ticker, **kwargs):
        trade_requests.append((ticker, kwargs.get("max_ts")))
        return get_trades(ticker, **kwargs)

    fake.get_trades = recording_get_trades
    report = run_sweep(fake, now=NOW, progress=lambda d, t: calls.append((d, t)), min_markets=10)
    assert [row["series"] for row in report["rows"]] == ["KXGOOD", "KXBAD"]  # KXTINY no llega al mínimo
    good, bad = report["rows"]
    assert good["title"] == "Serie buena" and good["category"] == "Clima"
    assert good["favorite_markets"] == 12 and good["favorite_upsets"] == 0
    assert good["favorites_maker"]["return_after_fees"] > 0 > bad["favorites_maker"]["return_after_fees"]
    assert bad["favorite_upsets"] == 6
    assert good["confidence"] == "baja"
    # La banda del bot (88–97¢): KXBAD pierde con claridad; KXGOOD no tuvo fallos, pero
    # 12 eventos son pocos para asegurar que gana.
    assert (good["verdict"], bad["verdict"]) == ("dudoso", "pierde")
    assert good["strategy"]["groups"] == 12 and bad["strategy"]["losing_groups"] == 6
    assert report["overall"]["groups"] == 24 and report["overall"]["return_after_fees"] < 0
    assert "series juntas" in report["conclusions"][0] and any("KXGOOD" in n for n in report["conclusions"])
    # Sin los últimos 15 minutos antes del cierre, como el bot.
    market = fake.settled["KXGOOD-0"]
    assert (market.ticker, int(market.close_time.timestamp()) - 900) in trade_requests
    assert calls[-1] == (24, 24)


def test_band_margin_of_error_counts_events_not_contracts():
    r = Research()
    # 30 eventos: un maker compra NO a 94¢; el favorito gana en 29 y pierde en 1.
    for i in range(30):
        r.add_market("yes" if i == 0 else "no", [trade("0.0600", 10, "yes")], group=f"E{i}")
    s = r.band_summary()
    assert (s["groups"], s["losing_groups"], s["avg_price"], s["win_rate"]) == (30, 1, D("0.94"), D("0.9667"))
    assert s["ci_low"] < 0 < s["return_after_fees"] < s["ci_high"]
    assert verdict(s) == "dudoso"  # un batacazo en 30 eventos no permite asegurar nada
    assert "no se puede asegurar" in band_note(s)

    same_event = Research()
    for _ in range(5):
        same_event.add_market("no", [trade("0.0600", 10, "yes")], group="MISMO")
    assert same_event.band_summary()["groups"] == 1 and verdict(same_event.band_summary()) == "sin datos"

    outside = Research()
    outside.add_market("no", [trade("0.0200", 10, "yes")])  # NO a 98¢: fuera de la banda del bot
    assert outside.band_summary()["contracts"] == 0

    # Sin ningún fallo el margen no se reduce a cero; con muchos eventos, sí se puede asegurar.
    few = Research()
    for i in range(20):
        few.add_market("no", [trade("0.0600", 10, "yes")], group=f"E{i}")
    assert few.band_summary()["ci_low"] < 0 and verdict(few.band_summary()) == "dudoso"
    many = Research()
    for i in range(400):
        many.add_market("yes" if i < 2 else "no", [trade("0.0600", 10, "yes")], group=f"E{i}")
    assert verdict(many.band_summary()) == "gana"


def test_margin_uses_delta_method_when_there_are_enough_losses():
    r = Research()
    # 200 eventos a 94¢ con 8 batacazos: hay fallos de sobra para la aproximación normal.
    for i in range(200):
        r.add_market("yes" if i < 8 else "no", [trade("0.0600", 10, "yes")], group=f"E{i}")
    s = r.band_summary()
    assert s["margin_method"] == "delta" and s["losing_groups"] == 8
    assert s["ci_low"] < s["return_after_fees"] < s["ci_high"]
    few = Research()
    for i in range(30):
        few.add_market("yes" if i == 0 else "no", [trade("0.0600", 10, "yes")], group=f"E{i}")
    assert few.band_summary()["margin_method"] == "delta+wilson"  # 1 fallo: también Wilson
    dump = few.band_groups_dump()
    assert dump["band"] == ["0.88", "0.97"] and len(dump["groups"]) == 30
    assert run_research(FakeKalshi(), series="KXNADA", keep_groups=True)["groups_dump"]["groups"] == {}
    assert "groups_dump" not in run_research(FakeKalshi(), series="KXNADA")


def test_wilson_interval():
    low, high = wilson(12, 12)
    assert 0.75 < low < 0.76 and high == 1.0
    low, high = wilson(0, 10)
    assert low == 0.0 and 0.27 < high < 0.28
    assert wilson(0, 0) == (0.0, 1.0)
