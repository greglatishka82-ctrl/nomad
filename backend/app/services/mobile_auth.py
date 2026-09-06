"""
Сервис аутентификации для мобильного приложения
"""
import bcrypt


def hash_password(password: str) -> str:
    """Хеширование пароля"""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    """Проверка пароля. None/пустой хэш => пароль не совпадает (без падения)."""
    if not hashed:
        return False
    return bcrypt.checkpw(password.encode(), hashed.encode())
