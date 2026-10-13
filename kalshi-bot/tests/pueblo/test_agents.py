from datetime import datetime, timezone
from decimal import Decimal as D

from pueblo.agents.cronista import Cronista
from pueblo.agents.kali import Kali, fill_words
from pueblo.agents.nube import Nube, bracket, judge
from pueblo.agents.radar import Radar
from pueblo.agents.vigia import Vigia
from pueblo.world import World

from .fakes import Clock, FakeKalshiPublic, FakeNWS, FakePanel, market, position

NY = "KXHIGHNY-26OCT08-B72.5"
PIT = "KXNHLGAME-26OCT08PITCBJ-PIT"


def texts(world, **filters):
    return [m["text"] for m in world.since() if all(m.get(k) == v for k, v in filters.items())]


def setup_kali(clock=None):
    clock = clock or Clock()
    world = World(None, clock)
    panel = FakePanel()
    return world, panel, Kali(world, panel, clock=clock), clock


# --- Kali -------------------------------------------------------------------------------


def test_fill_lines_become_plain_words():
    assert fill_words("LLENADO COMPRA YES 5.00 @ 0.9200 T-1 (maker, comisión $0.0200)")[:4] == (
        "compra",
        5,
        "SÍ",
        D("0.92"),
    )
    buy_no = fill_words("LLENADO VENDE YES 5.00 @ 0.1000 T-1 (maker, comisión $0.0200)")
    assert buy_no[2:4] == ("NO", D("0.90"))
    sold_no = fill_words("LLENADO (salida) COMPRA YES 5.00 @ 0.0100 T-1 (taker, comisión $0.0100)")
    assert sold_no[:4] == ("cobro", 5, "NO", D("0.99"))
    assert fill_words("LLENADO (fuera del bot) COMPRA YES 1.00 @ 0.5000 T-1 (taker, comisión $0.01)") is None


def test_kali_tells_the_town_what_the_real_bot_does():
    world, panel, kali, clock = setup_kali()
    panel.log("Arrancando bot | entorno=prod")  # lo viejo no se repite al conectar
    panel.position_rows = [position(PIT, "no", 5, "4.46", "0.47", "Pittsburgh vs Columbus Winner?", "Pittsburgh")]
    kali.run_if_due(clock())
    bot = world.bot("kali")
    assert bot["status"] == "Trabajando · 2 mercados · hoy +$4,45" and bot["mood"] == "happy"
    assert bot["detail"]["positions"][0]["name"] == "Gana Pittsburgh" and bot["detail"]["positions"][0]["side"] == "NO"
    assert texts(world) == []

    panel.labels_map[NY] = {"title": "Highest temperature in NYC?", "subtitle": "72° to 73°"}
    panel.log(f"LLENADO VENDE YES 5.00 @ 0.1000 {NY} (maker, comisión $0.0200)", minute=5)
    panel.log(f"LLENADO (salida) VENDE YES 5.00 @ 0.9900 {NY} (taker, comisión $0.0100)", minute=6)
    panel.result_rows = [
        {
            "ticker": NY,
            "title": "Highest temperature in NYC?",
            "subtitle": "72° to 73°",
            "net": "0.28",
            "closed_at": "2026-10-08T21:00:00+00:00",
            "sold_early": True,
        }
    ]
    clock.advance(300)
    kali.run_if_due(clock())
    assert texts(world) == [
        "He comprado 5 NO a 90¢ · Máxima en Nueva York · 72° a 73°",
        "He cobrado 5 SÍ a 99¢ · Máxima en Nueva York · 72° a 73°",
        "Cobrado antes: +$0,28 · Máxima en Nueva York · 72° a 73°",
    ]

    # El panel se reinicia: vuelve a numerar y recarga líneas viejas. Solo cuenta lo nuevo.
    old = [dict(line) for line in panel.log_lines]
    panel.log_lines = []
    for i, line in enumerate(old, start=1):
        panel.log_lines.append({**line, "id": i, "ts": line["ts"].replace("+00:00", ".123000+00:00")})
    panel.log("FRENO DE EMERGENCIA: pérdida de la sesión $10.20 >= límite $10", minute=9)
    panel.state = "halted"
    clock.advance(30)
    kali.run_if_due(clock())
    assert texts(world, to="tu") == [
        "Me he frenado: freno de emergencia.",
        "¡Freno de emergencia! pérdida de la sesión $10.20 >= límite $10",
    ]
    assert world.bot("kali")["mood"] == "sick"


def test_kali_without_the_panel_says_so_once():
    world = World(None, Clock())
    kali = Kali(world, None)
    kali.run_if_due(1)
    kali.next_run = 0
    kali.run_if_due(100)
    assert len(texts(world, to="tu")) == 1 and world.bot("kali")["mood"] == "sick"
    assert "PANEL_URL" in kali.talk()


# --- Nube -------------------------------------------------------------------------------


def test_brackets_and_how_a_temperature_bet_is_going():
    assert bracket("72° to 73°") == ("between", 72, 73)
    assert bracket("82° or below") == ("below", 82) and bracket("81° o más") == ("above", 81)
    assert bracket("", "KXHIGHNY-26OCT08-B72.5") == ("between", 72, 73)
    between = ("between", 72, 73)
    assert judge(between, "SÍ", 74, 75) == "perdida" and judge(between, "NO", 74, 75) == "ganada"
    assert judge(between, "SÍ", 72, 75) == "peligro" and judge(between, "SÍ", 72, 73) == "bien"
    assert judge(between, "NO", 70, 72) == "peligro"
    assert judge(("below", 82), "SÍ", 83, 80) == "perdida" and judge(("below", 82), "SÍ", 79, 84) == "peligro"
    assert judge(("above", 81), "NO", 81, 80) == "perdida" and judge(("above", 81), "NO", 78, 79) == "bien"


def test_nube_warns_vigia_when_a_weather_bet_of_kali_is_in_trouble():
    clock = Clock()
    world = World(None, clock)
    nws = FakeNWS()
    nube = Nube(world, nws, clock=clock)
    ny_day = "2026-10-08"
    nws.highs["NY"] = {ny_day: 73}
    nws.obs["NY"] = {"day": ny_day, "max_f": 72, "last_f": 71, "last_at": "2026-10-08T22:00:00+00:00"}
    world.update(
        "kali",
        detail={
            "positions": [
                {
                    "ticker": NY,
                    "name": "Máxima en Nueva York · 72° a 73°",
                    "side": "SÍ",
                    "subtitle": "72° to 73°",
                    "cost": "4.85",
                }
            ]
        },
    )
    nube.run_if_due(clock())
    rows = {r["key"]: r for r in world.bot("nube")["detail"]["cities"]}
    assert rows["NY"]["today"] == 73 and rows["NY"]["max_so_far"] == 72
    assert texts(world) == []  # va bien: no hay nada que decir

    # Sube la previsión: se lo dice a Kali y la apuesta pasa a peligrar.
    nws.highs["NY"] = {ny_day: 75}
    clock.advance(3700)
    nube.run_if_due(clock())
    assert texts(world, to="kali") == ["En Nueva York la máxima prevista para hoy sube de 73° a 75°."]
    assert texts(world, to="vigia") == [
        "Ojo: Kali tiene el SÍ a «72° a 73°» de Nueva York; van 72° y la previsión dice 75°. Peligra."
    ]
    # Se miden 74°: ya está perdida (solo se avisa del cambio).
    nws.obs["NY"]["max_f"] = 74
    clock.advance(900)
    nube.run_if_due(clock())
    clock.advance(900)
    nube.run_if_due(clock())
    assert texts(world, to="vigia")[-1].startswith(
        "Mala noticia: Kali tiene el SÍ a «72° a 73°» de Nueva York y ya van 74°"
    )
    assert len(texts(world, to="vigia")) == 2


# --- Vigía ------------------------------------------------------------------------------


def test_vigia_warns_you_when_a_bet_sinks_and_passes_on_what_nube_says():
    clock = Clock()
    world = World(None, clock)
    vigia = Vigia(world, clock=clock)
    pit = {"ticker": PIT, "name": "Gana Pittsburgh", "side": "NO", "cost": "4.46", "chance": "0.891"}
    world.update(
        "kali",
        detail={
            "connected": True,
            "positions": [pit],
            "balance": {"equity": "30.04"},
            "session_pnl": "-0.20",
            "max_loss": "10",
        },
    )
    vigia.run_if_due(clock())
    assert texts(world, to="tu") == []
    world.update("kali", detail={"positions": [{**pit, "chance": "0.47"}]})
    clock.advance(60)
    vigia.run_if_due(clock())
    clock.advance(60)
    vigia.run_if_due(clock())
    assert texts(world, to="tu") == [
        "Ojo: «Gana Pittsburgh» (NO) ha bajado del 89 % al 47 %. Si falla, se pierden $4,46."
    ]
    assert world.bot("vigia")["mood"] == "alert"

    world.say(
        "nube", "Ojo: Kali tiene el SÍ a «72° a 73°» de Nueva York; peligra.", to="vigia", kind="alert", ticker=PIT
    )
    world.update("kali", detail={"positions": [{**pit, "chance": "0.90"}], "session_pnl": "-7.50"})
    clock.advance(60)
    vigia.run_if_due(clock())
    said = texts(world, to="tu")
    assert said[1] == "Se recupera «Gana Pittsburgh»: vuelve al 90 %."
    assert said[2] == "Kali va −$7,50 desde que arrancó; el freno salta a −$10,00."
    assert said[3].startswith("Nube avisa: Ojo: Kali tiene el SÍ") and said[3].endswith("Hay $4,46 en juego.")


# --- Radar ------------------------------------------------------------------------------


def test_radar_counts_clear_favorites_that_kali_does_not_follow():
    clock = Clock()
    world = World(None, clock)
    world.update("kali", detail={"markets": [PIT]})
    kalshi = FakeKalshiPublic(
        [
            market("KXNHLGAME-26OCT08PITCBJ-PIT", "0.90", "0.92"),  # Kali ya sigue la NHL
            market("KXNFLGAME-26OCT09KCBUF-KC", "0.91", "0.93", "Kansas City vs Buffalo Winner?", "Kansas City"),
            market("KXNFLGAME-26OCT09DALNYG-NYG", "0.05", "0.08"),  # NO a 92¢
            market("KXMLBGAME-26OCT09LADSD-LAD", "0.50", "0.52"),  # sin favorito
            market("KXNFLGAME-26OCT09MIANE-MIA", "0.80", "0.95"),  # spread enorme
            market("KXNFLGAME-26OCT09SFSEA-SF", "0.92", "0.93", volume="3"),  # sin volumen
        ]
    )
    radar = Radar(world, kalshi, clock=clock)
    radar.run_if_due(clock())
    detail = world.bot("radar")["detail"]
    assert (detail["favorites"], detail["outside"]) == (3, 2)
    assert detail["by_series"] == [{"series": "KXNFLGAME", "label": "NFL", "count": 2}]
    assert texts(world, to="kali") == ["He visto 2 favoritos claros que Kali no mira: NFL (2)."]
    assert any(e["label"] == "Gana Kansas City · SÍ a 91¢" for e in detail["examples"])
    clock.advance(1300)
    radar.run_if_due(clock())
    assert len(texts(world, to="kali")) == 1  # sin cambios no repite


# --- Cronista ---------------------------------------------------------------------------


def test_cronista_writes_the_diary_and_the_news():
    clock = Clock(datetime(2026, 10, 9, 13, 5, tzinfo=timezone.utc).timestamp())  # 8:05 en Missouri
    world = World(None, clock)
    world.update(
        "kali",
        detail={
            "connected": True,
            "yesterday": {"net": "3.27", "markets": 15, "wins": 15, "losses": 0},
            "today": {"net": "4.45", "markets": 20, "wins": 19, "losses": 1},
            "totals": {"net": "8.84", "markets": 35},
        },
    )
    world.update("radar", detail={"favorites": 12})
    cronista = Cronista(world, "America/Chicago", clock=clock)
    world.say("kali", "Mercado cerrado: −$4,46 · Gana Pittsburgh", kind="trade", net="-4.46")
    world.say("kali", "Mercado cerrado: +$0,28 · Máxima en Miami", kind="trade", net="0.28")
    cronista.run_if_due(clock())
    diary = texts(world, kind="diary")
    assert diary[0] == "Noticia · Mal trago: Mercado cerrado: −$4,46 · Gana Pittsburgh"
    assert diary[1].startswith("Buenos días. Ayer Kali cerró 15 mercados: +$3,27 (15 ganados, 0 perdidos).")
    assert "Radar ve 12 favoritos" in diary[1]
    clock.advance(300)
    cronista.run_if_due(clock())
    assert len(texts(world, kind="diary")) == 2  # una vez al día
    clock.t = datetime(2026, 10, 10, 2, 31, tzinfo=timezone.utc).timestamp()  # 21:31 en Missouri
    cronista.next_run = 0
    cronista.run_if_due(clock())
    assert texts(world, kind="diary")[-1].startswith("Resumen del día: Kali cerró 20 mercados y va +$4,45")
