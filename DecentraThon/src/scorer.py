# scorer.py
from __future__ import annotations

import numpy as np
import pandas as pd

# Берём готовые наборы категорий и утилиту топ-категорий
from .utils import TRAVEL_CATS, PREMIUM_BOOST_CATS, ONLINE_CATS, top_categories

# Каталог рекомендуемых продуктов (порядок не важен — оставлен для наглядности)
PRODUCTS = [
    "Карта для путешествий",
    "Премиальная карта",
    "Кредитная карта",
    "Обмен валют",
    "Кредит наличными",
    "Депозит мультивалютный",
    "Депозит сберегательный (с «заморозкой»)",
    "Депозит накопительный",
    "Инвестиции (брокерский счёт)",
    "Золотые слитки",
]

# Правила/эвристики «пользы» для каждого продукта (используются при скоринге)
from .rules import (
    benefit_travel,
    benefit_premium,
    benefit_credit_card,
    benefit_fx,
    benefit_cash_loan,
    benefit_deposit_multi,
    benefit_deposit_savings,
    benefit_deposit_accum,
    benefit_invest,
    benefit_gold,
)


# ============================================================
#                 FEATURE ENGINEERING ПО КЛИЕНТУ
# ============================================================

def compute_features_for_client(
    tx_client: pd.DataFrame,
    tr_client: pd.DataFrame,
    avg_balance: float,
    period: tuple[pd.Timestamp, pd.Timestamp],
) -> dict:
    """
    Считает базовые поведенческие фичи клиента за указанный период [start, end).

    Ожидаемые столбцы:
      - tx_client: date, category, amount, client_code
      - tr_client: date, type, direction, amount, client_code

    Возвращает словарь с числовыми и категориальными признаками:
      TOTAL_SPEND, TRAVEL_SPEND, PREMIUM_SPEND, ONLINE_SPEND,
      FAV_SPEND_SUM, ATM_CNT, TRANSFER_CNT, NEED_RATIO, SHORTFALL,
      FX_TURNOVER, FREE_BAL_3M, FAV_CATS
    """
    start, end = period

    # --- Срез транзакций/переводов по периоду
    txm = tx_client[(tx_client["date"] >= start) & (tx_client["date"] < end)].copy()
    trm = tr_client[(tr_client["date"] >= start) & (tr_client["date"] < end)].copy()

    # --- Общие расходы
    total_spend = float(txm["amount"].sum())

    # --- Расходы по кластерам категорий
    is_travel = txm["category"].isin(TRAVEL_CATS)
    is_premium = txm["category"].isin(PREMIUM_BOOST_CATS)
    is_online = txm["category"].isin(ONLINE_CATS)

    travel_spend = float(txm.loc[is_travel, "amount"].sum())
    premium_spend = float(txm.loc[is_premium, "amount"].sum())
    online_spend = float(txm.loc[is_online, "amount"].sum())

    # --- Топ-3 любимых категорий и их суммарные траты
    fav_cats, cat_series = top_categories(txm, topn=3)
    fav_spend_sum = float(cat_series.loc[fav_cats].sum()) if fav_cats else 0.0

    # --- Снятия в банкоматах и исходящие платежи/переводы
    is_out = (trm["direction"] == "out")
    atm_cnt = int((trm["type"].eq("atm_withdrawal") & is_out).sum())

    transfer_types = trm["type"].isin(["p2p_out", "card_out", "utilities_out"])
    transfer_cnt = int((transfer_types & is_out).sum())

    # --- Денежные потоки / кассовый разрыв
    inflows = float(trm.loc[trm["direction"] == "in", "amount"].sum())
    outflows = float(trm.loc[is_out, "amount"].sum())
    need_ratio = (outflows / inflows) if inflows > 0 else np.inf
    shortfall_abs = max(0.0, outflows - inflows)

    # --- Валютные операции (оборот по fx_buy/fx_sell)
    fx_turnover = float(trm.loc[trm["type"].isin(["fx_buy", "fx_sell"]), "amount"].abs().sum())

    # --- Свободный остаток, доступный к размещению на 3 месяца (упрощённо)
    free_balance_3m = float(avg_balance) * 3.0 / 12.0

    return {
        "TOTAL_SPEND": total_spend,
        "TRAVEL_SPEND": travel_spend,
        "PREMIUM_SPEND": premium_spend,
        "ONLINE_SPEND": online_spend,
        "FAV_SPEND_SUM": fav_spend_sum,
        "ATM_CNT": atm_cnt,
        "TRANSFER_CNT": transfer_cnt,
        "NEED_RATIO": need_ratio,
        "SHORTFALL": shortfall_abs,
        "FX_TURNOVER": fx_turnover,
        "FREE_BAL_3M": free_balance_3m,
        "FAV_CATS": fav_cats,  # список строк
    }


# ============================================================
#                      СКОРИНГ ПРОДУКТОВ
# ============================================================

def score_products(feats: dict, avg_balance: float) -> tuple[str | None, list[tuple[str, float]], dict[str, float]]:
    """
    Подсчитывает «пользу» (эвристический скор) для каждого продукта на основе фич.
    Возвращает:
      - best: строка с названием лучшего продукта (или None),
      - top4: список из 4 (product, score) по убыванию,
      - scores: полный словарь product -> score.
    """
    scores: dict[str, float] = {
        "Карта для путешествий": benefit_travel(feats["TRAVEL_SPEND"]),
        "Премиальная карта": benefit_premium(
            avg_balance,
            feats["TOTAL_SPEND"],
            feats["PREMIUM_SPEND"],
            feats["ATM_CNT"],
            feats["TRANSFER_CNT"],
        ),
        "Кредитная карта": benefit_credit_card(
            feats["FAV_SPEND_SUM"],
            feats["ONLINE_SPEND"],
        ),
        "Обмен валют": benefit_fx(feats["FX_TURNOVER"]),
        "Кредит наличными": benefit_cash_loan(
            feats["NEED_RATIO"],
            feats["SHORTFALL"],
        ),
        "Депозит мультивалютный": benefit_deposit_multi(
            feats["FREE_BAL_3M"],
            feats["FX_TURNOVER"],
        ),
        "Депозит сберегательный (с «заморозкой»)": benefit_deposit_savings(
            feats["FREE_BAL_3M"]
        ),
        "Депозит накопительный": benefit_deposit_accum(feats["FREE_BAL_3M"]),
        "Инвестиции (брокерский счёт)": benefit_invest(feats["FREE_BAL_3M"]),
        "Золотые слитки": benefit_gold(feats["FREE_BAL_3M"]),
    }

    # Топ-4 по убыванию
    top4 = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:4]
    best = top4[0][0] if top4 else None
    return best, top4, scores