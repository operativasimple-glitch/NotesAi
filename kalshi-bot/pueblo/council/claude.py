"""Claude en el Consejo: lee un mercado y da su probabilidad con una frase (opcional).

Solo se usa si pones ANTHROPIC_API_KEY. Cada pregunta cuesta dinero de tu cuenta de Anthropic,
así que hay un límite de preguntas al día (PUEBLO_AI_MAX_CALLS).
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from .models import clamp

log = logging.getLogger(__name__)

SYSTEM = (
    "Eres Claude, miembro del Consejo de un pueblo de bots que estudia mercados de predicción de Kalshi. "
    "Para cada mercado das la probabilidad, de 1 a 99, de que se resuelva SÍ, y una razón corta en español "
    "(como mucho 25 palabras, en tono cercano). Sé calibrado: si los datos no bastan, quédate cerca del "
    "precio del mercado. No inventes datos que no te den."
)
SCHEMA = {
    "type": "object",
    "properties": {
        "probabilidad": {"type": "integer", "description": "Probabilidad de SÍ, de 1 a 99"},
        "razon": {"type": "string", "description": "Por qué, en una frase corta en español"},
    },
    "required": ["probabilidad", "razon"],
    "additionalProperties": False,
}


def cents(p: Optional[float]) -> str:
    return "—" if p is None else f"{round(p * 100)}¢"


def prompt_for(topic: dict, ctx: dict, others: dict) -> str:
    """Lo que se le cuenta a Claude de un mercado: el de verdad, sin adornos."""
    lines = [
        f"Mercado: {topic.get('title') or topic['ticker']}",
        f"Opción SÍ: {topic.get('subtitle') or '—'}",
        f"Ticker: {topic['ticker']}",
        f"Cierra: {topic.get('close_time') or '—'}",
        f"Precio ahora: se compra SÍ a {cents(topic.get('ask'))} y se vende a {cents(topic.get('bid'))}"
        f" (último {cents(topic.get('last'))}, hace un día {cents(topic.get('previous'))}).",
    ]
    if topic.get("rules"):
        lines.append(f"Reglas: {topic['rules']}")
    row = (ctx.get("cities") or {}).get(topic.get("city") or "")
    if topic.get("kind") == "weather" and row:
        lines.append(
            f"Servicio Meteorológico de EE. UU. ({row.get('city')}): máxima prevista hoy {row.get('today')} °F, "
            f"mañana {row.get('tomorrow')} °F; lo más alto medido hoy {row.get('max_so_far')} °F; "
            f"ahora {row.get('now')} °F. El mercado es del día {topic.get('date')}."
        )
    if others:
        lines.append(
            "Lo que opina el resto del Consejo: " + ", ".join(f"{n} {round(p * 100)} %" for n, p in others.items())
        )
    return "\n".join(lines)


class ClaudeForecaster:
    def __init__(self, api_key: str, model: str, client=None):
        if client is None:
            import anthropic

            client = anthropic.Anthropic(api_key=api_key, timeout=120.0, max_retries=2)
        self.client = client
        self.model = model

    def forecast(self, prompt: str) -> Optional[dict]:
        """{"fair": 0–1, "reason": "..."} o None si no quiere contestar."""
        extra = {}
        if "haiku" not in self.model:  # Haiku no tiene relevo en el servidor
            extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=16000,
            system=SYSTEM,
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": prompt}],
            **extra,
        )
        if response.stop_reason == "refusal":
            log.info("Claude no quiso opinar de este mercado")
            return None
        text = next((b.text for b in response.content if getattr(b, "type", "") == "text"), "")
        data = json.loads(text)
        return {"fair": clamp(int(data["probabilidad"]) / 100), "reason": str(data["razon"]).strip()[:220]}
