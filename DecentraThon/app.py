# app.py
# ------------------------------------------------------------
# Streamlit-приложение: персонализированные пуш-уведомления
# Брендинг BCC (шапка + стили), нативные уведомления (macOS/Windows),
# генерация текста пушей и метрики качества.
# ------------------------------------------------------------

import os
import sys
import textwrap
import subprocess
from pathlib import Path

import pandas as pd
import streamlit as st

# --- проектные импорты ---
from src.utils import read_data, month_word, last_month_span, kzt
from src.scorer import compute_features_for_client, score_products
from src.pushgen_llm import generate_personalized_push, ensure_cta
from src.pipeline_llm import estimate_benefit, OUT_DIR, DATA_DIR

# =========================
#          BRANDING
# =========================

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
LOGO_BIG_PATH = ASSETS_DIR / "bigLogo.svg"   # горизонтальный логотип
LOGO_SMALL_PATH = ASSETS_DIR / "logo.svg"    # иконка

BRAND_NAME = "bcc.kz"
BRAND_PRIMARY = "#146b3a"
BRAND_ACCENT = "#d4a24c"

PAGE_ICON = str(LOGO_SMALL_PATH) if LOGO_SMALL_PATH.exists() else None
st.set_page_config(page_title="bcc.kz • Персональные пуши", page_icon=PAGE_ICON, layout="wide")

def render_brand_header() -> None:
    """Шапка: слева большой логотип+название, справа маленькая иконка."""
    left, right = st.columns([6, 1])
    with left:
        row = st.columns([0.2, 0.8])
        with row[0]:
            if LOGO_BIG_PATH.exists():
                st.image(str(LOGO_BIG_PATH), use_column_width=True)
            else:
                st.markdown(
                    f"<div style='font-weight:800;font-size:22px;color:{BRAND_PRIMARY}'>BCC</div>",
                    unsafe_allow_html=True,
                )
        with row[1]:
            st.markdown(
                f"<div style='font-weight:700;font-size:22px;color:{BRAND_PRIMARY};padding-top:6px'>{BRAND_NAME}</div>",
                unsafe_allow_html=True,
            )
    with right:
        if LOGO_SMALL_PATH.exists():
            st.markdown("<div style='display:flex;justify-content:flex-end;'>", unsafe_allow_html=True)
            st.image(str(LOGO_SMALL_PATH), width=32)
            st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("<hr style='border:none;border-top:1px solid #e9ecef;margin:6px 0 16px 0'>", unsafe_allow_html=True)

# Лёгкий CSS-твик
st.markdown(
    f"""
    <style>
      .stButton>button {{ background:{BRAND_PRIMARY}!important; border-color:{BRAND_PRIMARY}!important; }}
      .stButton>button:hover {{ opacity:.95; }}
      .block-container h2 {{ border-left: 4px solid {BRAND_ACCENT}; padding-left: 8px; }}
    </style>
    """,
    unsafe_allow_html=True,
)

render_brand_header()

# =========================
#       NOTIFICATIONS
# =========================

try:
    from win10toast import ToastNotifier  # опционально (для Windows)
except Exception:
    ToastNotifier = None

def _as_apple_str(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")

def notify_macos(title: str, text: str) -> bool:
    if sys.platform != "darwin":
        return False
    script = f'display notification "{_as_apple_str(text)}" with title "{_as_apple_str(title)}"'
    try:
        subprocess.run(["osascript", "-e", script], check=True)
        return True
    except Exception:
        return False

def notify_windows(title: str, text: str, duration: int = 5) -> bool:
    if sys.platform != "win32":
        return False
    try:
        if ToastNotifier is not None:
            ToastNotifier().show_toast(title, text, duration=duration, threaded=False)
            return True
    except Exception:
        pass
    ps = rf'''
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms
$ni = New-Object System.Windows.Forms.NotifyIcon
$ni.Icon = [System.Drawing.SystemIcons]::Information
$ni.BalloonTipTitle = "{title.replace('"','`"')}"
$ni.BalloonTipText  = "{text.replace('"','`"')}"
$ni.Visible = $true
$ni.ShowBalloonTip({duration*1000})
Start-Sleep -Milliseconds {duration*1000}
$ni.Dispose()
'''
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)
        return True
    except Exception:
        return False

def browser_notify(title: str, text: str) -> None:
    st.components.v1.html(
        textwrap.dedent(
            f"""
            <script>
              (async () => {{
                try {{
                  const perm = await Notification.requestPermission();
                  if (perm === "granted") {{
                    new Notification({title!r}, {{ body: {text!r} }});
                  }} else {{
                    alert("Разрешите уведомления в браузере, чтобы показать пуш.");
                  }}
                }} catch(e) {{
                  alert("Браузер запретил уведомления: " + e);
                }}
              }})();
            </script>
            """
        ),
        height=0,
    )

def notify(title: str, text: str) -> None:
    ok = False
    if sys.platform == "darwin":
        ok = notify_macos(title, text)
    elif sys.platform == "win32":
        ok = notify_windows(title, text)
    if not ok:
        browser_notify(title, text)

# =========================
#         DATA LAYER
# =========================

@st.cache_data(show_spinner=False)
def load_all():
    clients, tx, tr = read_data(DATA_DIR)
    clients["client_code"] = pd.to_numeric(clients["client_code"], errors="coerce").astype("Int64")
    tx["client_code"] = pd.to_numeric(tx["client_code"], errors="coerce").astype("Int64")
    tr["client_code"] = pd.to_numeric(tr["client_code"], errors="coerce").astype("Int64")
    return (
        clients.dropna(subset=["client_code"]),
        tx.dropna(subset=["client_code"]),
        tr.dropna(subset=["client_code"]),
    )

def build_ctx(name, avg_bal, feats, benefit_num, meta):
    return {
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
        "travel_sum_num":  float(feats["TRAVEL_SPEND"]),
        "avg_balance_num": float(avg_bal),
        "premium_sum_num": float(feats["PREMIUM_SPEND"]),
        "online_sum_num":  float(feats["ONLINE_SPEND"]),
        "fx_turnover_num": float(feats["FX_TURNOVER"]),
        "shortfall_num":   float(feats["SHORTFALL"]),
        "free_bal_num":    float(feats["FREE_BAL_3M"]),
        "benefit":     kzt(benefit_num),
        "benefit_num": float(benefit_num),
        "top3_names":  meta.get("top3_names", ""),
    }

def compute_one(clients, tx, tr, cid: int):
    row = clients[clients["client_code"] == cid].iloc[0]
    name = row.get("name", "Клиент")
    val = row.get("avg_monthly_balance_kzt")
    avg_bal = float(val) if pd.notna(val) else 0.0

    txc = tx[tx["client_code"] == cid].copy()
    trc = tr[tr["client_code"] == cid].copy()

    dspan_tx = last_month_span(txc["date"])
    dspan_tr = last_month_span(trc["date"])
    end_tx = dspan_tx[1] if dspan_tx[1] is not None else pd.Timestamp.min
    end_tr = dspan_tr[1] if dspan_tr[1] is not None else pd.Timestamp.min
    start, end = dspan_tx if end_tx >= end_tr else dspan_tr

    if start is None:
        month = "этом месяце"
        best = "Премиальная карта"
        feats = {
            "TRAVEL_SPEND": 0.0, "PREMIUM_SPEND": 0.0, "ONLINE_SPEND": 0.0,
            "FX_TURNOVER": 0.0, "SHORTFALL": 0.0, "FREE_BAL_3M": avg_bal * 3 / 12,
            "FAV_CATS": [], "ATM_CNT": 0, "TRANSFER_CNT": 0,
        }
        benefit_num, meta = 0.0, {}
    else:
        month = month_word(end - pd.offsets.Day(1))
        feats = compute_features_for_client(txc, trc, avg_bal, (start, end))
        best, _, _ = score_products(feats, avg_bal)
        benefit_num, meta = estimate_benefit(best, txc, feats, avg_bal, (start, end))

    demo = {
        "status": row.get("status"),
        "age": int(row["age"]) if "age" in row and str(row["age"]).isdigit() else None,
        "city": row.get("city"),
    }

    ctx = build_ctx(name, avg_bal, feats, benefit_num, meta)
    push = generate_personalized_push(best, name, month, ctx, demo, max_len=220)
    # подстраховка: гарантируем CTA
    push = ensure_cta(best, push, key=str(cid))

    return {"name": name, "avg_bal": avg_bal, "month": month, "best": best, "push": push, "feats": feats, "ctx": ctx}

# =========================
#            UI
# =========================

st.title("💬 Персонализированные пуш-уведомления")

clients, tx, tr = load_all()
left, right = st.columns([1, 2])

with left:
    st.subheader("Клиент")
    client_ids = clients["client_code"].astype(int).tolist()
    cid = st.selectbox("client_code", client_ids, index=0)
    if st.button("🔁 Пересчитать для клиента"):
        st.cache_data.clear()

with right:
    res = compute_one(clients, tx, tr, int(cid))
    st.subheader("Рекомендация")
    st.markdown(f"**Продукт:** {res['best']}")
    st.markdown(f"**Пуш:** {res['push']}")

    if st.button("🔔 Показать нативное уведомление", use_container_width=True):
        notify("Персональный пуш", res["push"])
        st.success("Отправлено (системные уведомления или HTML5-фолбэк)")

    # Top-4 продуктов
    _, _, scores = score_products(res["feats"], res["avg_bal"])
    top4_df = (
        pd.DataFrame({"product": list(scores.keys()), "score": list(scores.values())})
        .sort_values("score", ascending=False).head(4).reset_index(drop=True)
    )
    st.markdown("**Top-4 продуктов**")
    st.dataframe(top4_df, use_container_width=True)

with st.expander("🧠 Почему этот пуш?"):
    ctx = res["ctx"]
    st.markdown(
        f"- Топ-категории: **{ctx.get('top3_names') or ctx.get('fav','-')}**\n"
        f"- Путешествия/Такси: **{ctx['travel_sum']}**; Онлайн-сервисы: **{ctx['online_sum']}**\n"
        f"- Ожидаемая выгода: **≈{ctx['benefit']}** за месяц\n"
        f"- Остаток: **{ctx['avg_balance']}**, свободно за 3 мес: **{ctx['free_bal']}**"
    )

st.divider()

col1, col2, col3 = st.columns([2, 2, 1])

with col1:
    st.subheader("Скачать результат")
    subm_path = os.path.join(OUT_DIR, "submission.csv")
    if os.path.exists(subm_path):
        df = pd.read_csv(subm_path)
        st.download_button(
            "📥 Скачать submission.csv",
            data=df.to_csv(index=False).encode("utf-8-sig"),
            file_name="submission.csv",
            mime="text/csv",
            use_container_width=True,
        )
    else:
        st.info("Файл output/submission.csv пока не найден. Сгенерируйте через кнопку справа.")

with col2:
    st.subheader("Генерация для всех")
    st.caption("Прогоняет весь пайплайн и обновляет output/submission.csv")
    if st.button("🚀 Сгенерировать для всех клиентов", use_container_width=True):
        from src.pipeline_llm import save_and_validate
        rows = []
        for _, r in clients.iterrows():
            ccid = int(r["client_code"])
            rres = compute_one(clients, tx, tr, ccid)
            rows.append((ccid, rres["best"], ensure_cta(rres["best"], rres["push"], key=str(ccid))))
        save_and_validate(clients, rows)
        st.success("Готово! Файл обновлён: output/submission.csv")
        st.cache_data.clear()

with col3:
    st.subheader("Инфо")
    st.metric("Клиентов", len(clients))
    st.metric("Транзакций", len(tx))
    st.metric("Переводов", len(tr))

# === Метрики качества пушей ===
st.header("📊 Качество пушей")
subm_path = os.path.join(OUT_DIR, "submission.csv")
if os.path.exists(subm_path):
    from src.metrics import push_length_stats, push_quality_summary
    subm_df = pd.read_csv(subm_path)
    stats_df = push_length_stats(subm_df)

    c1, c2, c3, c4 = st.columns(4)
    summary = push_quality_summary(stats_df)
    c1.metric("Пушей", summary["count"])
    c2.metric("Средняя длина", f"{summary['avg_length']:.1f}")
    c3.metric("В диапазоне 180–220", f"{summary['pct_in_range']*100:.1f}%")
    c4.metric("Есть CTA", f"{summary['pct_has_cta']*100:.1f}%")

    st.subheader("Распределение длины пушей")
    st.bar_chart(stats_df["length"], use_container_width=True)

    st.subheader("CTA-распределение")
    cta_counts = (
        stats_df["cta_text"].replace("", pd.NA).dropna().value_counts()
        .rename_axis("CTA").reset_index(name="count")
    )
    if len(cta_counts) > 0:
        st.dataframe(cta_counts, use_container_width=True)
    else:
        st.info("CTA не найдены — проверьте шаблоны/LLM.")

    st.subheader("Перечень пушей с флагами качества")
    st.dataframe(stats_df, use_container_width=True)
else:
    st.info("Сначала сгенерируйте файл output/submission.csv.")