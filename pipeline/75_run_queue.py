# Прогон очереди автопоиска через облачную функцию fn_process.
#
# Краулер нашёл сотни публикаций и сложил их в очередь со статусом queued — обработку никто не
# запускал. OCR при этом НЕ нужен: краулер уже сохранил текст статьи в ocrtext/, поэтому функция
# идёт сразу на извлечение и укладывается в секунды-минуты, а не в часы.
#
# Гоняем пачками с ограниченной одновременностью: каждый вызов — отдельный экземпляр функции,
# и слишком широкий фронт упрётся в лимиты моделей (429) и вызовет лишние повторы.
#
#   WORKERS=12 python 75_run_queue.py          — прогон всей очереди
#   LIMIT=20 python 75_run_queue.py            — только первые 20 (проба)
#   STATE=... python 75_run_queue.py           — свой файл прогресса
import os, sys, json, time, pathlib, collections, threading
import urllib.request, urllib.error
import concurrent.futures
import boto3
sys.stdout.reconfigure(encoding="utf-8")
HERE = pathlib.Path(__file__).parent
STATE = pathlib.Path(os.environ.get("STATE", HERE / "queue_run_state.json"))
FN = os.environ.get("FN_PROCESS", "https://functions.yandexcloud.net/d4eo24hhbg9obq44g47v")
WORKERS = int(os.environ.get("WORKERS", 12))
LIMIT = int(os.environ.get("LIMIT", 0))
# какие статусы забирать: queued — новьё из автопоиска, error — перезапуск упавших после правки
TAKE = set(os.environ.get("STATUS", "queued").split(","))
TIMEOUT = int(os.environ.get("TIMEOUT", 2400))

sec = {}
for line in open(HERE / ".secrets", encoding="utf-8"):
    line = line.strip()
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1); sec[k.strip()] = v.strip()
BUCKET = sec.get("BUCKET", "loess-results")
s3 = boto3.client("s3", endpoint_url="https://storage.yandexcloud.net", region_name="ru-central1",
                  aws_access_key_id=sec["AWS_ACCESS_KEY_ID"],
                  aws_secret_access_key=sec["AWS_SECRET_ACCESS_KEY"])

print("собираю очередь...", flush=True)
ids = set()
for pg in s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix="uploads/cl-"):
    for o in pg.get("Contents", []): ids.add(o["Key"].split("/")[1])


def status(j):
    try:
        return j, json.loads(s3.get_object(Bucket=BUCKET, Key=f"uploads/{j}/status.json")["Body"].read())
    except Exception:
        return j, {}


with concurrent.futures.ThreadPoolExecutor(24) as ex:
    st = dict(ex.map(status, ids))
counts = collections.Counter(v.get("status", "?") for v in st.values())
print(f"всего задач автопоиска: {len(ids)} | {dict(counts)}", flush=True)

state = json.load(open(STATE, encoding="utf-8")) if STATE.exists() else {"done": [], "failed": {}}
if TAKE != {"queued"}: state["done"] = []      # повтор: пройденное раньше не в счёт
done = set(state["done"])
todo = sorted(j for j, v in st.items() if v.get("status") in TAKE and j not in done)
if LIMIT: todo = todo[:LIMIT]
print(f"к обработке: {len(todo)} (уже пройдено ранее: {len(done)})", flush=True)
if not todo: sys.exit(0)

lock = threading.Lock()
stat = collections.Counter()
t0 = time.time()


def run(j):
    t = time.time()
    try:
        req = urllib.request.Request(FN, data=json.dumps({"job_id": j}).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        r = json.loads(urllib.request.urlopen(req, timeout=TIMEOUT).read().decode())
        ok = j in (r.get("processed") or [])
    except Exception as e:
        with lock:
            stat["ошибка"] += 1
            state["failed"][j] = str(e)[:120]
            if stat["ошибка"] <= 6: print(f"  ! {j}: {str(e)[:90]}", flush=True)
        return
    with lock:
        stat["обработано" if ok else "без результата"] += 1
        state["done"].append(j)
        n = stat["обработано"] + stat["без результата"] + stat["ошибка"]
        if n % 20 == 0 or n == len(todo):
            json.dump(state, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)
            el = time.time() - t0
            rate = n / el if el else 0
            left = (len(todo) - n) / rate / 60 if rate else 0
            print(f"  [{n}/{len(todo)}] {el/60:.0f} мин | {dict(stat)} | осталось ~{left:.0f} мин",
                  flush=True)


with concurrent.futures.ThreadPoolExecutor(WORKERS) as ex:
    list(ex.map(run, todo))

json.dump(state, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)
print(f"\nГОТОВО за {(time.time()-t0)/60:.0f} мин: {dict(stat)}", flush=True)

# итог: сколько статей дало разрезы и сколько предложений всего
with concurrent.futures.ThreadPoolExecutor(24) as ex:
    fin = dict(ex.map(status, todo))
props = sum(int(v.get("n_proposals") or 0) for v in fin.values())
with_p = sum(1 for v in fin.values() if int(v.get("n_proposals") or 0) > 0)
print(f"статей с разрезами: {with_p} из {len(todo)} | предложений всего: {props}")
print(f"-> {STATE.name}")
