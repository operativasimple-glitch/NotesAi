"""Línea de comandos: python -m kalshi_bot <comando>."""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import signal
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

from .client import KalshiAPIError
from .config import ConfigError, Settings, load_settings
from .engine import Bot, DryRunExecutor, Journal, LiveExecutor
from .models import Market, to_decimal
from .research import pct, run_research, run_sweep, verdict
from .risk import RiskManager
from .scanner import ScanParams, run_scan
from .strategies import build_strategy

log = logging.getLogger("kalshi_bot")


# --------------------------------------------------------------------------
# Formato
# --------------------------------------------------------------------------


def cents(price: Optional[Decimal]) -> str:
    """0.455 -> '45.5¢'."""
    if price is None:
        return "—"
    text = f"{price * 100:.2f}".rstrip("0").rstrip(".")
    return f"{text}¢"


def money(value: Decimal) -> str:
    return f"${value:,.2f}"


def contracts(value: Decimal) -> str:
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def time_left(market: Market, now: datetime) -> str:
    hours = market.hours_to_close(now)
    if hours is None:
        return "—"
    if hours < 0:
        return "cerrado"
    if hours < 1:
        return f"{hours * 60:.0f}m"
    if hours < 48:
        return f"{int(hours)}h{int((hours % 1) * 60):02d}m"
    return f"{hours / 24:.0f}d"


def short(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


# --------------------------------------------------------------------------
# Comandos
# --------------------------------------------------------------------------


def cmd_check(settings: Settings, args) -> int:
    print(f"Entorno: {settings.env}  ({settings.base_url})")
    signer = settings.signer()
    client = settings.client(signer)
    status = client.get_exchange_status()
    print(
        f"Exchange: activo={'sí' if status.get('exchange_active') else 'no'}, "
        f"trading={'sí' if status.get('trading_active') else 'no'}"
    )
    if signer is None:
        print("Credenciales: no configuradas. Puedes ver mercados y simular, pero no operar.")
        print("Crea una API key en Kalshi y rellena .env (ver README).")
        return 0
    balance = client.get_balance()
    print(f"Credenciales: OK (key {settings.api_key_id[:8]}…)")
    print(f"Saldo disponible: {money(balance.cash)} | Valor del portafolio: {money(balance.portfolio_value)}")
    try:
        limits = client.get_account_limits()
        read, write = limits.get("read") or {}, limits.get("write") or {}
        print(
            f"Nivel de API: {limits.get('usage_tier', '?')} "
            f"(lectura {read.get('refill_rate', '?')} tokens/s, escritura {write.get('refill_rate', '?')} tokens/s)"
        )
    except KalshiAPIError as exc:
        log.debug("No se pudieron leer los límites de la API: %s", exc)
    positions = client.get_positions()
    orders = client.get_orders(status="resting")
    prefix = settings.engine.order_prefix + "-"
    own = sum(1 for o in orders if o.client_order_id.startswith(prefix))
    print(f"Posiciones abiertas: {len(positions)} | Órdenes en reposo: {len(orders)} (del bot: {own})")
    print("Todo listo.")
    return 0


def cmd_events(settings: Settings, args) -> int:
    client = settings.client(settings.signer())
    events = client.get_events(series_ticker=args.series, limit=args.limit)
    if not events:
        print("No hay eventos abiertos con ese filtro.")
        return 0
    print(f"{'EVENTO':<34} {'SERIE':<16} TÍTULO")
    for ev in events:
        ticker, series, title = ev.get("event_ticker") or "", ev.get("series_ticker") or "", ev.get("title") or ""
        print(f"{ticker:<34} {series:<16} {short(title, 70)}")
    print("\nVer sus mercados: python -m kalshi_bot markets --event <EVENTO>")
    return 0


def top_series(client, category: Optional[str], top: int) -> list:
    """Series ordenadas por volumen total (las más negociadas primero)."""
    rows = client.get_series_list(category=category)
    rows.sort(key=lambda r: to_decimal(r.get("volume_fp"), to_decimal(r.get("volume"), Decimal(0))), reverse=True)
    return rows[:top]


def cmd_series(settings: Settings, args) -> int:
    client = settings.client(settings.signer())
    rows = top_series(client, args.category, args.limit)
    if not rows:
        print("No hay series con ese filtro. Prueba con Financials, Economics, Sports o Climate and Weather.")
        return 0
    print(f"{'SERIE':<24} {'CATEGORÍA':<22} {'VOLUMEN':>14}  TÍTULO")
    for r in rows:
        ticker, category, title = r.get("ticker") or "", r.get("category") or "", r.get("title") or ""
        volume = to_decimal(r.get("volume_fp"), to_decimal(r.get("volume"), Decimal(0)))
        print(f"{ticker:<24} {short(category, 22):<22} {volume:>14,.0f}  {short(title, 60)}")
    print("\nAnaliza las más negociadas: python -m kalshi_bot research --category <CATEGORÍA> --top 8")
    return 0


def cmd_markets(settings: Settings, args) -> int:
    if not (args.series or args.event):
        print("Indica --series o --event (usa 'events' para descubrirlos).", file=sys.stderr)
        return 2
    client = settings.client(settings.signer())
    markets = client.get_markets(series_ticker=args.series, event_ticker=args.event, max_pages=3)
    if not markets:
        print("No hay mercados abiertos con ese filtro.")
        return 0
    now = datetime.now(timezone.utc)
    markets.sort(key=lambda m: m.volume_24h, reverse=True)
    print(f"{'TICKER':<36} {'BID':>7} {'ASK':>7} {'ÚLTIMO':>7} {'VOL 24H':>9} {'CIERRA':>8}  DESCRIPCIÓN")
    for m in markets[: args.limit]:
        desc = m.subtitle or m.title
        print(
            f"{m.ticker:<36} {cents(m.yes_bid):>7} {cents(m.yes_ask):>7} {cents(m.last_price):>7} "
            f"{contracts(m.volume_24h):>9} {time_left(m, now):>8}  {short(desc, 50)}"
        )
    if len(markets) > args.limit:
        print(f"… y {len(markets) - args.limit} más (usa --limit)")
    print("\nPrecios del lado YES. Comprar NO a X¢ equivale a vender YES a (100 - X)¢.")
    return 0


def cmd_book(settings: Settings, args) -> int:
    client = settings.client(settings.signer())
    market = client.get_market(args.ticker)
    book = client.get_orderbook(args.ticker)
    print(f"{market.ticker} — {market.title} {('· ' + market.subtitle) if market.subtitle else ''}")
    print(f"Estado: {market.status} | Cierra: {market.close_time.isoformat() if market.close_time else '—'}")
    print(f"Mejor bid {cents(book.best_bid)} | mejor ask {cents(book.best_ask)} | spread {cents(book.spread)}")
    print(f"\n{'VENTAS YES (ask)':>28}")
    for level in reversed(book.asks[: args.depth]):
        print(f"{cents(level.price):>14} {contracts(level.size):>12}")
    print(f"{'-' * 28}")
    for level in book.bids[: args.depth]:
        print(f"{cents(level.price):>14} {contracts(level.size):>12}")
    print(f"{'COMPRAS YES (bid)':>28}")
    return 0


def cmd_positions(settings: Settings, args) -> int:
    client = settings.client(_require_signer(settings))
    positions = client.get_positions()
    if not positions:
        print("No tienes posiciones abiertas.")
        return 0
    print(f"{'TICKER':<36} {'POSICIÓN':>12} {'EXPOSICIÓN':>11} {'PNL REAL.':>10} {'COMISIONES':>10}")
    for p in sorted(positions.values(), key=lambda x: x.ticker):
        side = "YES" if p.position > 0 else "NO"
        print(
            f"{p.ticker:<36} {contracts(abs(p.position)) + ' ' + side:>12} {money(p.exposure):>11} "
            f"{money(p.realized_pnl):>10} {money(p.fees_paid):>10}"
        )
    return 0


def cmd_orders(settings: Settings, args) -> int:
    client = settings.client(_require_signer(settings))
    orders = client.get_orders(status="resting")
    if not orders:
        print("No tienes órdenes en reposo.")
        return 0
    prefix = settings.engine.order_prefix + "-"
    print(f"{'TICKER':<36} {'LADO':<10} {'PRECIO':>7} {'PENDIENTE':>10}  BOT  ORDER_ID")
    for o in sorted(orders, key=lambda x: (x.ticker, x.side, x.price)):
        side = "compra YES" if o.side == "bid" else "vende YES"
        mine = "sí " if o.client_order_id.startswith(prefix) else "no "
        print(f"{o.ticker:<36} {side:<10} {cents(o.price):>7} {contracts(o.remaining):>10}  {mine}  {o.order_id}")
    return 0


def cmd_cancel_all(settings: Settings, args) -> int:
    client = settings.client(_require_signer(settings))
    if args.everything:
        client.cancel_all_orders()
        print("Enviada la cancelación de TODAS las órdenes en reposo de la cuenta.")
        return 0
    executor = LiveExecutor(client, settings.engine.order_prefix, Journal(settings.log_dir / "journal.jsonl"))
    orders = executor.resting_orders()
    for order in orders:
        try:
            executor.cancel(order)
        except KalshiAPIError as exc:
            print(f"No se pudo cancelar {order.order_id}: {exc}", file=sys.stderr)
    print(f"Órdenes del bot canceladas: {len(orders)}")
    return 0


def cmd_run(settings: Settings, args) -> int:
    if settings.config_path is None:
        print("Aviso: no hay config.toml; copia config.example.toml a config.toml y edítalo.", file=sys.stderr)
    _add_file_logging(settings.log_dir / "bot.log")
    signer = settings.signer()
    client = settings.client(signer)
    strategy = build_strategy(settings.strategy_name, settings.strategy_params)
    journal = Journal(settings.log_dir / "journal.jsonl")

    if args.live:
        if signer is None:
            raise ConfigError("--live necesita credenciales (KALSHI_API_KEY_ID y la clave privada en .env)")
        if settings.is_production and not args.yes:
            _countdown_real_money(settings)
        executor = LiveExecutor(
            client, settings.engine.order_prefix, journal, ttl_seconds=settings.engine.order_ttl_seconds
        )
    else:
        log.info("Modo simulación: no se enviará ninguna orden. Usa --live para operar.")
        executor = DryRunExecutor(settings.engine.order_prefix, journal)

    bot = Bot(
        client,
        strategy,
        RiskManager(settings.risk),
        executor,
        settings.engine,
        env_name=settings.env,
        journal=journal,
    )
    bot.run(max_ticks=1 if args.once else None)
    return 1 if bot.halted_reason else 0


def cmd_scan(settings: Settings, args) -> int:
    client = settings.client(settings.signer())
    params = ScanParams(
        closing_within_hours=args.hours,
        series=args.series or [],
        min_volume_24h=Decimal(str(args.min_volume)),
        include_arbitrage=not args.no_arbitrage,
    )
    print("Escaneando mercados (solo lectura)...", file=sys.stderr)
    result = run_scan(client, params, datetime.now(timezone.utc))
    print(f"Mercados revisados: {result['markets_scanned']}\n")

    print(f"FAVORITOS (lado que cotiza entre {cents(params.fav_min_price)} y {cents(params.fav_max_price)})")
    if not result["favorites"]:
        print("  (ninguno)")
    for f in result["favorites"]:
        side = "SÍ" if f["side"] == "yes" else "NO"
        print(
            f"  {f['ticker']:<34} {side:<2} bid {cents(f['bid']):>6} ask {cents(f['ask']):>6} "
            f"cierra {f['hours_to_close'] if f['hours_to_close'] is not None else '?'}h  "
            f"vol {contracts(f['volume_24h'])}"
        )
    print(f"\nSPREADS AMPLIOS (≥ {cents(params.wide_spread)})")
    if not result["spreads"]:
        print("  (ninguno)")
    for s in result["spreads"]:
        print(
            f"  {s['ticker']:<34} bid {cents(s['bid']):>6} ask {cents(s['ask']):>6} "
            f"spread {cents(s['spread']):>5}  vol {contracts(s['volume_24h'])}"
        )
    if params.include_arbitrage:
        print("\nARBITRAJE EN EVENTOS (tras comisiones taker; confírmalo en el libro)")
        if not result["arbitrage"]:
            print("  (ninguno)")
        for a in result["arbitrage"]:
            print(
                f"  {a['event_ticker']:<30} {a['description']}: {a['legs']} patas, "
                f"beneficio {cents(a['profit'])} por juego de contratos"
            )
            if a["warning"]:
                print(f"    ⚠ {a['warning']}")
    return 0


def cmd_research(settings: Settings, args) -> int:
    client = settings.client(settings.signer())
    if args.sweep:
        return _print_sweep(client, args)
    if args.category:
        chosen = [r.get("ticker") for r in top_series(client, args.category, args.top) if r.get("ticker")]
        if not chosen:
            print(f"No hay series en la categoría {args.category!r} (mira 'series' para ver las que hay).")
            return 1
        print(f"Series más negociadas de {args.category}: {', '.join(chosen)}", file=sys.stderr)
        args.series = ",".join(chosen)

    def progress(done: int, total: int) -> None:
        if done == total or done % 10 == 0:
            print(f"  {done}/{total} mercados", file=sys.stderr)

    print("Descargando mercados liquidados y sus operaciones...", file=sys.stderr)
    report = run_research(
        client,
        series=args.series,
        max_markets=args.markets,
        skip_last_minutes=args.skip_last_minutes,
        trades_pages=args.pages,
        by_time=args.by_time,
        keep_groups=bool(args.dump_groups),
        exits=args.exits,
        progress=progress,
    )
    if args.dump_groups:
        _save_json(report.pop("groups_dump"), args.dump_groups)
    _save_json(report, args.json)
    print(f"\nSerie: {report['series']} | mercados: {report['markets']} | operaciones: {report['trades']}")
    if report["by_series"]:
        print("\n" + band_header())
        for name, stats in report["by_series"].items():
            print(_band_line(name, stats))
        print(_band_line("TODAS JUNTAS", report["strategy"]))
        print()
    if report.get("exits"):
        print("\n¿Cobrar antes de tiempo? Mismas compras, vendiendo antes en vez de esperar al final:")
        print(exits_header())
        for row in report["exits"]:
            print(_exit_line(row["rule"], row))
        for name, rows in report.get("exits_by_series", {}).items():
            print(f"\n  {name}:")
            for row in rows:
                print(_exit_line(row["rule"], row))
        print()
    if report["by_time"]:
        print("\n¿Cuándo gana? Según lo que faltaba para el cierre (en deportes, el final del partido):")
        print(band_header("MOMENTO"))
        for row in report["by_time"]:
            print(_band_line(row["window"], row))
        print()
    print(f"{'TRAMO':<10} {'TAKER':>18} {'MAKER':>18} {'TODOS':>18}")
    print(f"{'':<10} {'rend. (tras com.)':>18} {'rend. (tras com.)':>18} {'acierto/precio':>18}")
    for row in report["buckets"]:

        def fmt(stats):
            if not stats.get("contracts"):
                return "—"
            return f"{stats['return_after_fees'] * 100:+.1f}%"

        both = row["all"]
        ratio = f"{both['win_rate'] * 100:.1f}% / {both['avg_price'] * 100:.1f}¢" if both.get("contracts") else "—"
        print(f"{row['range']:<10} {fmt(row['taker']):>18} {fmt(row['maker']):>18} {ratio:>18}")
    print()
    for note in report["conclusions"]:
        print(f"• {note}")
    return 0


def cmd_web(settings: Settings, args) -> int:
    from .controller import BotController
    from .web.server import make_server

    password = os.environ.get("DASHBOARD_PASSWORD", "")
    if len(password) < 8:
        raise ConfigError(
            "Define DASHBOARD_PASSWORD (8 caracteres o más) en .env o en las variables del servidor: "
            "es la contraseña para entrar al panel"
        )
    port = args.port or int(os.environ.get("PORT") or 8000)
    controller = BotController(args.config)
    logging.getLogger().addHandler(controller.logs)
    _add_file_logging(settings.log_dir / "bot.log")
    server = make_server(controller, password, args.host, port)
    log.info("Panel web escuchando en http://%s:%d (entorno %s)", args.host, port, settings.env)
    controller.resume_if_needed()

    def on_term(signum, frame):  # noqa: ARG001
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, on_term)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        log.info("Apagando el panel: deteniendo el bot y cancelando sus órdenes...")
        server.server_close()
        controller.shutdown()
    return 0


def _print_sweep(client, args) -> int:
    def progress(done: int, total: int) -> None:
        if done == total or done % 25 == 0:
            print(f"  {done}/{total} mercados", file=sys.stderr)

    print("Comparando series: mercados liquidados y sus operaciones (puede tardar unos minutos)...", file=sys.stderr)
    report = run_sweep(
        client,
        series_count=args.series_count,
        per_series=args.per_series,
        skip_last_minutes=args.skip_last_minutes,
        progress=progress,
    )
    _save_json(report, args.json)
    print(f"\nSeries analizadas: {report['series_analyzed']} | mercados: {report['markets']}")
    print(f"Estrategia del bot: comprar a 88–97¢ como maker (sin los últimos {report['skip_last_minutes']} min)\n")
    print(band_header())

    for row in report["rows"]:
        print(_band_line(row["series"], row["strategy"]))
    print(_band_line("TODAS JUNTAS", report["overall"]))
    print()
    for note in report["conclusions"]:
        print(f"• {note}")
    return 0


def band_header(first: str = "SERIE") -> str:
    return f"{first:<20} {'EVENTOS':>7} {'RENDIMIENTO':>12} {'MARGEN DE ERROR (95 %)':>24} {'FALLOS':>7}  VEREDICTO"


def _band_line(name: str, stats: dict) -> str:
    """Una fila de la tabla: eventos, rendimiento, margen de error, fallos y veredicto."""
    if not stats.get("contracts"):
        return f"{name:<20} {stats.get('groups', 0):>7} {'—':>12} {'—':>24} {'—':>7}  sin datos"
    margin = f"{pct(stats['ci_low'])} a {pct(stats['ci_high'])}" if "ci_low" in stats else "—"
    upsets = f"{stats['losing_groups']}/{stats['groups']}"
    return (
        f"{name:<20} {stats['groups']:>7} {pct(stats['return_after_fees']):>12} {margin:>24} {upsets:>7}  "
        f"{verdict(stats)}"
    )


def exits_header() -> str:
    return (
        f"{'REGLA':<22} {'EVENTOS':>7} {'ESPERANDO':>10} {'CON REGLA':>10} {'DIFERENCIA':>11} "
        f"{'MARGEN DIF. (95 %)':>20} {'VENDIDO':>8} {'IBA A PERDER':>13}"
    )


def _exit_line(name: str, row: dict) -> str:
    """Una fila de la tabla de salidas antes de tiempo."""
    if "difference" not in row:
        return f"{name:<22} {row.get('groups', 0):>7}  sin datos"
    margin = f"{pct(row['diff_low'])} a {pct(row['diff_high'])}" if "diff_low" in row else "—"
    return (
        f"{name:<22} {row['groups']:>7} {pct(row['return_hold']):>10} {pct(row['return_rule']):>10} "
        f"{pct(row['difference']):>11} {margin:>20} {row['sold_share'] * 100:>7.1f}% "
        f"{row['sold_would_lose_share'] * 100:>12.1f}%"
    )


def _save_json(report: dict, path: Optional[str]) -> None:
    if path:
        Path(path).write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def _require_signer(settings: Settings):
    signer = settings.signer()
    if signer is None:
        raise ConfigError("Este comando necesita credenciales: rellena KALSHI_API_KEY_ID y la clave en .env")
    return signer


def _countdown_real_money(settings: Settings, seconds: int = 10) -> None:
    risk = settings.risk
    print("=" * 70, file=sys.stderr)
    print(f" ATENCIÓN: vas a operar con DINERO REAL en Kalshi ({settings.base_url}).", file=sys.stderr)
    print(
        f" Límites: {contracts(risk.max_order_contracts)} contratos/orden, "
        f"{contracts(risk.max_position_per_market)} por mercado, "
        f"exposición máx. {money(risk.max_total_exposure)}, pérdida máx. {money(risk.max_session_loss)}",
        file=sys.stderr,
    )
    print(f" Pulsa Ctrl+C en los próximos {seconds} segundos para abortar.", file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    time.sleep(seconds)


# --------------------------------------------------------------------------
# Logging y entrada
# --------------------------------------------------------------------------


def _setup_logging(verbose: bool) -> None:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S"))
    root.addHandler(handler)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def _add_file_logging(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(path, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)


def _explain_api_error(exc: KalshiAPIError) -> str:
    hint = ""
    if exc.is_auth_error:
        hint = (
            "\nPista: Kalshi rechazó las credenciales. Comprueba KALSHI_API_KEY_ID, que el .pem sea el de esa "
            "key y que KALSHI_ENV coincida con donde la creaste (las keys de demo solo sirven en demo). "
            "Revisa también que el reloj del sistema esté en hora."
        )
    elif exc.code == "network_error":
        hint = "\nPista: no se pudo conectar con Kalshi. Revisa tu conexión a internet."
    elif exc.status == 404:
        hint = "\nPista: no existe ese recurso. ¿Está bien escrito el ticker?"
    return f"Error de la API: {exc}{hint}"


COMMANDS = {
    "check": cmd_check,
    "events": cmd_events,
    "markets": cmd_markets,
    "book": cmd_book,
    "positions": cmd_positions,
    "orders": cmd_orders,
    "cancel-all": cmd_cancel_all,
    "run": cmd_run,
    "scan": cmd_scan,
    "research": cmd_research,
    "series": cmd_series,
    "web": cmd_web,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m kalshi_bot", description="Bot de trading para Kalshi")
    parser.add_argument("-c", "--config", help="ruta a config.toml (por defecto ./config.toml)")
    parser.add_argument("-v", "--verbose", action="store_true", help="más detalle en el log")
    sub = parser.add_subparsers(dest="command", required=True, metavar="comando")

    sub.add_parser("check", help="verifica conexión, credenciales y saldo")

    p = sub.add_parser("series", help="lista las series más negociadas (todas o de una categoría)")
    p.add_argument("--category", help="p. ej. Financials, Economics, Sports")
    p.add_argument("--limit", type=int, default=40)

    p = sub.add_parser("events", help="lista eventos abiertos")
    p.add_argument("--series", help="filtra por serie, p. ej. KXHIGHNY")
    p.add_argument("--limit", type=int, default=25)

    p = sub.add_parser("markets", help="lista mercados abiertos de una serie o evento")
    p.add_argument("--series", help="ticker de la serie, p. ej. KXHIGHNY")
    p.add_argument("--event", help="ticker del evento")
    p.add_argument("--limit", type=int, default=30)

    p = sub.add_parser("book", help="muestra el libro de órdenes de un mercado")
    p.add_argument("ticker")
    p.add_argument("--depth", type=int, default=10)

    sub.add_parser("positions", help="muestra tus posiciones abiertas")
    sub.add_parser("orders", help="muestra tus órdenes en reposo")

    p = sub.add_parser("cancel-all", help="cancela las órdenes del bot")
    p.add_argument("--everything", action="store_true", help="cancela TODAS las órdenes de la cuenta")

    p = sub.add_parser("scan", help="busca oportunidades ahora (favoritos, spreads, arbitraje)")
    p.add_argument("--hours", type=float, default=48, help="mercados que cierran en las próximas N horas")
    p.add_argument("--series", nargs="*", help="limitar a estas series")
    p.add_argument("--min-volume", type=float, default=100, help="volumen mínimo en 24 h")
    p.add_argument("--no-arbitrage", action="store_true", help="no revisar eventos de varios resultados")

    p = sub.add_parser("research", help="mide con datos reales quién gana a cada precio")
    p.add_argument("--series", help="serie a estudiar, o varias separadas por comas (por defecto, todas)")
    p.add_argument("--markets", type=int, default=150, help="mercados liquidados a analizar")
    p.add_argument(
        "--skip-last-minutes",
        type=int,
        default=15,
        help="ignora las operaciones de los últimos minutos antes del cierre (el bot no opera ahí)",
    )
    p.add_argument("--sweep", action="store_true", help="compara las series activas: ¿dónde ganan los favoritos?")
    p.add_argument("--series-count", type=int, default=12, help="con --sweep: cuántas series comparar")
    p.add_argument("--per-series", type=int, default=60, help="con --sweep: mercados liquidados por serie")
    p.add_argument("--json", metavar="ARCHIVO", help="guarda también el informe completo en JSON")
    p.add_argument("--category", help="analiza las series más negociadas de esta categoría (p. ej. Financials)")
    p.add_argument("--pages", type=int, default=2, help="páginas de 1000 operaciones por mercado (más = más historia)")
    p.add_argument("--by-time", action="store_true", help="separa el resultado según lo que faltaba para el cierre")
    p.add_argument("--dump-groups", metavar="ARCHIVO", help="guarda los datos por evento (para combinar pruebas)")
    p.add_argument("--exits", action="store_true", help="compara esperar al final con vender antes de tiempo")
    p.add_argument("--top", type=int, default=8, help="con --category: cuántas series")

    p = sub.add_parser("web", help="abre el panel web para manejar el bot desde el móvil")
    p.add_argument("--host", default="0.0.0.0", help="interfaz de red (por defecto todas)")
    p.add_argument("--port", type=int, help="puerto (por defecto $PORT o 8000)")

    p = sub.add_parser("run", help="ejecuta el bot (simulación salvo que uses --live)")
    p.add_argument("--live", action="store_true", help="envía órdenes de verdad")
    p.add_argument("--once", action="store_true", help="hace una sola vuelta y termina")
    p.add_argument("--yes", action="store_true", help="omite la cuenta atrás de seguridad en prod")
    return parser


def main(argv: Optional[list] = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    try:
        settings = load_settings(args.config)
        return COMMANDS[args.command](settings, args) or 0
    except ConfigError as exc:
        print(f"Error de configuración: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except KalshiAPIError as exc:
        print(_explain_api_error(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrumpido.", file=sys.stderr)
        return 130
