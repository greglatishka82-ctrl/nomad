"""
Утилиты для нормализации и сравнения телефонных номеров.
Хранение: +7XXXXXXXXXX (всегда с + и кодом страны).
Сравнение: по последним 10 цифрам.
"""


def normalize_phone(phone: str | None) -> str | None:
    """
    Нормализует номер телефона к формату +7XXXXXXXXXX.

    Примеры:
      "77027182233"   -> "+77027182233"
      "+77027182233"  -> "+77027182233"
      "+7 702 718 22 33" -> "+77027182233"
      "8 (702) 718-22-33" -> "+77027182233"
      "7027182233"    -> "+77027182233"
      None            -> None
      ""              -> None
    """
    if not phone or not phone.strip():
        return None

    digits = "".join(filter(str.isdigit, phone))

    if not digits:
        return None

    # Если номер начинается с 8 (как в КЗ/РФ) — заменяем на 7
    if digits.startswith("8") and len(digits) >= 11:
        digits = "7" + digits[1:]

    # Если номер из 10 цифр (без кода страны) — добавляем 7
    if len(digits) == 10:
        digits = "7" + digits

    # Если номер из 11 цифр и начинается не с 7 — добавляем 7
    if len(digits) == 11 and not digits.startswith("7"):
        digits = "7" + digits

    # Если уже 12+ цифр и начинается с 7 — берём последние 11
    if len(digits) > 11 and digits.startswith("7"):
        digits = digits[-11:]

    if len(digits) < 10:
        return None

    return "+" + digits


def phones_match(phone1: str | None, phone2: str | None) -> bool:
    """
    Сравнивает два номера по последним 10 цифрам.
    Работает даже если один номер с '+', а другой без.
    """
    if not phone1 or not phone2:
        return False

    d1 = "".join(filter(str.isdigit, phone1))
    d2 = "".join(filter(str.isdigit, phone2))

    if not d1 or not d2:
        return False

    return d1[-10:] == d2[-10:]


def phone_last10(phone: str | None) -> str | None:
    """Возвращает последние 10 цифр номера (для сравнений в БД)."""
    if not phone:
        return None
    digits = "".join(filter(str.isdigit, phone))
    return digits[-10:] if len(digits) >= 10 else None
