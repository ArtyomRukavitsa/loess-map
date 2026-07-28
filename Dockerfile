# Воспроизводимая среда конвейера извлечения (OCR + LLM + постобработка).
# Сборка:  docker build -t loess-atlas .
# Запуск:  docker run --rm -v %cd%:/work -w /work loess-atlas python pipeline/54_postprocess_v2.py
#          (секреты — через переменные окружения или монтирование .secrets, В ОБРАЗ НЕ ЗАПЕКАЮТСЯ)
FROM python:3.11-slim

# системные библиотеки для PyMuPDF / Pillow / img2pdf
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc libjpeg-dev zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pipeline/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pipeline/ ./pipeline/
COPY static_site/ ./static_site/
COPY cloud/ ./cloud/

# проверка, что все скрипты синтаксически валидны на этапе сборки
RUN python -m py_compile pipeline/*.py static_site/build_customer_map_v2.py

CMD ["python", "-c", "print('loess-atlas pipeline image ready. Смонтируй .secrets и запусти нужную стадию pipeline/*.py')"]
