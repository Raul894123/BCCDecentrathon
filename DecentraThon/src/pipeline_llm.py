# src/pipeline_llm.py
from __future__ import annotations

import os
import re
from typing import Dict, List, Tuple, Optional

import pandas as pd

from .metrics import date_coverage, push_length_stats, push_quality_summary
from .utils import read_data, last_month_span, month_word, kzt
from .scorer import compute_features_for_client, score_products
from .pushgen_llm import generate_personalized_push, ensure_cta

# ----------------------------
#   ПАПКИ/ПУТИ
# ----------------------------
BASE_DIR = os.path.dirname(os.path.dirname(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
OUT_DIR = os.path.join(BASE_DIR, "output")
os.makedirs(OUT_DIR, exist_ok=True)

# ----------------------------
#   КАЛЬКУЛЯТОР ВЫГОДЫ
# ----------------------------
def estimate_benefit(
    product: str,
    tx_client: pd.DataFrame,
    feats: Dict,
    avg_bal: float,
    period: Tuple[pd.Timestamp, pd.Timestamp],
) -> Tuple[float, Dict]:
    """
    Упрощённые расчёты «ожидаемой выгоды» (в духе ТЗ).
    Возвращает (benefit_num, meta), где meta — пояснения для прозрачности.
    """
    start, end = period
    tx_last = tx_client[(tx_client["date"] >= start) & (tx_client["date"] < end)].copy()
    total_spend = float(tx_last["amount"].sum())

    # Эвристики/каппы
    TRAVEL_CB = 0.04
    TRAVEL_CAP = 15_000.0
    PREMIUM_TIER = 0.02 if avg_bal < 1_000_000 else (0.03 if avg_bal < 3_000_000 else 0.04)
    ATM_FEE_SAVED = 300.0
    TRANSFER_FEE_SAVED = 200.0
    PREMIUM_CAP = 20_000.0
    CC_CAP = 20_000.0

    travel_sum  = float(feats.get("TRAVEL_SPEND", 0.0))
    online_sum  = float(feats.get("ONLINE_SPEND", 0.0))
    premium_sum = float(feats.get("PREMIUM_SPEND", 0.0))

    if product == "Карта для путешествий":
        cb = min(TRAVEL_CB * travel_sum, TRAVEL_CAP)
        return cb, {
            "total_spend": total_spend,
            "calc": f"{TRAVEL_CB*100:.0f}% × travel ({int(travel_sum):,}) → cap {int(TRAVEL_CAP):,}".replace(",", " ")
        }

    if product == "Премиальная карта":
        base_cb   = PREMIUM_TIER * total_spend
        boost_cb  = 0.04 * premium_sum
        saved_fees = feats.get("ATM_CNT", 0) * ATM_FEE_SAVED + feats.get("TRANSFER_CNT", 0) * TRANSFER_FEE_SAVED
        benefit = min(base_cb + boost_cb + saved_fees, PREMIUM_CAP)
        return benefit, {
            "tier": PREMIUM_TIER, "total_spend": total_spend,
            "premium_sum": premium_sum, "saved_fees": saved_fees
        }

    if product == "Кредитная карта":
        cat_sum = tx_last.groupby("category")["amount"].sum().sort_values(ascending=False)
        top3_sum = float(cat_sum.head(3).sum())
        top3_names = ", ".join(cat_sum.head(3).index.tolist())
        benefit = min(0.10 * top3_sum + 0.10 * online_sum, CC_CAP)
        return benefit, {"top3_sum": top3_sum, "online_sum": online_sum, "top3_names": top3_names}

    # Прочие продукты — без явной формулы
    return 0.0, {"total_spend": total_spend}

# ----------------------------
#   ВАЛИДАЦИЯ ТЕКСТОВ PUSH
# ----------------------------
MAX_PUSH_LEN = 220  # по ТЗ ориентир 180–220

def _push_ok(text: str) -> Tuple[bool, List[str]]:
    """Базовая проверка текста пуша."""
    issues: List[str] = []
    if not isinstance(text, str) or not text.strip():
        issues.append("пустой текст")
        return False, issues
    if len(text) > MAX_PUSH_LEN:
        issues.append(f"длина {len(text)} > {MAX_PUSH_LEN}")
    if text.count("!") > 1:
        issues.append("больше одного '!'")
    # простой чек CTA
    if not re.search(r"(Открыть|Оформить|Подключить|Подключите|Посмотреть|Настроить)\b", text, re.IGNORECASE):
        issues.append("нет явного CTA (Открыть/Оформить/Посмотреть/Настроить/Подключить)")
    return (len(issues) == 0), issues

def validate_and_report(clients_df: pd.DataFrame, out_df: pd.DataFrame) -> None:
    """
    Проверки соответствия clients ↔ pushes и базовая проверка текстов.
    Печатает короткий отчёт в консоль.
    """
    uniq_clients = set(pd.to_numeric(clients_df["client_code"], errors="coerce").dropna().astype(int))
    uniq_pushes  = set(pd.to_numeric(out_df["client_code"], errors="coerce").dropna().astype(int))

    missing = sorted(uniq_clients - uniq_pushes)
    extra   = sorted(uniq_pushes - uniq_clients)
    dups    = out_df["client_code"][out_df["client_code"].duplicated(keep=False)].tolist()

    total_clients = len(uniq_clients)
    total_pushes  = out_df["client_code"].nunique()

    bad_rows: List[Tuple[int, List[str]]] = []
    for _, r in out_df.iterrows():
        ok, issues = _push_ok(r["push_notification"])
        if not ok:
            bad_rows.append((int(r["client_code"]), issues))

    if total_clients == total_pushes and not missing and not dups:
        print(f"✅ {total_clients} клиентов → {total_pushes} пушей → всё ок")
    else:
        print(f"⚠️  {total_clients} клиентов → {total_pushes} пушей")
    if missing:
        print("   ▸ нет пушей для client_code:", ", ".join(map(str, missing[:20])), ("… +" + str(len(missing)-20)) if len(missing)>20 else "")
    if dups:
        print("   ▸ дубликаты client_code в выходе:", dups[:20], ("… +" + str(len(dups)-20)) if len(dups)>20 else "")
    if extra:
        print("   ▸ есть client_code, которых нет в clients.csv:", extra[:20])

    if bad_rows:
        print("   ▸ тексты, требующие правки (первые 10):")
        for cid, issues in bad_rows[:10]:
            print(f"     - client {cid}: {', '.join(issues)}")
    else:
        print("🧪 Тексты пушей проходят базовые проверки (≤220, ≤1 '!', есть CTA).")

def save_and_validate(clients_df: pd.DataFrame, rows: List[Tuple[int, str, str]]) -> None:
    """
    Сохраняет submission.csv и печатает отчёт по качеству (длины/CTA/coverage дат).
    rows: [(client_code, product, push_text)]
    """
    out = pd.DataFrame(rows, columns=["client_code","product","push_notification"])
    out_path = os.path.join(OUT_DIR, "submission.csv")
    out.to_csv(out_path, index=False, encoding="utf-8-sig")
    print("Saved:", out_path)

    # ----- Валидация набора и текстов -----
    validate_and_report(clients_df, out)

    # ----- Метрики качества пушей -----
    push_stats = push_length_stats(out)
    push_stats_path = os.path.join(OUT_DIR, "push_stats.csv")
    push_stats.to_csv(push_stats_path, index=False, encoding="utf-8-sig")

    summary = push_quality_summary(push_stats)
    summary_path = os.path.join(OUT_DIR, "push_quality_summary.json")
    pd.Series(summary, dtype="object").to_json(summary_path, force_ascii=False, indent=2)

    print("— Push Quality —")
    print(f"   Кол-во пушей: {summary['count']}")
    print(f"   Средняя длина: {summary['avg_length']:.1f} символов")
    print(f"   Доля в диапазоне 180–220: {summary['pct_in_range']*100:.1f}%")
    print(f"   Доля с CTA: {summary['pct_has_cta']*100:.1f}%")
    print(f"   Доля уникальности CTA: {summary['unique_cta_ratio']*100:.1f}%")
    if summary["top_cta"]:
        top_str = ", ".join([f"{k}: {v}" for k, v in summary["top_cta"].items()])
        print(f"   Топ-CTA: {top_str}")

    # ----- Coverage дат по исходным датасетам -----
    try:
        _, txx, trr = read_data(DATA_DIR)
        cov_tx = date_coverage(txx)
        cov_tr = date_coverage(trr)
        cov_df = pd.DataFrame(
            [
                {"table": "transactions", **cov_tx},
                {"table": "transfers", **cov_tr},
            ]
        )
        cov_path = os.path.join(OUT_DIR, "date_coverage.csv")
        cov_df.to_csv(cov_path, index=False, encoding="utf-8-sig")
        print("— Date Coverage —")
        for _, r in cov_df.iterrows():
            print(f"   {r['table']}: {r['ok']}/{r['total']} → {r['coverage']*100:.1f}%")
    except Exception as e:
        print("Coverage calc error:", e)

# ----------------------------
#   ХЕЛПЕРЫ СБОРКИ КОНТЕКСТА
# ----------------------------
def _build_demo(row: pd.Series) -> Dict:
    """Демография из clients.csv — мягко влияет на тон пуша."""
    return {
        "status": row.get("status"),
        "age": int(row["age"]) if "age" in row and str(row["age"]).isdigit() else None,
        "city": row.get("city"),
    }

def _build_ctx_map(feats: Dict, avg_bal: float, benefit_num: float, meta: Dict) -> Dict:
    """Контекст для генерации: и форматированные строки, и «сырые» числа."""
    return {
        # форматированные
        "travel_sum":  kzt(feats["TRAVEL_SPEND"]),
        "avg_balance": kzt(avg_bal),
        "premium_sum": kzt(feats["PREMIUM_SPEND"]),
        "online_sum":  kzt(feats["ONLINE_SPEND"]),
        "fx_turnover": kzt(feats["FX_TURNOVER"]),
        "shortfall":   kzt(feats["SHORTFALL"]),
        "free_bal":    kzt(feats["FREE_BAL_3M"]),
        "fav": ", ".join(feats["FAV_CATS"]) if feats["FAV_CATS"] else "-",
        "atm_cnt":     feats["ATM_CNT"],
        "transfer_cnt":feats["TRANSFER_CNT"],
        # числовые
        "travel_sum_num":  float(feats["TRAVEL_SPEND"]),
        "avg_balance_num": float(avg_bal),
        "premium_sum_num": float(feats["PREMIUM_SPEND"]),
        "online_sum_num":  float(feats["ONLINE_SPEND"]),
        "fx_turnover_num": float(feats["FX_TURNOVER"]),
        "shortfall_num":   float(feats["SHORTFALL"]),
        "free_bal_num":    float(feats["FREE_BAL_3M"]),
        # выгода/объяснимость
        "benefit": kzt(benefit_num),
        "benefit_num": float(benefit_num),
        "top3_names": meta.get("top3_names", ""),
    }

# ----------------------------
#   MAIN (CLI-режим)
# ----------------------------
if __name__ == "__main__":
    # 0) читаем данные
    clients, tx, tr = read_data(DATA_DIR)

    rows: List[Tuple[int, str, str]] = []
    for _, row in clients.iterrows():
        cid = int(row["client_code"])
        name = row.get("name", "Клиент")

        # колонка баланса в датасете — строчными
        val = row.get("avg_monthly_balance_kzt")
        avg_bal = float(val) if pd.notna(val) else 0.0

        tx_client = tx[tx["client_code"] == cid].copy()
        tr_client = tr[tr["client_code"] == cid].copy()

        dspan_tx = last_month_span(tx_client["date"])
        dspan_tr = last_month_span(tr_client["date"])

        # 1) нет данных ни в транзакциях, ни в переводах → дефолтный продукт
        if dspan_tx[0] is None and dspan_tr[0] is None:
            product = "Премиальная карта"
            month = "этом месяце"

            feats = {
                "TOTAL_SPEND": 0.0,
                "TRAVEL_SPEND": 0.0, "PREMIUM_SPEND": 0.0, "ONLINE_SPEND": 0.0,
                "FX_TURNOVER": 0.0, "SHORTFALL": 0.0, "FREE_BAL_3M": avg_bal * 3 / 12,
                "FAV_CATS": [], "ATM_CNT": 0, "TRANSFER_CNT": 0,
            }
            benefit_num, meta = 0.0, {}
            demo = _build_demo(row)
            ctx_map = _build_ctx_map(feats, avg_bal, benefit_num, meta)

            push = generate_personalized_push(product, name, month, ctx_map, demo, max_len=MAX_PUSH_LEN)
            push = ensure_cta(product, push)  # гарантируем CTA
            rows.append((cid, product, push))
            continue

        # 2) выбираем свежий период: transactions vs transfers
        end_tx = dspan_tx[1] if dspan_tx[1] is not None else pd.Timestamp.min
        end_tr = dspan_tr[1] if dspan_tr[1] is not None else pd.Timestamp.min
        start, end = (dspan_tx if end_tx >= end_tr else dspan_tr)

        month = month_word(end - pd.offsets.Day(1))

        # 3) фичи клиента за период
        feats = compute_features_for_client(tx_client, tr_client, avg_bal, (start, end))

        # 4) выбор продукта
        best, top4, scores = score_products(feats, avg_bal)

        # 5) оценка выгоды
        benefit_num, meta = estimate_benefit(best, tx_client, feats, avg_bal, (start, end))

        # 6) демография + контекст для генерации
        demo = _build_demo(row)
        ctx_map = _build_ctx_map(feats, avg_bal, benefit_num, meta)

        # 7) генерация пуша
        push = generate_personalized_push(best, name, month, ctx_map, demo, max_len=MAX_PUSH_LEN)
        push = ensure_cta(best, push)  # гарантируем CTA
        rows.append((cid, best, push))

    # 8) сохранение + отчёты/метрики
    save_and_validate(clients, rows)