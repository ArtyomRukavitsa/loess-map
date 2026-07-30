# Цифровой атлас геологических разрезов и палеоархивов

Автоматическое извлечение структурированных научных данных о геологических разрезах
(лёссы, четвертичные отложения: местоположение, мощность, стратиграфия, методы датирования,
тип отложений) из архива **русскоязычных научных публикаций** — в структурированную базу
и на интерактивную карту.

**Заказчик:** Институт географии РАН · **Инфраструктура:** Yandex Cloud

**🔗 Живая карта:** https://loess-map.website.yandexcloud.net

[![CI](https://github.com/ArtyomRukavitsa/loess-map/actions/workflows/ci.yml/badge.svg)](https://github.com/ArtyomRukavitsa/loess-map/actions/workflows/ci.yml)

---

## Что это

Из архива ~10 000 файлов (~58 ГБ: PDF/DjVu-сканы и **фотографии страниц книг**) автоматический
конвейер извлекает данные о разрезах и наносит их на карту. Итог v2: **12 039 разрезов,
3 033 с координатами, 2 438 маркеров**, 16 категорий отложений.

## Архитектура

**Обработка архива** — набор независимых стадий, каждая читает вход и пишет выход в Object Storage:

```
Архив (Яндекс.Диск)
  └─► каталогизация ─► OCR (Vision) ─► извлечение (LLM, строгая JSON-схема с цитатой)
        ─► консолидация ─► геокодинг ─► привязка фраз к страницам ─► рендер сканов ─► карта
```

**Пополнение базы** работает целиком в облаке, без чьего-либо компьютера:

```
учёный загружает файл ─► напрямую в Object Storage (подписанная ссылка)
  └─► fn-process: распознавание ─► извлечение ─► привязка страниц ─► геокод
        └─► предложенные объекты ─► проверка человеком «принять / отклонить»
              └─► fn-publish: слияние ─► досчёт только нового ─► пересборка и публикация карты

fn-crawl: автопоиск публикаций в открытых источниках ─► те же предложения на проверку
```

Ключевое: принятые объекты попадают на карту только после решения человека, а данные карты
живут в Object Storage — поэтому пересборка не зависит от локальной машины.

## Структура репозитория

```
pipeline/       стадии конвейера (v1: 21–47, v2: 52–64) + инструкция по ВМ + requirements.txt
static_site/    карта (OpenLayers): сборщик, шаблон, деплой, собранный index.html,
                сборщик страницы загрузки (сама страница собирается локально — см. .gitignore)
cloud/          Yandex Cloud Functions:
                  fn_ocr_text     распознавание документа из архива
                  fn_extract_text извлечение записей из текста
                  fn_verify       ручная проверка данных в карточке
                  fn_ingest       приём загруженных публикаций (подписанные ссылки, очередь)
                  fn_process      обработка загрузки: распознавание, извлечение, геокод
                  fn_publish      слияние принятого и публикация карты
                  fn_crawl        автопополнение из открытых источников
app.py          Streamlit-карта (ранняя версия) + results.csv
Dockerfile      воспроизводимая среда конвейера
.github/workflows/   CI (линт/синтаксис/docker build) + CD (авто-деплой карты)
```

У каждой облачной функции рядом лежит `DEPLOY.md` с параметрами, переменными окружения и триггерами.

## Технологии

- **Python 3.9+**, `boto3`, `PyMuPDF`, `img2pdf`, `Pillow`, `openpyxl`, `python-docx`; карта — OpenLayers / Streamlit+pydeck.
- **Yandex Cloud:** Vision OCR (async), AI Studio / Foundation Models (мульти-модельный роутинг, строгая JSON-схема с цитатой-evidence), Object Storage (S3 + static hosting), Cloud Functions.
- **Внешние:** Nominatim/OSM (геокодинг), open-meteo + Copernicus DEM GLO-90 (расчёт высот), Carto/Esri (тайлы).

## Запуск

**Конвейер (Docker — воспроизводимо):**
```bash
docker build -t loess-atlas .
docker run --rm -v $(pwd):/work -w /work \
  --env-file .secrets loess-atlas python pipeline/54_postprocess_v2.py
```

**Конвейер (локально):**
```bash
pip install -r pipeline/requirements.txt
# создайте .secrets (не в репозитории): YANDEX_FOLDER_ID, YANDEX_API_KEY, BUCKET, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
python pipeline/52_extract_v2.py arch     # извлечение по OCR-текстам
python pipeline/54_postprocess_v2.py      # консолидация в разрезы
python pipeline/55_geocode_v2.py          # геокодинг
```

**Карта (статическая, OpenLayers):**
```bash
python static_site/build_customer_map_v2.py   # собрать index.html из данных
python static_site/deploy_site.py             # деплой в Object Storage
```

**Карта (Streamlit, ранняя):**
```bash
pip install -r requirements.txt && streamlit run app.py
```

## CI/CD

- **CI** (`.github/workflows/ci.yml`) — на каждый пуш/PR: `black --check`, `py_compile` всех скриптов, сборка Docker-образа.
- **CD** (`.github/workflows/deploy.yml`) — на пуш собранной карты в `main`: авто-деплой `static_site/index.html` в Yandex Object Storage (static website hosting).
  Требуются секреты репозитория: `YC_ACCESS_KEY`, `YC_SECRET_KEY` (ключи Object Storage).

## Возможности карты

Дизайн заказчика (OpenLayers): выбор подложки, поиск, фильтры (тип отложений/стратиграфия/датирование/
мощность/высота/точность), accuracy-круги, кластеризация, **попап-карточка** на разрез с полями,
цитатой-обоснованием, индикаторами уверенности и надёжности локации, типами мощности, DEM-высотой.

## Оговорки

- Данные извлечены **автоматически** — пригодны для скрининга; точки для публикации сверять с первоисточником (в карточке — цитата + источник).
- Пусто = `ND` (данные не выдумываются). Расчётная высота помечена «DEM (computed)».
- Секреты и большие данные в репозиторий не входят (`.gitignore`).
