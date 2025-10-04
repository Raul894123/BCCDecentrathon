# utils.py
from __future__ import annotations

import csv
import io
import os
import re
import time
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd


# =========================
#        КОНСТАНТЫ
# =========================

# Имена месяцев в предложном падеже
RU_MONTHS: Dict[int, str] = {
    1: "январе", 2: "феврале", 3: "марте", 4: "апреле",
    5: "мае", 6: "июне", 7: "июле", 8: "августе",
    9: "сентябре", 10: "октябре", 11: "ноябре", 12: "декабре",
}

# Наборы категорий (используются в скоринге/тексте)
ONLINE_CATS = {"Едим дома", "Смотрим дома", "Играем дома"}
PREMIUM_BOOST_CATS = {"Ювелирные украшения", "Косметика и Парфюмерия", "Кафе и рестораны"}
TRAVEL_CATS = {"Путешествия", "Такси", "Отели", "АЗС"}


# =========================
#   АНТИСПАМ-ЛОГИКА ДЛЯ PUSH
# =========================

_last_sent: Dict[int, Tuple[float, str, str]] = {}  # client_code -> (ts, last_cta, last_product)

def can_send(client_code: int, cta: str, product: str, min_hours: int = 48) -> bool:
    """
    Простая антиспам-проверка:
    - не чаще 1 раза в `min_hours`;
    - не повторять тот же CTA подряд.
    """
    now = time.time()
    last = _last_sent.get(client_code)

    if not last:
        _last_sent[client_code] = (now, cta, product)
        return True

    ts, last_cta, _ = last
    if now - ts < min_hours * 3600:
        return False
    if cta.strip().lower() == str(last_cta).strip().lower():
        return False

    _last_sent[client_code] = (now, cta, product)
    return True


# =========================
#   ПАРСИНГ ДАТ/СУММ
# =========================

def _parse_dates_series(s: pd.Series) -> pd.Series:
    """
    Умный парсер дат для столбца:
    - если у половины+ значений формат dd.mm.yyyy → dayfirst;
    - иначе — обычный `to_datetime`.
    """
    s = s.astype(str)

    # Быстрая эвристика: заметная доля значений вида dd.mm.yyyy
    if s.str.contains(r"\b\d{1,2}[.]\d{1,2}[.]\d{2,4}\b", regex=True).mean() >= 0.5:
        return pd.to_datetime(s, errors="coerce", dayfirst=True)

    return pd.to_datetime(s, errors="coerce")


# Все типичные артефакты csv → в число (точка — разделитель)
_AMOUNT_JUNK_RE = re.compile(r"[^0-9.\-]")

def _parse_amount_series(s: pd.Series) -> pd.Series:
    """
    Нормализует денежные значения:
    - убирает NBSP/пробелы;
    - заменяет запятые на точки;
    - выбрасывает нечисловые символы;
    - приводит к float, NaN → 0.0.
    """
    s = (
        s.astype(str)
         .str.replace("\u00A0", "", regex=False)   # NBSP
         .str.replace(" ", "", regex=False)
         .str.replace(",", ".", regex=False)
         .str.replace(_AMOUNT_JUNK_RE, "", regex=True)
    )
    return pd.to_numeric(s, errors="coerce").fillna(0.0)


# =========================
#      ЧТЕНИЕ CSV
# =========================

def _detect_delimiter(sample_text: str) -> str:
    """
    Пытаемся определить разделитель (',', ';', '\\t', '|').
    Возвращаем наиболее вероятный, по умолчанию — запятая.
    """
    try:
        dialect = csv.Sniffer().sniff(sample_text, delimiters=[",", ";", "\t", "|"])
        return dialect.delimiter
    except Exception:
        counts = {d: sample_text.count(d) for d in [",", ";", "\t", "|"]}
        delim = max(counts, key=counts.get)
        return delim if counts[delim] > 0 else ","


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Единообразные имена: нижний регистр, '_' вместо пробелов/дефисов."""
    df.columns = (
        df.columns
          .str.strip()
          .str.lower()
          .str.replace(" ", "_", regex=False)
          .str.replace("-", "_", regex=False)
    )
    return df


def _read_csv_smart(path: str) -> pd.DataFrame:
    """
    Читает CSV с автоопределением разделителя и нормализацией заголовков.
    Кодировка: utf-8-sig, безопасная замена испорченных символов.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(path)

    with io.open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        sample = f.read(4096)
        delim = _detect_delimiter(sample)

    df = pd.read_csv(path, encoding="utf-8-sig", sep=delim)
    return _normalize_columns(df)


def _coalesce_col(
    df: pd.DataFrame,
    candidates: Sequence[str],
    required: bool = True,
    default_val=None,
) -> str:
    """
    Возвращает первое подходящее имя колонки из списка candidates.
    Если ничего не найдено:
      - required=True → KeyError с подсказкой;
      - required=False → создаёт столбец с `default_val` и возвращает его имя.
    """
    for c in candidates:
        if c in df.columns:
            return c

    if required:
        raise KeyError(
            f"Не найдена ни одна из колонок: {list(candidates)}. Нашлись: {list(df.columns)}"
        )

    name = candidates[0]
    df[name] = default_val
    return name


def _to_int64_nullable(series: pd.Series) -> pd.Series:
    """Безопасно приводим к Int64 (nullable)."""
    return pd.to_numeric(series, errors="coerce").astype("Int64")


def read_data(data_dir: str) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Загружает:
      - clients.csv
      - transactions.csv (или transaction.csv как запасной вариант)
      - transfers.csv

    Делает:
      - нормализацию имён столбцов,
      - приведение дат/сумм,
      - выравнивание ключевых имён колонок.
    """
    # --- clients
    clients = _read_csv_smart(os.path.join(data_dir, "clients.csv"))

    # --- transactions
    tx_path = os.path.join(data_dir, "transactions.csv")
    if not os.path.exists(tx_path):
        tx_path = os.path.join(data_dir, "transaction.csv")
    tx = _read_csv_smart(tx_path)

    # --- transfers
    tr = _read_csv_smart(os.path.join(data_dir, "transfers.csv"))

    # --- сопоставления для transactions
    tx_date = _coalesce_col(tx, ["date", "дата"])
    tx_cat  = _coalesce_col(tx, ["category", "категория", "cat"])
    tx_amt  = _coalesce_col(tx, ["amount", "sum", "amount_kzt", "amt", "value", "сумма"])
    tx_cur  = _coalesce_col(tx, ["currency", "валюта", "curr"], required=False, default_val="KZT")
    tx_cid  = _coalesce_col(tx, ["client_code", "clientid", "client_id", "id"])

    # --- сопоставления для transfers
    tr_date = _coalesce_col(tr, ["date", "дата"])
    tr_type = _coalesce_col(tr, ["type", "тип"])
    tr_dir  = _coalesce_col(tr, ["direction", "dir", "направление"])
    tr_amt  = _coalesce_col(tr, ["amount", "sum", "amount_kzt", "amt", "value", "сумма"])
    tr_cur  = _coalesce_col(tr, ["currency", "валюта", "curr"], required=False, default_val="KZT")
    tr_cid  = _coalesce_col(tr, ["client_code", "clientid", "client_id", "id"])

    # --- приведение типов (даты / суммы / client_code)
    for df, dcol in ((tx, tx_date), (tr, tr_date)):
        df[dcol] = _parse_dates_series(df[dcol])

    for df, acol in ((tx, tx_amt), (tr, tr_amt)):
        df[acol] = _parse_amount_series(df[acol])

    for df, ccol in ((clients, "client_code"), (tx, tx_cid), (tr, tr_cid)):
        if ccol in df.columns:
            df[ccol] = _to_int64_nullable(df[ccol])

    # --- переименование в канонические столбцы
    tx = tx.rename(columns={
        tx_date: "date",
        tx_cat: "category",
        tx_amt: "amount",
        tx_cur: "currency",
        tx_cid: "client_code",
    })
    tr = tr.rename(columns={
        tr_date: "date",
        tr_type: "type",
        tr_dir: "direction",
        tr_amt: "amount",
        tr_cur: "currency",
        tr_cid: "client_code",
    })

    return clients, tx, tr


# =========================
#       УТИЛИТЫ ТЕКСТА
# =========================

def kzt(x: float) -> str:
    """
    Форматирование суммы в тенге:
    1234567.8 → "1 234 568 ₸"
    """
    try:
        val = int(round(float(x)))
    except Exception:
        val = 0
    return f"{val:,}".replace(",", " ").replace("\xa0", " ") + " ₸"


def month_word(dt: pd.Timestamp) -> str:
    """'2025-02-10' → 'феврале'. Если нет даты — 'этом месяце'."""
    try:
        return RU_MONTHS.get(int(pd.Timestamp(dt).month), "этом месяце")
    except Exception:
        return "этом месяце"


def last_month_span(dates: pd.Series) -> Tuple[Optional[pd.Timestamp], Optional[pd.Timestamp]]:
    """
    Возвращает границы последнего месяца, встречающегося в Series:
      (start_inclusive, next_month_start_exclusive)
    Если дат нет — (None, None).
    """
    s = dates.dropna()
    if s.empty:
        return None, None

    last_date = pd.Timestamp(s.max())
    start = last_date.replace(day=1)

    # начало следующего месяца
    if start.month == 12:
        next_start = start.replace(year=start.year + 1, month=1, day=1)
    else:
        next_start = start.replace(month=start.month + 1, day=1)

    return start, next_start


def sum_in_period(df: pd.DataFrame,
                  start: Optional[pd.Timestamp],
                  end: Optional[pd.Timestamp],
                  filt: Optional[pd.Series]) -> float:
    """
    Сумма `amount` за [start, end) c дополнительной маской `filt`.
    Если start=None — возвращает 0.0.
    """
    if start is None or end is None:
        return 0.0
    m = (df["date"] >= start) & (df["date"] < end)
    if filt is not None:
        m &= filt
    return float(df.loc[m, "amount"].sum())


def top_categories(tx_client: pd.DataFrame, topn: int = 3) -> Tuple[List[str], pd.Series]:
    """
    Возвращает (список_топN_категорий, Series суммы по категориям).
    """
    grp = tx_client.groupby("category", dropna=False)["amount"].sum().sort_values(ascending=False)
    return list(grp.index[:topn]), grp