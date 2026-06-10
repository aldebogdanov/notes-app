from .base import NotificationAdapter
from .telegram import TelegramAdapter


def build_adapter_registry(settings) -> dict[str, NotificationAdapter]:
    """Adapters configured in this deployment, keyed by channel name.

    Empty token means the channel is off; the app must boot without it.
    """
    registry: dict[str, NotificationAdapter] = {}
    if settings.telegram_bot_token:
        registry[TelegramAdapter.name] = TelegramAdapter(settings.telegram_bot_token)
    return registry
