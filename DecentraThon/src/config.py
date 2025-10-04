# src/config.py
from dataclasses import dataclass

@dataclass(frozen=True)
class BenefitConfig:
    """
    Конфигурация для расчёта выгодности продуктов.
    Все параметры вынесены в константы для удобства настройки.
    """

    # --- Кэшбэк по тревел-карте ---
    travel_cb_rate: float = 0.04              # 4% кэшбэк на тревел-категории
    travel_cb_monthly_cap: float = 15_000.0   # Максимальный кэшбэк за месяц

    # --- Премиальная карта ---
    premium_base_cb_low: float = 0.02         # Базовый кэшбэк при остатке < 2 млн
    premium_base_cb_high: float = 0.04        # Базовый кэшбэк при остатке ≥ 2 млн
    premium_high_balance_kzt: float = 2_000_000.0
    premium_fee_savings_atm_per_tx: float = 300.0    # Экономия за снятие в банкомате
    premium_fee_savings_transfer_per_tx: float = 100.0 # Экономия за перевод
    premium_cb_monthly_cap: float = 25_000.0   # Лимит кэшбэка в месяц

    # --- Кредитная карта ---
    cc_fav_rate: float = 0.10                 # Кэшбэк по топ-категориям
    cc_online_rate: float = 0.10              # Кэшбэк по онлайн-тратам
    cc_cb_monthly_cap: float = 30_000.0       # Лимит кэшбэка в месяц

    # --- Валюта и кредит наличными ---
    fx_spread_saving_rate: float = 0.005      # Экономия от обмена валюты
    cash_loan_need_ratio: float = 1.5         # Порог ratio для кредита наличными

    # --- Вклады/инвестиции/золото ---
    savings_high_rate: float = 0.13   # Годовая ставка (сбер. вклад)
    savings_accum_rate: float = 0.10  # Годовая ставка (накопительный)
    multi_curr_rate: float = 0.07     # Годовая ставка (мультивалютный)
    invest_util_rate: float = 0.003   # Условная полезность инвестиций
    gold_util_rate: float = 0.002     # Условная полезность золота


# Глобальный объект конфигурации
CFG = BenefitConfig()