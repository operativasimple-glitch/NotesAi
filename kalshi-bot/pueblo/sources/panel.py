"""Lectura del panel del bot de Kalshi (solo lectura: nunca envía órdenes ni cambia ajustes).

El panel pide iniciar sesión con su contraseña y la cabecera X-Requested-With: kalshi-bot.
"""

from __future__ import annotations

import requests

CSRF = {"X-Requested-With": "kalshi-bot"}
READ_ONLY = ("/api/status", "/api/positions", "/api/results", "/api/logs", "/api/labels")


class PanelError(Exception):
    pass


class PanelClient:
    def __init__(self, base_url: str, password: str, session=None, timeout: float = 20.0):
        self.base_url = base_url.rstrip("/")
        self.password = password
        self.session = session or requests.Session()
        self.timeout = timeout
        self._logged_in = False

    def _login(self) -> None:
        try:
            resp = self.session.post(
                f"{self.base_url}/api/login", json={"password": self.password}, headers=CSRF, timeout=self.timeout
            )
        except requests.RequestException as exc:
            raise PanelError(f"No contesta el panel: {exc.__class__.__name__}") from exc
        if resp.status_code == 429:
            raise PanelError("El panel está bloqueado un momento por intentos fallidos; se reintentará")
        if resp.status_code != 200:
            raise PanelError("El panel no acepta la contraseña (revisa PANEL_PASSWORD)")
        self._logged_in = True

    def get(self, path: str, **params) -> object:
        if not path.startswith(READ_ONLY):  # el pueblo solo lee del panel
            raise PanelError(f"{path} no es de solo lectura")
        for attempt in range(2):
            if not self._logged_in:
                self._login()
            try:
                resp = self.session.get(f"{self.base_url}{path}", params=params, headers=CSRF, timeout=self.timeout)
            except requests.RequestException as exc:
                raise PanelError(f"No contesta el panel: {exc.__class__.__name__}") from exc
            if resp.status_code == 401 and attempt == 0:
                self._logged_in = False  # la sesión caducó (o el panel se reinició): se entra otra vez
                continue
            if resp.status_code != 200:
                try:
                    detail = resp.json().get("error", "")
                except ValueError:
                    detail = ""
                raise PanelError(f"El panel respondió {resp.status_code}{': ' + detail if detail else ''}")
            return resp.json()
        raise PanelError("El panel no deja entrar")

    def status(self) -> dict:
        return self.get("/api/status")

    def positions(self) -> list:
        return self.get("/api/positions")

    def results(self, days: int = 2, tz_minutes: int = 0) -> dict:
        return self.get("/api/results", days=days, tz=tz_minutes, scope="bot")

    def logs(self, after: int = 0) -> list:
        return self.get("/api/logs", after=after)

    def labels(self, tickers: list) -> dict:
        if not tickers:
            return {}
        return self.get("/api/labels", tickers=",".join(tickers))
