# rules.py
from __future__ import annotations

from typing import Final
from .config import CFG

# -------------------------------------------------------------------
# ВСПОМОГАТЕЛЬНОЕ
# -------------------------------------------------------------------

def _cap(value: float, cap_value: float) -> float:
    """Ограничивает значение сверху (и не даёт уйти в отрицательные из-за шумов)."""
    if value <= 0:
        return 0.0
    return min(value, cap_value)


# -------------------------------------------------------------------
# ЭВРИСТИКИ «ПОЛЬЗЫ» ДЛЯ ПРОДУКТОВ
# (возвращают положительную «оценку выгоды» в KZT-эквиваленте)
# -------------------------------------------------------------------

def benefit_travel(travel_spend: float) -> float:
    """
    Тревел-карта: фиксированный кешбэк на суммы из travel-категорий, с месячным капом.
    """
    return _cap(travel_spend * CFG.travel_cb_rate, CFG.travel_cb_monthly_cap)


def benefit_premium(
    avg_balance: float,
    base_spend: float,
    premium_spend: float,
    atm_cnt: int,
    transfer_cnt: int,
) -> float:
    """
    Премиальная карта:
      - базовый кешбэк по всему обороту (ставка зависит от среднего остатка),
      - повышенный кешбэк по premium-категориям,
      - экономия на комиссиях за ATM/переводы,
      - общий кап на кешбэк.
    """
    base_rate: Final[float] = (
        CFG.premium_base_cb_high
        if avg_balance >= CFG.premium_high_balance_kzt
        else CFG.premium_base_cb_low
    )

    base_cb = base_rate * max(base_spend, 0.0)
    boost_cb = 0.04 * max(premium_spend, 0.0)

    # экономия комиссий (не капим — это не кешбэк, а сервисная выгода)
    fees_saved = max(atm_cnt, 0) * CFG.premium_fee_savings_atm_per_tx \
               + max(transfer_cnt, 0) * CFG.premium_fee_savings_transfer_per_tx

    return _cap(base_cb + boost_cb, CFG.premium_cb_monthly_cap) + max(fees_saved, 0.0)


def benefit_credit_card(fav_spend_sum: float, online_spend: float) -> float:
    """
    Кредитная карта: повышенный кешбэк в топ-категориях + на онлайн-сервисы, общий кап.
    """
    cb = CFG.cc_fav_rate * max(fav_spend_sum, 0.0) + CFG.cc_online_rate * max(online_spend, 0.0)
    return _cap(cb, CFG.cc_cb_monthly_cap)


def benefit_fx(fx_turnover: float) -> float:
    """
    Обмен валют: экономия на спреде (приблизительная), без капа.
    """
    return max(fx_turnover, 0.0) * CFG.fx_spread_saving_rate


def benefit_cash_loan(need_ratio: float, shortfall_abs: float) -> float:
    """
    Кредит наличными: «польза» проявляется при кассовом разрыве (outflows > inflows).
    Если потребность превышает порог, оцениваем как 1% от дефицита (эвристика).
    """
    if need_ratio >= CFG.cash_loan_need_ratio and shortfall_abs > 0:
        return 0.01 * shortfall_abs
    return 0.0


def benefit_deposit_savings(free_balance_3m: float) -> float:
    """
    Сберегательный вклад (без снятий): высокая ставка, считаем квартальный эффект.
    """
    return max(free_balance_3m, 0.0) * (CFG.savings_high_rate / 4.0)


def benefit_deposit_accum(free_balance_3m: float) -> float:
    """
    Накопительный вклад (с пополнениями): ставка ниже, считаем квартальный эффект.
    """
    return max(free_balance_3m, 0.0) * (CFG.savings_accum_rate / 4.0)


def benefit_deposit_multi(free_balance_3m: float, fx_turnover: float) -> float:
    """
    Мультивалютный вклад: проценты + небольшая добавка за FX-активность (удобство/экономия).
    """
    return max(free_balance_3m, 0.0) * (CFG.multi_curr_rate / 4.0) + 0.001 * max(fx_turnover, 0.0)


def benefit_invest(free_balance_3m: float) -> float:
    """
    Инвестиции: упрощённая «полезность» от возможности разместить часть свободного остатка.
    """
    return max(free_balance_3m, 0.0) * CFG.invest_util_rate


def benefit_gold(free_balance_3m: float) -> float:
    """
    Золото: консервативная «полезность» как средство диверсификации/сбережения.
    """
    return max(free_balance_3m, 0.0) * CFG.gold_util_rate