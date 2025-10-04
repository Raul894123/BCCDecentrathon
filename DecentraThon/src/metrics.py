# src/metrics.py
from __future__ import annotations

import re
import pandas as pd

# Расширенный и единообразный список CTA.
# Вытаскиваем ВЕСЬ триггер-слово (группа 1), чтобы потом считать частоты.
CTA_RE = re.compile(
    r"\b("
    r"Открыть(?:\s+(?:сч[её]т|вклад))?"      # Открыть / Открыть счёт / Открыть вклад
    r"|Оформить(?:\s+(?:карту|сейчас))?"      # Оформить / Оформить карту / Оформить сейчас
    r"|Подключить|Подключите"                 # Подключить / Подключите
    r"|Посмотреть(?:\s+условия)?"             # Посмотреть / Посмотреть условия
    r"|Настроить(?:\s+обмен)?"                # Настроить / Настроить обмен
    r"|Узнать(?:\s+лимит)?"                   # Узнать / Узнать лимит
    r")\b",
    re.IGNORECASE,
)


def date_coverage(df: pd.DataFrame, date_col: str = "date") -> dict:
    """
    Доля корректных (не NaT/не NaN) дат среди всех строк.
    Возвращает: {"total", "ok", "coverage"}.
    """
    total = len(df)
    if total == 0:
        return {"total": 0, "ok": 0, "coverage": 0.0}
    ok = int(df[date_col].notna().sum())
    return {"total": total, "ok": ok, "coverage": ok / total}


def push_length_stats(subm: pd.DataFrame, text_col: str = "push_notification") -> pd.DataFrame:
    """
    Базовые признаки качества для каждого пуша:
      - length               — длина строки
      - has_cta              — найден ли CTA из списка
      - in_range_180_220     — длина в целевом диапазоне ТЗ (включительно)
      - cta_text             — нормализованный CTA (первое совпадение)

    Возвращает датафрейм с колонками:
      ["client_code", "length", "has_cta", "in_range_180_220", "cta_text"]
    """
    df = subm.copy()
    texts = df[text_col].astype(str)

    # Длина
    df["length"] = texts.str.len()

    # Поиск CTA: используем векторный str.contains на компилированном паттерне
    df["has_cta"] = texts.str.contains(CTA_RE)

    # Извлечение первого совпавшего CTA (если нет — пустая строка)
    df["cta_text"] = texts.str.extract(CTA_RE, expand=False).fillna("")

    # Диапазон длины по ТЗ (180–220), включительно
    df["in_range_180_220"] = df["length"].between(180, 220, inclusive="both")

    return df[["client_code", "length", "has_cta", "in_range_180_220", "cta_text"]]


def push_quality_summary(push_stats_df: pd.DataFrame) -> dict:
    """
    Агрегированные метрики по датафрейму из push_length_stats:
      - count             — кол-во пушей
      - avg_length        — средняя длина
      - pct_in_range      — доля пушей в диапазоне 180–220
      - pct_has_cta       — доля пушей с CTA
      - unique_cta_ratio  — разнообразие CTA (кол-во уникальных / общее число CTA)
      - top_cta           — топ-5 CTA с частотами
    """
    n = len(push_stats_df)
    if n == 0:
        return {
            "count": 0,
            "avg_length": 0.0,
            "pct_in_range": 0.0,
            "pct_has_cta": 0.0,
            "unique_cta_ratio": 0.0,
            "top_cta": {},
        }

    avg_len = float(push_stats_df["length"].mean())
    pct_in_range = float(push_stats_df["in_range_180_220"].mean())
    pct_has_cta = float(push_stats_df["has_cta"].mean())

    # Частоты CTA считаем только по непустым значениям
    cta_counts = (
        push_stats_df["cta_text"]
        .replace("", pd.NA)
        .dropna()
        .value_counts()
    )

    total_cta = int(cta_counts.sum())
    unique_cta_ratio = 0.0 if total_cta == 0 else (cta_counts.nunique() / total_cta)
    top_cta = cta_counts.head(5).to_dict()

    return {
        "count": int(n),
        "avg_length": avg_len,
        "pct_in_range": pct_in_range,
        "pct_has_cta": pct_has_cta,
        "unique_cta_ratio": unique_cta_ratio,
        "top_cta": top_cta,
    }