# Деплой функции проверки данных (fn_verify) — 15 минут в консоли

Наш ключ Functions не деплоит (нужен IAM), поэтому деплоишь ты через консоль — как делал `fn-extract-text`.

## Шаги

1. **Консоль Yandex Cloud** → каталог `b1ggg591of2vjaik3f9p` → **Cloud Functions** → **Создать функцию** (имя `fn-verify`).
2. **Создать версию:**
   - Среда выполнения: **`python311`**
   - Способ: **Редактор кода** (или ZIP из этой папки).
   - Файлы: вставь `index.py` и `requirements.txt` из `cloud_slice/fn_verify/`.
   - **Точка входа:** `index.handler`
   - Таймаут: 30 с, память: 128 МБ.
3. **Переменные окружения** (те же, что у `fn-extract-text`):
   - `AWS_ACCESS_KEY_ID` = значение из `.secrets`
   - `AWS_SECRET_ACCESS_KEY` = значение из `.secrets`
   - `BUCKET` = `loess-results`
4. **Сохранить** версию.
5. **Сделать публичной** (чтобы карта могла звать без авторизации):
   - Вкладка функции → **«Обзор»** → блок **«Публичная функция»** → включить **«Публичный доступ»**.
   - (или Права доступа → добавить `allUsers` роль `functions.functionInvoker`)
6. **Скопировать URL для вызова** — вид `https://functions.yandexcloud.net/d4xxxxxxxxxxxxxxx` (или `https://d5xxxx.apigw.yandexcloud.net`). **Пришли его мне** — я вставлю в карту.

## Проверка (после деплоя)
```bash
# GET — пусто по новому разрезу
curl "https://functions.yandexcloud.net/<ID>?section_id=test1"
# POST — сохранить правку
curl -X POST "https://functions.yandexcloud.net/<ID>" \
  -H "Content-Type: application/json" \
  -d '{"section_id":"test1","field":"deposit","verdict":"incorrect","comment":"это не лёсс"}'
# GET снова — должна вернуться правка
curl "https://functions.yandexcloud.net/<ID>?section_id=test1"
```

Правки сохраняются в `s3://loess-results/corrections/<section_id>.json`.
