# Деплой функции загрузки публикаций (fn_ingest) — 15 минут в консоли

Наш ключ Functions не деплоит (нужен IAM), поэтому деплоишь ты через консоль — как `fn-verify`.

## Шаги

1. **Консоль Yandex Cloud** → каталог `<идентификатор каталога — см. .secrets>` → **Cloud Functions** → **Создать функцию** (имя `fn-ingest`).
2. **Создать версию:**
   - Среда выполнения: **`python311`**
   - Способ: **Редактор кода** (или ZIP из этой папки).
   - Файлы: `index.py` и `requirements.txt` из `cloud_slice/fn_ingest/`.
   - **Точка входа:** `index.handler`
   - Таймаут: **30 с**, память: **128 МБ**.
3. **Переменные окружения** (те же, что у `fn-verify`):
   - `AWS_ACCESS_KEY_ID` = значение из `.secrets`
   - `AWS_SECRET_ACCESS_KEY` = значение из `.secrets`
   - `BUCKET` = `loess-results`
4. **Сохранить** версию.
5. **Сделать публичной:** вкладка функции → «Обзор» → **«Публичный доступ»** → включить.
6. **Скопировать URL для вызова** (`https://functions.yandexcloud.net/d4...`) — **пришли его мне**, я подставлю в страницу загрузки.

CORS на бакете уже настроен (PUT/GET/HEAD) — вручную ничего не нужно.

## Проверка после деплоя
```bash
# список задач (там уже есть тестовая — Velichko_1997)
curl "https://functions.yandexcloud.net/<ID>?action=jobs"

# выдать ссылку на загрузку
curl -X POST "https://functions.yandexcloud.net/<ID>" -H "Content-Type: application/json" \
  -d '{"action":"create","filename":"test.pdf","user":"artyom"}'
```

## Как работает весь путь

| Шаг | Кто делает | Где |
|---|---|---|
| 1. Пользователь грузит PDF | `upload.html` → `fn_ingest` выдаёт ссылку → браузер льёт файл **напрямую** в хранилище | облако |
| 2. Файл встаёт в очередь | `fn_ingest` (`action=enqueue`) → `uploads/<job>/status.json` | облако |
| 3. Обработка | **`57_process_uploads.py`** — OCR (Vision) → извлечение (те же модели) → привязка страниц → геокод | локально |
| 4. Предложения | `uploads/<job>/proposals.json`, статус `ready` | облако |
| 5. Проверка | `upload.html` — «Принять / Отклонить» + комментарий → `decisions.json` | облако |
| 6. На карту | **`58_merge_approved.py`** дописывает принятые в `records_clean_geo_v2.xlsx` → пересборка карты | локально |

Обработчик запускается разово (`python 57_process_uploads.py`) или дежурит: `WATCH=1 python 57_process_uploads.py`.

## После деплоя — подставить URL в страницу
```bash
INGEST_API="https://functions.yandexcloud.net/<ID>" python build_upload_page.py
```
