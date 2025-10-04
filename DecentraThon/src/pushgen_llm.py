# src/pushgen_llm.py
from __future__ import annotations

import hashlib
import re
from typing import Dict, Optional

from .utils import kzt  # форматирование сумм (разряды + " ₸")

# =========================
# Константы
# =========================
MAX_LEN = 220  # лимит по ТЗ (целимся в 180–220)

# Регэксп для поиска CTA (используется и в клампере, и в ensure_cta)
_CTA_RE = re.compile(
    r"\b(Открыть|Оформить|Подключить|Подключите|Посмотреть|Настроить|Узнать)\b",
    re.IGNORECASE,
)

# =========================
# Хелперы
# =========================
def _smart_clamp(text: str, max_len: int = MAX_LEN) -> str:
    """
    Аккуратно ограничивает длину и ГАРАНТИРУЕТ, что CTA в конце не пропадёт.
    Логика:
      1) Чистим лишние '!' (не более одного).
      2) Если длина в норме — возвращаем как есть.
      3) Если есть CTA — сохраняем хвост от последнего CTA до конца, подрезаем только «голову».
      4) Если CTA нет — обычное «умное» обрезание с многоточием.
    """
    if not isinstance(text, str):
        return ""
    t = re.sub(r"!{2,}", "!", text.strip())
    if len(t) <= max_len:
        return t

    # есть CTA → держим хвост
    m_all = list(_CTA_RE.finditer(t))
    if m_all:
        last = m_all[-1]
        tail = t[last.start():].rstrip()
        if not tail.endswith((".", "!", "?")):
            tail = tail + "."
        head_budget = max_len - len(tail) - 1  # минус пробел между головой и хвостом
        head_budget = max(head_budget, 0)
        head = t[:head_budget]

        # красиво подрезаем голову
        cut = head
        for stop in (". ", "! ", "? "):
            i = head.rfind(stop)
            if i >= head_budget - 40:
                cut = head[: i + 1].rstrip()
                break
        else:
            i = head.rfind(" ")
            if i >= head_budget - 25:
                cut = head[:i]
            cut = cut.rstrip(" ,;") + "…"

        return (cut + " " + tail).strip()

    # CTA нет → обычный «умный» кламп
    cut = t[:max_len]
    for stop in (". ", "! ", "? "):
        i = cut.rfind(stop)
        if i >= max_len - 40:
            return cut[: i + 1].rstrip()
    i = cut.rfind(" ")
    if i >= max_len - 25:
        cut = cut[:i]
    return cut.rstrip(" ,;") + "…"


def _norm_status(raw: Optional[str]) -> str:
    """Грубая нормализация статуса для выбора тона."""
    if not isinstance(raw, str):
        return "Стандартный клиент"
    s = raw.strip().lower()
    if "студент" in s:
        return "Студент"
    if "зарплат" in s:
        return "Зарплатный клиент"
    if "преми" in s:
        return "Премиальный клиент"
    return "Стандартный клиент"


def _month_hint(month: Optional[str]) -> str:
    """Возвращает корректную вставку месяца или дефолт «этом месяце»."""
    return month.strip() if isinstance(month, str) and month.strip() else "этом месяце"


def _seeded_choice(seed: str, options: list[str]) -> str:
    """
    Детеминированный выбор варианта:
      — одна и та же комбинация seed → всегда один вариант,
      — разные клиенты/ключи → разные варианты.
    """
    if not options:
        return ""
    h = int(hashlib.md5(seed.encode("utf-8")).hexdigest(), 16)
    return options[h % len(options)]


def _cta_for_product_var(product: str, key: str) -> str:
    """Возвращает CTA для продукта с вариативностью (подмешиваем key в seed)."""
    pools = {
        "Карта для путешествий": ["Оформить карту", "Оформить сейчас"],
        "Премиальная карта":     ["Оформить сейчас", "Подключить карту"],
        "Кредитная карта":       ["Оформить карту", "Подключить карту"],
        "Обмен валют":           ["Настроить обмен", "Посмотреть курс"],
        "FX":                    ["Настроить обмен", "Посмотреть курс"],
        "Инвестиции":            ["Открыть счёт", "Посмотреть условия"],
        "Кредит наличными":      ["Узнать лимит", "Посмотреть условия"],
        "Золотые слитки":        ["Посмотреть условия", "Открыть в приложении"],
    }
    default = ["Оформить сейчас", "Посмотреть", "Подключить"]
    opts = pools.get(product, default)
    return _seeded_choice(f"{key}|cta|{product}", opts)


def _tone_preamble(name: str, status: Optional[str], age: Optional[int], city: Optional[str]) -> str:
    """Короткий персональный префейс (без официоза, на «вы»)."""
    status = _norm_status(status)
    name_part = f"{name}," if name else "Здравствуйте,"
    city_hint = f" в {city.strip()}" if isinstance(city, str) and city.strip() else ""

    if status == "Студент":
        return f"{name_part} если часто{city_hint} пользуетесь сервисами и такси — есть выгоднее."
    if status == "Премиальный клиент":
        return f"{name_part} с вашим профилем можно оптимизировать расходы и сервис."
    if status == "Зарплатный клиент":
        return f"{name_part} давайте сделаем повседневные платежи удобнее."
    return f"{name_part} смотрим на последние траты и подбираем лучший продукт."


def _benefit_phrase(product: str, mon: str, ctx: Dict, key: str) -> str:
    """
    Если есть benefit_num — даём аккуратную оценку + короткое «почему».
    Иначе — мягкий продуктовый фолбэк.
    """
    b = float(ctx.get("benefit_num", 0.0) or 0.0)
    b_txt = ctx.get("benefit", "0 ₸")
    travel, prem, online = ctx.get("travel_sum"), ctx.get("premium_sum"), ctx.get("online_sum")
    fav = ctx.get("top3_names") or ctx.get("fav", "")
    atm, trf = ctx.get("atm_cnt"), ctx.get("transfer_cnt")

    if b <= 0:
        candidates = {
            "Карта для путешествий": [
                "Часть расходов на поездки вернулась бы кешбэком.",
                "По поездкам и такси можно получать возврат.",
            ],
            "Премиальная карта": [
                "Базовый кешбэк и повышенный — на рестораны/косметику.",
                "Повышенный кешбэк и экономия на комиссиях.",
            ],
            "Кредитная карта": [
                "До 10% в любимых категориях и на онлайн-сервисы.",
                "Кешбэк до 10% по частым тратам.",
            ],
            "Обмен валют": [
                "Выгодный курс и автопокупка по цели.",
                "Можно настроить автопокупку по целевому курсу.",
            ],
        }.get(product, [
            "Это поможет экономить и упростит платежи.",
            "Подойдёт по вашему профилю расходов.",
        ])
        extra = []
        if product == "Карта для путешествий" and travel:
            extra.append(f"Поездки: {travel}")
        if product == "Премиальная карта" and prem:
            extra.append(f"Рестораны/косметика: {prem}")
        if product == "Кредитная карта" and online:
            extra.append(f"Онлайн-сервисы: {online}")
        if product == "Премиальная карта" and (atm or trf):
            extra.append(f"Снятия/переводы: {atm}/{trf}")
        tail = (". " + "; ".join(extra)) if extra else ""
        return _seeded_choice(f"{key}|bf0", candidates) + tail

    core = _seeded_choice(
        f"{key}|bf",
        [
            f"В {mon} ориентировочная выгода ~{b_txt}.",
            f"По вашему профилю в {mon} вернулось бы около {b_txt}.",
            f"В {mon} можно было бы сэкономить примерно {b_txt}.",
            f"Оценка выгоды за {mon}: около {b_txt}.",
        ],
    )
    reason_parts = []
    if product == "Карта для путешествий" and travel:
        reason_parts.append(f"за счёт поездок ({travel})")
    if product == "Премиальная карта" and prem:
        reason_parts.append(f"за счёт премиальных категорий ({prem})")
    if product == "Кредитная карта" and (fav or online):
        reason_parts.append("за счёт любимых категорий и онлайн-сервисов")
    if product == "Премиальная карта" and (atm or trf):
        reason_parts.append("и снижения комиссий")
    reason = " " + _seeded_choice(f"{key}|why", ["", (" ".join(reason_parts) + ".").strip() if reason_parts else ""])
    return core + reason

# =========================
# Генерация текста
# =========================
def generate_personalized_push(
    product: str,
    name: str,
    month: str,
    ctx: Dict,
    demo: Optional[Dict] = None,
    max_len: Optional[int] = None,
) -> str:
    """
    Короткий персональный пуш (1 мысль + 1 CTA):
      — персональный префейс,
      — подводка под продукт с числами из контекста,
      — вариативная формулировка выгоды,
      — детерминированный CTA под продукт.
    """
    demo = demo or {}
    status, age, city = demo.get("status"), demo.get("age"), demo.get("city")

    pre = _tone_preamble(name, status, age, city)
    mon = _month_hint(month)
    key = f"{name}|{product}|{mon}|{status or ''}|{city or ''}"
    cta = _cta_for_product_var(product, key)

    # Подводка под продукт
    if product == "Карта для путешествий":
        lead = f"В {mon} заметные траты на поездки и такси ({ctx.get('travel_sum','0 ₸')}). "
    elif product == "Премиальная карта":
        lead = f"С вашим остатком и тратами в ресторанах/косметике ({ctx.get('premium_sum','0 ₸')}) выгода выше. "
    elif product == "Кредитная карта":
        fav = ctx.get("top3_names") or ctx.get("fav", "-")
        lead = f"Ваши топ-категории — {fav}; онлайн-сервисы на {ctx.get('online_sum','0 ₸')}. "
    elif product in ("Обмен валют", "FX"):
        lead = f"Нужен удобный курс{f' для поездок в {city}' if city else ''}? Настройте автопокупку. "
    elif product.startswith("Депозит сбер"):
        lead = "Если средства лежат без снятий — сберегательный вклад даст максимум. "
    elif product.startswith("Депозит накоп"):
        lead = "Регулярно пополняете — копите на цели без снятий. "
    elif product.startswith("Депозит мульти"):
        lead = "Храните тенге и валюту вместе с начислением. "
    elif product == "Инвестиции":
        lead = "Низкий порог входа и сниженные комиссии для аккуратного старта. "
    elif product == "Кредит наличными":
        lead = "Нужна подушка на крупные траты — оформите кредит и настройте выплаты. "
    elif product == "Золотые слитки":
        lead = "Физическое золото для диверсификации и сохранения стоимости. "
    else:
        lead = "Подбираем вариант с наибольшей пользой для вашего профиля. "

    benefit = _benefit_phrase(product, mon, ctx, key)
    msg = f"{pre} {lead}{benefit} {cta}."

    # Мягко ограничиваем длину (CTA хвост не теряется)
    limit = max_len if isinstance(max_len, int) and max_len > 0 else MAX_LEN
    return _smart_clamp(msg, limit)

# =========================
# Автодобавление CTA (подстраховка)
# =========================
def ensure_cta(product: str, text: str, key: str = "") -> str:
    """
    Если в тексте нет CTA из допустимого списка — аккуратно добавим
    релевантный для продукта (через точку в конце).
    key — стабильный идентификатор (например, client_code) для вариативности.
    """
    if not isinstance(text, str) or not text.strip():
        return text
    if _CTA_RE.search(text):
        return text

    try:
        cta = _cta_for_product_var(product, key or product)
    except Exception:
        cta = "Оформить сейчас"

    cleaned = text.rstrip().rstrip(".!?").rstrip()
    return f"{cleaned}. {cta}."