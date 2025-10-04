# src/build_data.py
import os
import re
import io
import csv
import sys
from pathlib import Path
from typing import List, Optional, Tuple

import pandas as pd

# Корневая папка с данными
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# -----------------------------------------------------------
#                 УТИЛИТЫ ЧТЕНИЯ И НОРМАЛИЗАЦИИ
# -----------------------------------------------------------

def detect_delimiter(sample_text: str) -> str:
    """
    Пытаемся угадать разделитель CSV по 4КБ префиксу файла.
    Фолбэк — самый частый символ из списка; если ничего — запятая.
    """
    try:
        dialect = csv.Sniffer().sniff(sample_text, delimiters=[",", ";", "\t", "|"])
        return dialect.delimiter
    except Exception:
        counts = {d: sample_text.count(d) for d in [",", ";", "\t", "|"]}
        delim = max(counts, key=counts.get)
        return delim if counts[delim] > 0 else ","


def _normalize_headers(df: pd.DataFrame) -> pd.DataFrame:
    """Единообразно нормализуем имена столбцов: trim, lower, пробелы/дефисы → '_'."""
    df = df.copy()
    df.columns = (
        df.columns
          .str.strip()
          .str.lower()
          .str.replace(" ", "_", regex=False)
          .str.replace("-", "_", regex=False)
    )
    return df


def read_csv_smart(path: Path) -> pd.DataFrame:
    """
    Читает CSV c авто-определением разделителя и нормализацией заголовков.
    Бросает исключение, если файл пуст/нечитаем.
    """
    with io.open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        sample = f.read(4096) or ","
        delim = detect_delimiter(sample)
    df = pd.read_csv(path, sep=delim, encoding="utf-8-sig")
    if df.empty:
        raise ValueError("Файл пустой")
    return _normalize_headers(df)


def coalesce_col(df: pd.DataFrame, variants: List[str], required: bool = True, default=None) -> str:
    """
    Возвращает первое найденное имя колонки из списка variants.
    Если ни одной нет: либо поднимаем исключение, либо создаём колонку с default.
    """
    for v in variants:
        if v in df.columns:
            return v
    if required:
        raise KeyError(f"Не найдена ни одна из колонок: {variants}. Есть: {list(df.columns)}")
    name = variants[0]
    df[name] = default
    return name


def parse_client_code_from_filename(path: Path) -> Optional[int]:
    """
    Пробуем вытащить client_code из имени файла: client_123 / client-123 / client 123.
    """
    m = re.search(r"client[_\- ]?(\d+)", path.name, flags=re.I)
    return int(m.group(1)) if m else None


def _to_datetime_safe(s: pd.Series) -> pd.Series:
    """
    Универсальный парсер дат:
    - пробуем ISO/свободный формат
    - если много шаблонов dd.mm.yyyy — повторяем с dayfirst=True
    """
    s = s.astype(str)
    series = pd.to_datetime(s, errors="coerce")
    # если >50% не распарсилось, и похоже на dd.mm.yyyy — пробуем dayfirst
    if series.isna().mean() > 0.5 and s.str.contains(r"\b\d{1,2}[.]\d{1,2}[.]\d{2,4}\b", regex=True).mean() > 0.3:
        series = pd.to_datetime(s, errors="coerce", dayfirst=True)
    return series


def _to_amount_safe(s: pd.Series) -> pd.Series:
    """
    Приводим к числу:
    - удаляем пробелы/NBSP, запятые → точки, всё нечисловое → пусто
    """
    s = (
        s.astype(str)
         .str.replace("\u00A0", "", regex=False)  # NBSP
         .str.replace(" ", "", regex=False)
         .str.replace(",", ".", regex=False)
         .str.replace(r"[^0-9.\-]", "", regex=True)
    )
    return pd.to_numeric(s, errors="coerce").fillna(0.0)


def _cast_client_code(s: pd.Series) -> pd.Series:
    """Код клиента → Int64 (nullable), строковые артефакты → NaN."""
    return pd.to_numeric(s, errors="coerce").astype("Int64")


# -----------------------------------------------------------
#                РАСПОЗНАВАНИЕ ТИПОВ ФАЙЛОВ
# -----------------------------------------------------------

def detect_table_kind(df: pd.DataFrame) -> Tuple[bool, bool]:
    """
    Пытаемся понять, таблица транзакций (transactions) это или переводов (transfers).
    Логика:
      - transactions: есть 'category', НО может не быть 'direction'
      - transfers   : есть 'type' и 'direction'
      - если есть и то, и то — решаем по количеству непустых значений
    """
    is_tx = ("category" in df.columns) and ("direction" not in df.columns)
    is_tr = ("type" in df.columns) and ("direction" in df.columns)

    if is_tx or is_tr:
        return is_tx, is_tr

    cat_nonnull = df["category"].notna().sum() if "category" in df.columns else 0
    type_has = ("type" in df.columns) and ("direction" in df.columns)
    type_nonnull = df["type"].notna().sum() if type_has else 0

    if cat_nonnull >= type_nonnull and "category" in df.columns:
        return True, False
    if type_has:
        return False, True
    return False, False


# -----------------------------------------------------------
#                      ОСНОВНОЙ СБОРЩИК
# -----------------------------------------------------------

def main():
    tx_frames, tr_frames = [], []

    # Все CSV в data/ (включая подпапки), кроме целевых итоговых файлов
    csv_files = [p for p in DATA_DIR.rglob("*.csv")]
    blacklist = {"transactions.csv", "transaction.csv", "transfers.csv", "clients.csv"}
    csv_files = [p for p in csv_files if p.name.lower() not in blacklist]

    if not csv_files:
        print("⚠️  В data/ не найдено ни одного клиентского CSV.")
        sys.exit(1)

    handled, skipped = 0, 0
    for path in csv_files:
        try:
            df = read_csv_smart(path)
        except Exception as e:
            skipped += 1
            print(f"— пропускаю (не читается): {path} → {e}")
            continue

        # если нет client_code — попробуем вытащить из имени
        if "client_code" not in df.columns:
            cid = parse_client_code_from_filename(path)
            if cid is not None:
                df["client_code"] = cid

        is_transactions, is_transfers = detect_table_kind(df)

        if is_transactions:
            # Нормализация ожидаемых столбцов для transactions
            date = coalesce_col(df, ["date", "дата"])
            cat  = coalesce_col(df, ["category", "категория", "cat"])
            amt  = coalesce_col(df, ["amount", "sum", "amount_kzt", "amt", "value", "сумма"])
            cur  = coalesce_col(df, ["currency", "валюта", "curr"], required=False, default="KZT")
            cid  = coalesce_col(df, ["client_code", "clientid", "client_id", "id"])

            df[date] = _to_datetime_safe(df[date])
            df[amt]  = _to_amount_safe(df[amt])
            df[cid]  = _cast_client_code(df[cid])

            part = df[[date, cat, amt, cur, cid]].rename(
                columns={date: "date", cat: "category", amt: "amount", cur: "currency", cid: "client_code"}
            ).dropna(subset=["client_code"])

            tx_frames.append(part)
            handled += 1

        elif is_transfers:
            # Нормализация ожидаемых столбцов для transfers
            date = coalesce_col(df, ["date", "дата"])
            typ  = coalesce_col(df, ["type", "тип"])
            dire = coalesce_col(df, ["direction", "dir", "направление"])
            amt  = coalesce_col(df, ["amount", "sum", "amount_kzt", "amt", "value", "сумма"])
            cur  = coalesce_col(df, ["currency", "валюта", "curr"], required=False, default="KZT")
            cid  = coalesce_col(df, ["client_code", "clientid", "client_id", "id"])

            df[date] = _to_datetime_safe(df[date])
            df[amt]  = _to_amount_safe(df[amt])
            df[cid]  = _cast_client_code(df[cid])

            part = df[[date, typ, dire, amt, cur, cid]].rename(
                columns={date: "date", typ: "type", dire: "direction", amt: "amount", cur: "currency", cid: "client_code"}
            ).dropna(subset=["client_code"])

            tr_frames.append(part)
            handled += 1

        else:
            skipped += 1
            print(f"— пропускаю (не распознал тип): {path}  колонки={list(df.columns)}")

    # Собираем и сохраняем
    out_tx = DATA_DIR / "transactions.csv"
    out_tr = DATA_DIR / "transfers.csv"

    if tx_frames:
        tx = pd.concat(tx_frames, ignore_index=True)
        # Дедуп по (client_code, date, category, amount, currency)
        tx = tx.drop_duplicates(subset=["client_code", "date", "category", "amount", "currency"])
        tx = tx.sort_values(["client_code", "date"], kind="mergesort")
        tx.to_csv(out_tx, index=False, encoding="utf-8-sig")
        print(f"✅ Записал {len(tx):,} строк → {out_tx}")
    else:
        print("⚠️  Не найдено транзакций (transactions).")

    if tr_frames:
        tr = pd.concat(tr_frames, ignore_index=True)
        tr = tr.drop_duplicates(subset=["client_code", "date", "type", "direction", "amount", "currency"])
        tr = tr.sort_values(["client_code", "date"], kind="mergesort")
        tr.to_csv(out_tr, index=False, encoding="utf-8-sig")
        print(f"✅ Записал {len(tr):,} строк → {out_tr}")
    else:
        print("⚠️  Не найдено переводов (transfers).")

    # Короткая сводка
    print(f"— Файлов обработано: {handled}, пропущено: {skipped}")

if __name__ == "__main__":
    main()