from typing import Protocol, runtime_checkable


class NotificationSendError(Exception):
    """Send failed after adapter-level retries; message carries the reason."""


@runtime_checkable
class NotificationAdapter(Protocol):
    """A delivery channel. `name` doubles as the note_notifications.channel value."""

    name: str

    async def send(self, chat_ref: str, text: str) -> None:
        """Deliver text to chat_ref. Raises NotificationSendError on failure."""
        ...
