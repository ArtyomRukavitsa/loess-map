# Синхронизация рабочих данных карты с Object Storage (префикс data/).
# Нужна, чтобы карту можно было пересобирать В ОБЛАКЕ: облачная функция берёт данные оттуда,
# а не с чьего-то ноутбука. Локально скрипт остаётся способом залить/забрать актуальное состояние.
#   python 62_sync_data.py up     — отправить локальные файлы в бакет
#   python 62_sync_data.py down   — забрать из бакета (перезаписав локальные)
#   python 62_sync_data.py list   — что сейчас лежит в бакете
import os, sys, json, pathlib
sys.stdout.reconfigure(encoding="utf-8")
HERE = pathlib.Path(__file__).parent
SITE = HERE.parent / "loess_static_site"

sec = {}
for line in open(HERE / ".secrets", encoding="utf-8"):
    line = line.strip()
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1); sec[k.strip()] = v.strip()
import boto3
s3 = boto3.client("s3", endpoint_url="https://storage.yandexcloud.net", region_name="ru-central1",
                  aws_access_key_id=sec["AWS_ACCESS_KEY_ID"], aws_secret_access_key=sec["AWS_SECRET_ACCESS_KEY"])
BUCKET = sec.get("BUCKET", "loess-results")
PREFIX = "data/"

# имя в бакете -> путь на диске
FILES = {
    "records_clean_geo_v2.xlsx": HERE / "records_clean_geo_v2.xlsx",
    "section_evidence.json":     HERE / "section_evidence.json",
    "dem_elevation.json":        HERE / "dem_elevation.json",
    "accuracy_radius.json":      HERE / "accuracy_radius.json",
    "page_links_v2.json":        HERE / "page_links_v2.json",
    "scan_index.json":           HERE / "scan_index.json",
    "rotated_pages.json":        HERE / "rotated_pages.json",
    "column_data.json":          HERE / "column_data.json",
    "object_kind.json":           HERE / "object_kind.json",
    "object_links.json":          HERE / "object_links.json",
    "merged_uploads.json":       HERE / "merged_uploads.json",
    "geocache_sections.json":    HERE / "geocache_sections.json",
    "template.html":             SITE / "template_customer_map.html",
    "builder.py":                SITE / "build_customer_map_v2.py",   # облачная функция берёт сборщик отсюда
}
CTYPE = {".json": "application/json", ".html": "text/html; charset=utf-8",
         ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}

def human(n): return f"{n/1048576:.1f} МБ" if n >= 1048576 else f"{n/1024:.0f} КБ"

mode = (sys.argv[1] if len(sys.argv) > 1 else "list").lower()

if mode == "up":
    # Облачная публикация тоже пишет в data/ (дописывает принятые объекты). Если бакет свежее
    # локальной копии, заливка молча затрёт чужие изменения — поэтому сначала проверяем.
    if os.environ.get("FORCE", "0") != "1":
        import hashlib
        stale = []
        for name, path in FILES.items():
            if not path.exists(): continue
            try:
                head = s3.head_object(Bucket=BUCKET, Key=PREFIX + name)
            except Exception:
                continue
            if head["LastModified"].timestamp() <= path.stat().st_mtime + 5:
                continue
            # Время в облаке новее — но это ещё не значит, что содержимое другое: публикация
            # возвращает те же данные обратно. Сравниваем сам файл, иначе защита срабатывает впустую.
            etag = head.get("ETag", "").strip('"')
            local = hashlib.md5(path.read_bytes()).hexdigest()
            if etag and etag != local:
                stale.append(name)
        if stale:
            print("В БАКЕТЕ ДРУГАЯ ВЕРСИЯ ДАННЫХ — заливка затёрла бы чужие изменения:")
            for name in stale: print(f"   {name}")
            print("\nСначала забери актуальное:  python 62_sync_data.py down")
            print("Если уверен, что локальная версия правильная:  FORCE=1 python 62_sync_data.py up")
            sys.exit(1)
    total = 0
    for name, path in FILES.items():
        if not path.exists():
            print(f"  пропуск (нет файла): {name}"); continue
        body = path.read_bytes()
        s3.put_object(Bucket=BUCKET, Key=PREFIX + name, Body=body,
                      ContentType=CTYPE.get(path.suffix, "application/octet-stream"))
        total += len(body)
        print(f"  ↑ {name:28} {human(len(body))}")
    print(f"\nотправлено в s3://{BUCKET}/{PREFIX} — {human(total)}")

elif mode == "down":
    total = 0
    for name, path in FILES.items():
        try:
            body = s3.get_object(Bucket=BUCKET, Key=PREFIX + name)["Body"].read()
        except Exception:
            print(f"  пропуск (нет в бакете): {name}"); continue
        path.write_bytes(body); total += len(body)
        print(f"  ↓ {name:28} {human(len(body))}")
    print(f"\nзабрано из s3://{BUCKET}/{PREFIX} — {human(total)}")

else:
    r = s3.list_objects_v2(Bucket=BUCKET, Prefix=PREFIX)
    items = r.get("Contents", [])
    if not items:
        print("в бакете пусто — запусти: python 62_sync_data.py up")
    for o in sorted(items, key=lambda x: x["Key"]):
        print(f"  {o['Key'].replace(PREFIX,''):28} {human(o['Size']):>9}   {o['LastModified']:%Y-%m-%d %H:%M}")
