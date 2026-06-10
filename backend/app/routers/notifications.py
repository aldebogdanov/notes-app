import secrets
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from ..deps import get_current_user, get_db
from ..models import User
from ..notifications import (
    KNOWN_CHANNELS,
    NotificationAdapter,
    NotificationSendError,
    get_channel_config,
)
from ..rate_limit import (
    auth_rate_limit_key,
    check_auth_rate_limit,
    clear_auth_failures,
    record_auth_failure,
)
from ..schemas import (
    NotificationChannelOut,
    NotificationSettingsIn,
    NotificationSettingsOut,
    TelegramLinkOut,
)

router = APIRouter(prefix="/account/notifications", tags=["notifications"])

LINK_CODE_TTL_SECONDS = 600


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _registry(request: Request) -> dict[str, NotificationAdapter]:
    return getattr(request.app.state, "adapter_registry", None) or {}


def _telegram_adapter_or_503(request: Request):
    adapter = _registry(request).get("telegram")
    if adapter is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Telegram is not configured on this server",
        )
    return adapter


def _notify_scheduler(request: Request) -> None:
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is not None:
        scheduler.notify_change()


def _telegram_raw(user: User) -> dict:
    return ((user.notification_settings or {}).get("channels") or {}).get("telegram") or {}


def _mutate(user: User, channel: str, changes: dict, *, drop: tuple[str, ...] = ()) -> None:
    settings = user.notification_settings or {}
    channels = settings.setdefault("channels", {})
    config = channels.setdefault(channel, {})
    config.update(changes)
    for key in drop:
        config.pop(key, None)
    user.notification_settings = settings
    flag_modified(user, "notification_settings")


def _settings_out(user: User, request: Request) -> NotificationSettingsOut:
    registry = _registry(request)
    channels = {}
    for channel in KNOWN_CHANNELS:
        config = get_channel_config(user, channel)
        channels[channel] = NotificationChannelOut(
            available=channel in registry,
            enabled=config.enabled,
            linked=config.chat_ref is not None,
        )
    timezone = (user.notification_settings or {}).get("timezone") or "UTC"
    return NotificationSettingsOut(timezone=timezone, channels=channels)


@router.get("/settings", response_model=NotificationSettingsOut)
def get_settings(
    request: Request,
    user: User = Depends(get_current_user),
) -> NotificationSettingsOut:
    return _settings_out(user, request)


@router.put("/settings", response_model=NotificationSettingsOut)
def update_settings(
    request: Request,
    payload: NotificationSettingsIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> NotificationSettingsOut:
    if payload.timezone is not None:
        try:
            ZoneInfo(payload.timezone)
        except Exception:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Unknown timezone '{payload.timezone}'",
            ) from None
        settings = user.notification_settings or {}
        settings["timezone"] = payload.timezone
        user.notification_settings = settings
        flag_modified(user, "notification_settings")
    for channel, channel_in in (payload.channels or {}).items():
        if channel not in KNOWN_CHANNELS:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unknown channel '{channel}'"
            )
        if channel_in.enabled and get_channel_config(user, channel).chat_ref is None:
            raise HTTPException(status.HTTP_409_CONFLICT, f"Link {channel} first")
        _mutate(user, channel, {"enabled": channel_in.enabled})
    db.commit()
    # A freshly-enabled due note should fire on the next pass, not the rescan.
    _notify_scheduler(request)
    return _settings_out(user, request)


@router.post("/telegram/link", response_model=TelegramLinkOut)
async def telegram_link(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TelegramLinkOut:
    adapter = _telegram_adapter_or_503(request)
    try:
        username = await adapter.get_bot_username()
    except NotificationSendError:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Telegram API unavailable") from None
    code = secrets.token_urlsafe(8)
    _mutate(
        user,
        "telegram",
        {"link_code": code, "link_code_issued_at": _utcnow().isoformat()},
    )
    db.commit()
    return TelegramLinkOut(
        code=code,
        deep_link=f"https://t.me/{username}?start={code}",
        expires_in=LINK_CODE_TTL_SECONDS,
    )


@router.post("/telegram/verify", response_model=NotificationSettingsOut)
async def telegram_verify(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> NotificationSettingsOut:
    rate_limit_key = auth_rate_limit_key(request, "tg-verify", user.id)
    check_auth_rate_limit(rate_limit_key)
    adapter = _telegram_adapter_or_503(request)

    raw = _telegram_raw(user)
    code = raw.get("link_code")
    issued_at_raw = raw.get("link_code_issued_at")
    expired = True
    if code and issued_at_raw:
        issued_at = datetime.fromisoformat(issued_at_raw)
        expired = _utcnow() - issued_at > timedelta(seconds=LINK_CODE_TTL_SECONDS)
    if expired:
        _mutate(user, "telegram", {}, drop=("link_code", "link_code_issued_at"))
        db.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No active link code")

    try:
        updates = await adapter.get_updates()
    except NotificationSendError:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Telegram API unavailable") from None
    expected = f"/start {code}"
    match = next((u for u in updates if u.text == expected), None)
    if match is None:
        record_auth_failure(rate_limit_key)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Press Start in Telegram, then retry")
    _mutate(
        user,
        "telegram",
        {"chat_id": match.chat_id},
        drop=("link_code", "link_code_issued_at"),
    )
    db.commit()
    clear_auth_failures(rate_limit_key)
    return _settings_out(user, request)


@router.delete("/telegram/link", response_model=NotificationSettingsOut)
def telegram_unlink(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> NotificationSettingsOut:
    _mutate(
        user,
        "telegram",
        {"enabled": False},
        drop=("chat_id", "link_code", "link_code_issued_at"),
    )
    db.commit()
    return _settings_out(user, request)
