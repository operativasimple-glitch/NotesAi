from datetime import timedelta
from decimal import Decimal as D

from kalshi_bot.fees import estimate_fee, fee_per_contract
from kalshi_bot.models import ASK, BID, IOC, OrderIntent
from kalshi_bot.risk import RiskLimits, RiskManager

from .fakes import NOW, make_market


def test_fee_formula_rounds_up_to_the_cent():
    assert estimate_fee(D("0.50"), D("1"), D("0.07")) == D("0.02")  # 0.0175 -> 0.02
    assert estimate_fee(D("0.50"), D("100"), D("0.07")) == D("1.75")
    assert estimate_fee(D("0.10"), D("10"), D("0.07")) == D("0.07")  # 0.063 -> 0.07
    assert estimate_fee(D("0.50"), D("10"), D("0")) == D("0")
    assert fee_per_contract(D("0.50"), D("4"), D("0.07")) == D("0.0175")  # $0.07 repartidos en 4
    assert fee_per_contract(D("0.50"), D("0"), D("0.07")) == D("0")


def intent(side, price, count, tif="good_till_canceled"):
    return OrderIntent("T", side, D(price), D(count), tif)


def manager(**kw):
    return RiskManager(RiskLimits(**kw))


def test_price_band_and_order_size():
    rm = manager(max_order_contracts=D("5"), min_price=D("0.03"), max_price=D("0.97"))
    ok, notes = rm.filter_intents(
        [intent(BID, "0.98", 1), intent(ASK, "0.02", 1), intent(BID, "0.50", 9)],
        position=D(0),
        committed_exposure=D(0),
    )
    assert [(i.side, i.count) for i in ok] == [(BID, D(5))]
    assert len(notes) == 3  # dos rechazadas y una recortada


def test_position_limit_counts_all_orders_on_the_same_side():
    rm = manager(max_position_per_market=D("20"), max_order_contracts=D("10"))
    ok, _ = rm.filter_intents(
        [intent(BID, "0.40", 5), intent(BID, "0.39", 5), intent(ASK, "0.60", 10)],
        position=D(17),
        committed_exposure=D(0),
    )
    assert [(i.side, i.count) for i in ok] == [(BID, D(3)), (ASK, D(10))]


def test_exposure_budget_clips_new_risk():
    rm = manager(max_total_exposure=D("50"))
    ok, notes = rm.filter_intents([intent(BID, "0.50", 5)], position=D(0), committed_exposure=D("48"))
    assert ok[0].count == D(4)  # $2 de presupuesto / $0.50 por contrato
    ok, notes = rm.filter_intents([intent(ASK, "0.80", 5)], position=D(0), committed_exposure=D("49.9"))
    assert ok == [] and "exposición" in notes[0]  # vender YES a 0.80 cuesta 0.20 por contrato


def test_closing_orders_are_allowed_even_without_budget():
    rm = manager(max_total_exposure=D("50"), max_order_contracts=D("10"))
    ok, _ = rm.filter_intents([intent(ASK, "0.60", 8, IOC)], position=D(5), committed_exposure=D("80"))
    assert ok[0].count == D(5)  # cierra los 5 YES; no puede abrir NO sin presupuesto


def test_fractional_counts_are_floored():
    rm = manager()
    ok, _ = rm.filter_intents(
        [intent(BID, "0.50", "2.7"), intent(ASK, "0.5", "0.4")], position=D(0), committed_exposure=D(0)
    )
    assert [i.count for i in ok] == [D(2)]


def test_fractional_leftovers_can_still_be_sold():
    # Una venta a 99¢ que solo se llenó en parte deja 0,85 NO: se venden tal cual, aunque comprar
    # SÍ a 1¢ esté fuera de la banda de precios (antes se redondeaba a 0 y no se podía salir).
    ok, notes = manager().filter_intents(
        [intent(BID, "0.01", "0.85", IOC)], position=D("-0.85"), committed_exposure=D(0)
    )
    assert [i.count for i in ok] == [D("0.85")] and notes == []
    # Lo que abre posición nueva sigue yendo en contratos enteros.
    ok, _ = manager().filter_intents([intent(ASK, "0.50", "2.7")], position=D("0.4"), committed_exposure=D(0))
    assert [i.count for i in ok] == [D("2.4")]  # cierra 0,4 SÍ y abre 2 NO


def test_session_loss_breaker():
    rm = manager(max_session_loss=D("20"))
    assert rm.check_loss(D("100")) is None  # sin sesión iniciada
    rm.start_session(D("100"))
    assert rm.check_loss(D("85")) is None
    assert "pérdida" in rm.check_loss(D("80"))
    assert manager(max_session_loss=D("0")).check_loss(D("0")) is None


def test_market_block_reasons():
    rm = manager(min_minutes_to_close=15)
    assert rm.market_block_reason(make_market(), NOW) is None
    assert "no activo" in rm.market_block_reason(make_market(status="closed"), NOW)
    closing = make_market(close_time=(NOW + timedelta(minutes=10)).isoformat())
    assert "cierra en 10 min" in rm.market_block_reason(closing, NOW)


def test_closing_orders_skip_price_and_size_caps():
    rm = manager(max_order_contracts=D("5"), max_price=D("0.98"))
    sell_all = intent(ASK, "0.99", "12", IOC)
    ok, _ = rm.filter_intents([sell_all], position=D("12"), committed_exposure=D("0"))
    assert [i.count for i in ok] == [D("12")]  # cerrar: a 99¢ y de una vez
    ok, notes = rm.filter_intents([sell_all], position=D("0"), committed_exposure=D("0"))
    assert ok == [] and "fuera de" in notes[0]  # abrir a 99¢ sigue prohibido
    ok, _ = rm.filter_intents([sell_all], position=D("3"), committed_exposure=D("0"))
    assert [i.count for i in ok] == [D("3")]  # solo la parte que cierra
    ok, _ = rm.filter_intents([intent(ASK, "0.50", "12")], position=D("3"), committed_exposure=D("0"))
    assert [i.count for i in ok] == [D("5")]  # dentro de rango: 3 que cierran + 2 nuevos (máx. 5)


def test_caps_follow_the_balance():
    # Con 20 $: como mucho 3 $ en un mercado (15 %) y 12 $ en juego (60 %).
    rm = manager(max_order_contracts=D("10"), max_market_pct=D("0.15"), max_exposure_pct=D("0.6"))
    ok, notes = rm.filter_intents([intent(BID, "0.90", 10)], position=D(0), committed_exposure=D(0), equity=D("20"))
    assert ok[0].count == D(3)
    assert "recortada de 10.00 a 3.00 contratos (máximo en un mercado: 15 % del saldo, $3.00)" in notes[0]
    # Ya tiene 3 contratos en ese mercado: no suma más.
    ok, notes = rm.filter_intents(
        [intent(BID, "0.90", 10)], position=D(3), committed_exposure=D(0), equity=D("20"), market_cost=D("2.70")
    )
    assert ok == [] and "máximo en un mercado" in notes[0]
    # Con 11 $ ya en juego en otros mercados solo cabe 1 $ más.
    ok, notes = rm.filter_intents([intent(BID, "0.90", 10)], position=D(0), committed_exposure=D("11"), equity=D("20"))
    assert ok[0].count == D(1) and "máximo en juego: 60 % del saldo, $12.00" in notes[0]
    # Sin saldo conocido (o con los límites a 0) no cambia nada.
    ok, _ = rm.filter_intents([intent(BID, "0.90", 10)], position=D(0), committed_exposure=D(0))
    assert ok[0].count == D(10)


def test_closing_is_never_blocked_by_the_balance_caps():
    rm = manager(max_order_contracts=D("10"), max_market_pct=D("0.15"), max_exposure_pct=D("0.6"))
    ok, _ = rm.filter_intents(
        [intent(ASK, "0.60", 5, IOC)], position=D(5), committed_exposure=D("40"), equity=D("20"), market_cost=D("4.5")
    )
    assert ok[0].count == D(5)


def test_daily_loss_pause_lasts_until_the_next_day_and_survives_a_restart(tmp_path):
    day_file = tmp_path / "risk_day.json"
    rm = RiskManager(RiskLimits(max_daily_loss_pct=D("0.25")), day_file=day_file)
    today = NOW.date()
    assert rm.track_day(D("20"), today) is None  # empieza el día con 20 $
    assert rm.track_day(D("16"), today) is None  # -20 %: aún no
    assert rm.track_day(D("14.9"), today) is None  # -25,5 %, pero una sola vuelta (puede ser un mal dato)
    assert rm.track_day(D("16"), today) is None  # se recuperó: no era nada
    assert [rm.track_day(D("14.9"), today) for _ in range(2)] == [None, None]
    reason = rm.track_day(D("14.9"), today)  # tres vueltas seguidas por debajo: pausa
    assert reason and "pausa hasta mañana" in reason and "$20.00" in reason
    assert rm.track_day(D("19"), today) == reason  # aunque se recupere, sigue en pausa ese día
    # Un reinicio del bot (cada vez que se sube un cambio) no borra la pausa ni el punto de partida.
    again = RiskManager(RiskLimits(max_daily_loss_pct=D("0.25")), day_file=day_file)
    assert again.track_day(D("19"), today) == reason
    assert again.day_status()["paused"] is True and again.day_status()["start_equity"] == D("20")
    # Al día siguiente vuelve a comprar, contando desde el saldo de ese día.
    assert again.track_day(D("19"), today + timedelta(days=1)) is None
    assert again.day_status() == {
        "day": (today + timedelta(days=1)).isoformat(),
        "start_equity": D("19"),
        "paused": False,
        "max_daily_loss_pct": D("0.25"),
    }


def test_daily_pause_is_off_with_zero_and_ignores_a_broken_file(tmp_path):
    day_file = tmp_path / "risk_day.json"
    day_file.write_text("no es json")
    rm = RiskManager(RiskLimits(max_daily_loss_pct=D("0")), day_file=day_file)
    assert rm.track_day(D("20"), NOW.date()) is None
    assert rm.track_day(D("1"), NOW.date()) is None
