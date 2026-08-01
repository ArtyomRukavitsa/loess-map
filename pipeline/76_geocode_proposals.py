# Доводка координат у предложений, вышедших из fn_process без них.
#
# Причина пропусков: в поле «административная единица» модель кладёт то, что написано в статье,
# а там сплошь неформальные области — «Центральная Якутия», «Костенковско-Боршевский район».
# В справочнике таких нет, запрос «Амга, Центральная Якутия» пустой, хотя «Амга» находится сразу.
# Целые статьи из-за этого давали объекты без координат, то есть невидимые на карте.
#
# Скрипт добирает их локально, не трогая извлечение: пробует название без уточнения, а также
# отрезает от названия порядковый номер памятника («Костенки I» -> «Костенки»). Каждую такую
# точку помечает, чтобы проверяющий знал: уточнение из статьи справочником не подтверждено,
# возможен тёзка.
#
#   DRY=1 python 76_geocode_proposals.py       — показать, что добавится
#   JOB=cl-xxxx python 76_geocode_proposals.py — одна публикация
import os, re, sys, json, time, pathlib, collections, threading
import urllib.request, urllib.parse
import concurrent.futures
import boto3
sys.stdout.reconfigure(encoding="utf-8")
HERE = pathlib.Path(__file__).parent
CACHE = HERE / "geocache_proposals.json"
DRY = os.environ.get("DRY", "0") == "1"
ONLY = os.environ.get("JOB", "")
UA = "loess-sections/1.0 (research)"
CIS = "ru,ua,kz,by,md,ge,az,am,uz,kg,tj,tm,ee,lv,lt"
SETTLE = {"city", "town", "village", "hamlet", "municipality", "administrative", "suburb",
          "borough", "isolated_dwelling", "locality", "quarter"}

sec = {}
for line in open(HERE / ".secrets", encoding="utf-8"):
    line = line.strip()
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1); sec[k.strip()] = v.strip()
BUCKET = sec.get("BUCKET", "loess-results")
s3 = boto3.client("s3", endpoint_url="https://storage.yandexcloud.net", region_name="ru-central1",
                  aws_access_key_id=sec["AWS_ACCESS_KEY_ID"],
                  aws_secret_access_key=sec["AWS_SECRET_ACCESS_KEY"])

cache = json.load(open(CACHE, encoding="utf-8")) if CACHE.exists() else {}
lock = threading.Lock()
_last = [0.0]


def nominatim(q_text):
    """Один запрос в секунду — правило сервиса; держим глобально, а не на поток."""
    if q_text in cache: return cache[q_text]
    res = None
    try:
        with lock:
            wait = 1.1 - (time.time() - _last[0])
            if wait > 0: time.sleep(wait)
            _last[0] = time.time()
        q = urllib.parse.urlencode({"q": q_text, "format": "json", "limit": 5, "countrycodes": CIS})
        r = urllib.request.Request("https://nominatim.openstreetmap.org/search?" + q,
                                   headers={"User-Agent": UA})
        for it in json.load(urllib.request.urlopen(r, timeout=30)):
            if it.get("class") in ("place", "boundary") and it.get("type") in SETTLE:
                la, lo = float(it["lat"]), float(it["lon"])
                if 41 <= la <= 82 and 19 <= lo <= 180: res = [la, lo]; break
    except Exception:
        res = None
    cache[q_text] = res
    return res


# «Костенки I», «Боршево II», «Разрез-3» — порядковый номер объекта, а не часть топонима
NUM_TAIL = re.compile(r"[\s\-–]+(?:[IVXLC]{1,5}|\d{1,3}[а-яa-z]?)\s*$", re.I)
PREFIX = re.compile(r"^\s*(?:с|д|пос|г|ст|хут|пгт|аул|разрез|скв)\.?\s+", re.I)


# Геокодер отвечает уверенной точкой почти на что угодно: «16 км» он нашёл под Хабаровском,
# «АМК-4515» — в Казахстане. Поэтому то, что заведомо не топоним, к нему вообще не носим.
NOT_PLACE = [
    (re.compile(r"^\s*\d+[\s,.]*км", re.I), "расстояние по трассе"),
    (re.compile(r"^[A-ZА-Я]{2,5}[-–]?\d{2,}"), "номер выработки"),
    (re.compile(r"^(?:скв|обн|шурф|расчистк|разрез|profile|core)\b", re.I), "обозначение выработки"),
    (re.compile(r"\b(?:море|озеро|залив|пролив|хребет|плато|равнин|низменност|возвышенност"
                r"|бассейн|междуречь)", re.I), "объект-площадь, а не точка"),
    (re.compile(r"\b(?:район|область|край|округ|республик|уезд|губерни)\b", re.I), "административная единица"),
]


def not_place(name):
    """Почему это название нельзя искать как населённый пункт (или None, если можно)."""
    for rx, label in NOT_PLACE:
        if rx.search(name or ""): return label
    if not re.findall(r"[А-Яа-яЁёA-Za-z]{4,}", name or ""): return "нет словесной основы"
    return None


def variants(name):
    n = str(name or "").strip()
    out = [n]
    bare = PREFIX.sub("", n).strip()
    if bare != n: out.append(bare)
    short = NUM_TAIL.sub("", bare).strip()
    if short and short != bare and len(short) >= 4: out.append(short)
    seen, res = set(), []
    for v in out:
        if len(v) >= 3 and v.lower() not in seen: seen.add(v.lower()); res.append(v)
    return res


jobs = set()
for pg in s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix="uploads/"):
    for o in pg.get("Contents", []):
        if o["Key"].endswith("/proposals.json"): jobs.add(o["Key"].split("/")[1])
if ONLY: jobs = {ONLY} & jobs or {ONLY}
print(f"публикаций с предложениями: {len(jobs)}")

stat = collections.Counter()
report = []


def load(j):
    try:
        p = json.loads(s3.get_object(Bucket=BUCKET, Key=f"uploads/{j}/proposals.json")["Body"].read())
        return j, (p if isinstance(p, list) else p.get("proposals", []))
    except Exception:
        return j, []


with concurrent.futures.ThreadPoolExecutor(16) as ex:
    data = dict(ex.map(load, jobs))
missing = [(j, i, p) for j, items in data.items() for i, p in enumerate(items)
           if p.get("lat") is None and str(p.get("locality") or "").strip() not in ("", "ND", "не указано")]
print(f"объектов без координат: {len(missing)}")
names = sorted({str(p.get("locality")).strip() for _, _, p in missing})
print(f"уникальных названий: {len(names)} (столько запросов максимум, ~1 в секунду)\n")

changed = collections.defaultdict(dict)
for k, name in enumerate(names, 1):
    why = not_place(name)
    if why:
        stat["отсеяно"] += 1
        if stat["отсеяно"] <= 8: print(f"   пропуск «{name[:28]}» — {why}")
        continue
    got, used = None, ""
    for v in variants(name):
        got = nominatim(v)
        if got: used = v; break
    if got:
        stat["найдено"] += 1
        changed["_names"][name] = (got, used)
        if len(report) < 25: report.append((name, used, got))
    else:
        stat["не найдено"] += 1
    if k % 25 == 0:
        json.dump(cache, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False)
        print(f"  [{k}/{len(names)}] найдено {stat['найдено']}, не найдено {stat['не найдено']}", flush=True)

json.dump(cache, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False)
print(f"\nнайдено {stat['найдено']} из {len(names)} названий\n")
for name, used, got in report:
    mark = "" if used == name else f"  (искали как «{used}»)"
    print(f"   {name[:26]:26} -> {got[0]:.4f}, {got[1]:.4f}{mark}")

found = changed["_names"]
touched = collections.Counter()
for j, i, p in missing:
    nm = str(p.get("locality")).strip()
    if nm in found: touched[j] += 1
print(f"\nбудет дополнено публикаций: {len(touched)}, объектов: {sum(touched.values())}")
if DRY:
    print("сухой прогон — в бакет ничего не записано"); sys.exit(0)

wrote = 0
for j, n in touched.items():
    items = data[j]
    for p in items:
        nm = str(p.get("locality") or "").strip()
        if p.get("lat") is None and nm in found:
            (la, lo), used = found[nm]
            p["lat"], p["lon"] = la, lo
            # уточнение из статьи справочником не подтверждено — проверяющий должен это видеть
            p["geo_note"] = ("найдено по названию без уточнения" if used == nm
                             else f"найдено как «{used}» — номер объекта отброшен")
    s3.put_object(Bucket=BUCKET, Key=f"uploads/{j}/proposals.json", ContentType="application/json",
                  Body=json.dumps(items, ensure_ascii=False).encode())
    wrote += n
print(f"дописано координат: {wrote}")
