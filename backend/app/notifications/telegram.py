import anyio
import httpx

from .base import NotificationSendError

TELEGRAM_MESSAGE_LIMIT = 4096
_MAX_ATTEMPTS = 3
_BACKOFF_SECONDS = (0.5, 1.0, 2.0)
_REQUEST_TIMEOUT = 10.0


class TelegramAdapter:
    name = "telegram"

    def __init__(
        self,
        token: str,
        *,
        base_url: str = "https://api.telegram.org",
        client: httpx.AsyncClient | None = None,
        sleep=anyio.sleep,
    ) -> None:
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._client = client
        self._sleep = sleep

    async def send(self, chat_ref: str, text: str) -> None:
        # Only the adapter knows its channel's cap; callers send full text.
        if len(text) > TELEGRAM_MESSAGE_LIMIT:
            text = text[: TELEGRAM_MESSAGE_LIMIT - 1] + "…"
        url = f"{self._base_url}/bot{self._token}/sendMessage"
        payload = {"chat_id": chat_ref, "text": text}

        last_error = ""
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            wait = _BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS)) - 1]
            try:
                response = await self._post(url, payload)
            except httpx.HTTPError as exc:
                # str(exc) may contain the tokenized URL — keep only the type.
                last_error = f"network error: {type(exc).__name__}"
            else:
                if response.status_code == 200:
                    return
                last_error = f"HTTP {response.status_code}: {self._description(response)}"
                if response.status_code == 429:
                    retry_after = self._retry_after(response)
                    if retry_after is not None:
                        wait = retry_after
                elif response.status_code < 500:
                    # Bad chat_id, blocked bot, bad token: retrying cannot help.
                    raise NotificationSendError(f"telegram send failed: {last_error}")
            if attempt < _MAX_ATTEMPTS:
                await self._sleep(wait)
        raise NotificationSendError(
            f"telegram send failed after {_MAX_ATTEMPTS} attempts: {last_error}"
        )

    async def _post(self, url: str, payload: dict) -> httpx.Response:
        if self._client is not None:
            return await self._client.post(url, json=payload, timeout=_REQUEST_TIMEOUT)
        async with httpx.AsyncClient() as client:
            return await client.post(url, json=payload, timeout=_REQUEST_TIMEOUT)

    @staticmethod
    def _description(response: httpx.Response) -> str:
        try:
            return response.json().get("description", "")
        except ValueError:
            return ""

    @staticmethod
    def _retry_after(response: httpx.Response) -> float | None:
        try:
            value = response.json().get("parameters", {}).get("retry_after")
        except ValueError:
            return None
        return float(value) if value is not None else None
