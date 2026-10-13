"""python -m pueblo: arranca los bots y la web del pueblo."""

from __future__ import annotations

import logging
import signal
import sys

from .agents import build_agents
from .config import ConfigError, load_config
from .runtime import Town
from .web.server import make_server
from .world import World


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"Error de configuración: {exc}", file=sys.stderr)
        return 2
    world = World(config.data_dir)
    town = Town(world, build_agents(world, config))
    server = make_server(world, town, config.password, config.host, config.port)

    def stop(signum, frame):  # noqa: ARG001
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    town.start()
    logging.getLogger("pueblo").info("Pueblo abierto en http://%s:%d", config.host, config.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        town.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
