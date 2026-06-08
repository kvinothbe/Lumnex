"""Intent router: classify the customer message into one of the platform's intent labels."""

from vizuara.intent.router import INTENTS, IntentResult, classify
from vizuara.intent.templates import short_circuit_reply

__all__ = ["INTENTS", "IntentResult", "classify", "short_circuit_reply"]
