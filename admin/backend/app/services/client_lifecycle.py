"""Shared client lookup and safe reactivation rules for the admin service."""

from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Client, ClientBlock, MobileSession, now_kz
from app.services.phone_utils import phones_match


async def find_client_by_phone(
    db: AsyncSession,
    phone: str,
    *,
    include_deleted: bool = False,
    for_update: bool = False,
) -> Optional[Client]:
    """Match canonical and legacy phone values, optionally locking the match."""
    exact_query = select(Client).where(Client.phone == phone)
    if not include_deleted:
        exact_query = exact_query.where(Client.is_deleted == False)
    if for_update:
        exact_query = exact_query.with_for_update()
    exact = (await db.execute(exact_query)).scalar_one_or_none()
    if exact and (not include_deleted or not exact.is_deleted):
        return exact

    candidates_query = select(Client).where(Client.phone.is_not(None))
    if not include_deleted:
        candidates_query = candidates_query.where(Client.is_deleted == False)
    else:
        candidates_query = candidates_query.order_by(Client.is_deleted, Client.id)
    candidates = (await db.execute(candidates_query)).scalars().all()
    candidate = next(
        (item for item in candidates if phones_match(item.phone, phone)),
        None,
    )
    if not candidate or not for_update:
        return candidate
    return (await db.execute(
        select(Client).where(Client.id == candidate.id).with_for_update()
    )).scalar_one()


async def reactivate_deleted_client(
    db: AsyncSession,
    client: Client,
    *,
    name: str,
    phone: str,
    password_hash: Optional[str],
) -> bool:
    """Turn a tombstone into a usable new profile without reviving old access."""
    if not client.is_deleted:
        return False

    current_time = now_kz()
    client.is_deleted = False
    client.name = name.strip() or client.name
    client.phone = phone
    # A deleted card may still contain an old Telegram identity. Reactivation
    # must not silently grant that old account access to the new profile.
    client.telegram_id = None
    client.password_hash = password_hash
    client.offline_operation_id = None
    client.reschedule_count_24h = 0
    client.reschedule_window_started_at = None
    client.support_chat_opened_at = None
    client.support_chat_closed_at = None

    await db.execute(
        update(MobileSession)
        .where(MobileSession.client_id == client.id)
        .values(is_active=False)
    )
    await db.execute(
        update(ClientBlock)
        .where(
            ClientBlock.client_id == client.id,
            ClientBlock.blocked_until > current_time,
        )
        .values(blocked_until=current_time)
    )
    return True
