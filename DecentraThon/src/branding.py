# src/branding.py
from __future__ import annotations

from pathlib import Path
import base64
import streamlit as st

# --- Пути к логотипам (положите сюда свои файлы) ---
ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
LOGO_BIG_PATH = ASSETS_DIR / "bigLogo.svg"   # горизонтальный большой логотип
LOGO_SMALL_PATH = ASSETS_DIR / "logo.svg"    # компактный знак/иконка

# --- Брендовые константы (подгоните под брендбук) ---
BRAND_NAME    = "bcc.kz"
BRAND_SHORT   = "BCC"
BRAND_PRIMARY = "#416b3a"   # зелёный
BRAND_ACCENT  = "#d4a24c"   # «золото»
BRAND_TEXT    = "#10231a"   # тёмно-зелёный

def _guess_mime(p: Path) -> str:
    """
    Корректно определяет MIME по расширению.
    SVG должен быть 'image/svg+xml' (а не 'image/svg'),
    JPEG — 'image/jpeg', PNG — 'image/png'.
    """
    ext = p.suffix.lower()
    if ext in (".svg",):
        return "image/svg+xml"
    if ext in (".jpg", ".jpeg"):
        return "image/jpeg"
    if ext == ".png":
        return "image/png"
    # безопасный фолбэк — пусть будет svg+xml
    return "image/svg+xml"

@st.cache_resource(show_spinner=False)
def _img_to_data_uri(p: Path) -> str | None:
    """
    Загружает файл и возвращает data URI. Закэшировано на сессию.
    Если файл отсутствует/битый — вернёт None.
    """
    try:
        data = p.read_bytes()
        b64 = base64.b64encode(data).decode("ascii")
        mime = _guess_mime(p)
        return f"data:{mime};base64,{b64}"
    except Exception:
        return None

def render_brand_header() -> None:
    """
    Рендерит верхнюю шапку: слева — большой логотип + название, справа — маленький знак.
    На узких экранах маленький скрывается, большой чуть уменьшается.
    """
    big = _img_to_data_uri(LOGO_BIG_PATH)
    small = _img_to_data_uri(LOGO_SMALL_PATH)

    # если не нашли изображение — покажем текстовый фолбэк
    logo_left = (
        f'<img src="{big}" alt="BCC" class="logo-big" />'
        if big else f'<div class="logo-fallback" aria-label="{BRAND_SHORT}">{BRAND_SHORT}</div>'
    )
    logo_right = f'<img src="{small}" alt="BCC mark" class="logo-small" />' if small else ""

    st.markdown(
        f"""
<style>
/* Контейнер шапки */
.bcc-header {{
  display:flex; align-items:center; justify-content:space-between;
  gap:12px; margin: 4px 0 16px 0; padding: 8px 0;
  border-bottom: 1px solid #e9ecef;
}}

/* Большой логотип слева */
.logo-big {{
  height: 36px; max-width: 520px; object-fit: contain; display:block;
  image-rendering: -webkit-optimize-contrast;
}}

/* Маленький логотип справа (как «иконка приложения») */
.logo-small {{
  height: 28px; width:auto; object-fit: contain; display:block;
  filter: drop-shadow(0 1px 1px rgba(0,0,0,.06));
}}

/* Текстовое название рядом с большим логотипом */
.brand-title {{
  font-weight: 700; font-size: 20px; color: {BRAND_PRIMARY};
  margin-left: 8px; letter-spacing: .2px;
}}

/* Фолбэк, если нет картинок */
.logo-fallback {{
  font-weight: 800; font-size: 20px; color: {BRAND_PRIMARY};
}}

/* Адаптивность */
@media (max-width: 680px) {{
  .logo-small {{ display: none; }}
  .logo-big   {{ height: 30px; }}
}}
</style>

<div class="bcc-header" role="banner">
  <div style="display:flex; align-items:center; gap:10px;">
    {logo_left}
    <span class="brand-title">{BRAND_NAME}</span>
  </div>
  <div>
    {logo_right}
  </div>
</div>
""",
        unsafe_allow_html=True,
    )