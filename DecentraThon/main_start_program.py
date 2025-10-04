# main.py
import os
import sys
import socket
import subprocess
import webbrowser
from pathlib import Path

def install_requirements():
    """Ставит зависимости, если их нет"""
    try:
        import pandas, streamlit  # Попробуем импортировать ключевые пакеты
        print("✅ Зависимости уже установлены.")
    except ImportError:
        print("📦 Устанавливаю зависимости...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])

def run_app():
    """Запуск твоего Streamlit-приложения"""
    os.system(f"{sys.executable} -m streamlit run app.py")

if __name__ == "__main__":
    install_requirements()
    run_app()
ROOT = Path(__file__).resolve().parent
APP = ROOT / "app.py"
REQ = ROOT / "requirements.txt"

def find_free_port(start=8503, limit=20):
    for p in range(start, start+limit):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise RuntimeError("Свободный порт не найден")

def ensure_requirements():
    if not REQ.exists():
        return
    print("→ Проверяю зависимости (pip install -r requirements.txt)…")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", str(REQ)])

def run_streamlit(port: int):
    cmd = [
        sys.executable, "-m", "streamlit", "run", str(APP),
        "--server.port", str(port),
        "--server.headless", "true",
        "--browser.gatherUsageStats", "false",
    ]
    print("→ Запускаю Streamlit:", " ".join(cmd))
    # открываем браузер сразу
    url = f"http://localhost:{port}"
    try:
        webbrowser.open_new_tab(url)
    except Exception:
        pass
    # ведём процесс в текущем окне (ctrl+C для выхода)
    subprocess.call(cmd)

if __name__ == "__main__":
    if not APP.exists():
        print("Не найден app.py рядом с main.py")
        sys.exit(1)

    ensure_requirements()
    port = find_free_port(8503)
    print(f"✅ Готово! Откроется на: http://localhost:{port}")
    run_streamlit(port)