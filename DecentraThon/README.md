# Case 1 — Персонализированные пуш-уведомления (MVP)

##
1) Грузит `clients.csv`, `transactions.csv` (`transaction.csv` тоже ок) и `transfers.csv`.
2) Считает выгоду по продуктам по простым объяснимым формулам.
3) Выбирает лучший продукт.
4) Генерирует персональное пуш-уведомление через LLM (GPT), строго под TOV.
5) Пишет результат в `output/submission.csv`.

## Быстрый старт
```bash
python -m venv .venv
# mac/linux:
. .venv/bin/activate
# windows:
# .\.venv\Scripts\activate

pip install -r requirements.txt
# .env с OPENAI_API_KEY
python -m src.pipeline_llm