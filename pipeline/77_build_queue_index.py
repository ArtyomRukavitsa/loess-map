# Указатель очереди: одна короткая строчка на каждую задачу.
#
# Зачем: страница проверки показывала последние 50 задач из 822 — остальные были недостижимы,
# к ним не вело ни кнопки, ни адреса. Просто поднять лимит нельзя: каждая задача — отдельное
# обращение к хранилищу, 50 штук занимают 7.6 с, все 822 заняли бы две минуты, и страница
# не дождётся. Поэтому сводим всё в один файл, который функция отдаёт целиком за один запрос.
#
# Кладём сюда только то, по чему проверяющий выбирает работу: название, состояние, сколько
# объектов нашлось и сколько из них с координатами. Сами объекты остаются в proposals.json.
#
#   python 77_build_queue_index.py            — пересобрать указатель
#   DRY=1 python 77_build_queue_index.py      — только показать сводку
import os, sys, json, time, pathlib, collections
import concurrent.futures
import boto3
sys.stdout.reconfigure(encoding="utf-8")
HERE = pathlib.Path(__file__).parent
OUT = HERE / "queue_index.json"
KEY = "data/queue_index.json"
DRY = os.environ.get("DRY", "0") == "1"

sec = {}
for line in open(HERE / ".secrets", encoding="utf-8"):
    line = line.strip()
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1); sec[k.strip()] = v.strip()
BUCKET = sec.get("BUCKET", "loess-results")
s3 = boto3.client("s3", endpoint_url="https://storage.yandexcloud.net", region_name="ru-central1",
                  aws_access_key_id=sec["AWS_ACCESS_KEY_ID"],
                  aws_secret_access_key=sec["AWS_SECRET_ACCESS_KEY"])

t0 = time.time()
jobs, has_props = set(), set()
for pg in s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix="uploads/"):
    for o in pg.get("Contents", []):
        parts = o["Key"].split("/")
        if len(parts) < 3: continue
        if parts[2] == "status.json": jobs.add(parts[1])
        elif parts[2] == "proposals.json": has_props.add(parts[1])
print(f"задач: {len(jobs)}, из них с предложениями: {len(has_props)}")


def get(key, default=None):
    try:
        return json.loads(s3.get_object(Bucket=BUCKET, Key=key)["Body"].read())
    except Exception:
        return default


def row(j):
    st = get(f"uploads/{j}/status.json", {}) or {}
    n_geo = n_dec = 0
    props = get(f"uploads/{j}/proposals.json", []) if j in has_props else []
    items = props if isinstance(props, list) else props.get("proposals", [])
    n_geo = sum(1 for p in items if p.get("lat") is not None)
    dec = get(f"uploads/{j}/decisions.json", {}) if j in has_props else {}
    n_dec = len(dec) if isinstance(dec, dict) else 0
    return {
        "id": j,
        "name": str(st.get("filename") or j)[:180],
        "status": st.get("status", "?"),
        "n": len(items),                       # найдено объектов
        "geo": n_geo,                          # из них с координатами
        "checked": n_dec,                      # по скольким уже есть решение проверяющего
        "src": st.get("user") or st.get("source") or "",
        "url": st.get("url", ""),
        "created": st.get("created", 0),
        "msg": str(st.get("msg") or "")[:120],
    }


with concurrent.futures.ThreadPoolExecutor(32) as ex:
    rows = list(ex.map(row, sorted(jobs)))

# сначала то, где есть что проверять и что ещё не разобрано
rows.sort(key=lambda r: (r["checked"] >= r["n"] and r["n"] > 0, -r["n"], -r["geo"]))
idx = {"built": int(time.time()), "total": len(rows), "jobs": rows}

st = collections.Counter(r["status"] for r in rows)
print(f"собрано за {time.time()-t0:.0f} с | состояния: {dict(st)}")
print(f"объектов всего: {sum(r['n'] for r in rows)} | с координатами: {sum(r['geo'] for r in rows)} | "
      f"уже проверено: {sum(r['checked'] for r in rows)}")
body = json.dumps(idx, ensure_ascii=False).encode()
print(f"размер указателя: {len(body)//1024} КБ")
print()
print("первые строки (как их увидит проверяющий):")
for r in rows[:8]:
    print(f"   {r['n']:3} объектов ({r['geo']:3} с коорд.)  {r['status']:8}  {r['name'][:58]}")

if DRY:
    print("\nсухой прогон — файл не сохранён"); sys.exit(0)
OUT.write_bytes(body)
s3.put_object(Bucket=BUCKET, Key=KEY, Body=body, ContentType="application/json")
print(f"\n-> {OUT.name} и s3://{BUCKET}/{KEY}")
