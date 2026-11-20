from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import AuditLog


async def record_admin_action(
    db: AsyncSession,
    admin_username: str,
    action: str,
    details: str = "",
) -> None:
    """Persist one successful administrator action in the owner-facing audit."""
    db.add(AuditLog(
        admin_username=admin_username,
        action=action,
        details=details,
    ))
    await db.commit()
