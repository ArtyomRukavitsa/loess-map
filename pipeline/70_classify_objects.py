# Классификация: ЧТО ИМЕННО описано в публикации — прямой ответ на главную мысль геолога:
# «сложности связаны уже не с поиском слов, а с пониманием того, что именно упоминается:
#  конкретная выработка, сводный разрез, стратиграфическое подразделение или регион в целом».
#
# Переизвлечение НЕ нужно: у каждой записи уже есть цитаты-основания, по ним модель и судит.
# 2342 объекта вместо разбора 630 документов заново.
#
# Выход: object_kind.json — {"lat,lon": {kind, why, sure}}
#   LIMIT=20 python 70_classify_objects.py   — проба
import os, re, sys, json, time, random, pathlib, collections, threading, concurrent.futures
import urllib.request, urllib.error
import openpyxl
sys.stdout.reconfigure(encoding="utf-8")
HERE = pathlib.Path(__file__).parent
XLSX = HERE / "records_clean_geo_v2.xlsx"
OUT = HERE / "object_kind.json"
LIMIT = int(os.environ.get("LIMIT", 0))
WORKERS = int(os.environ.get("WORKERS", 8))
MODEL = os.environ.get("KIND_MODEL", "deepseek-v32")

sec = {}
for line in open(HERE / ".secrets", encoding="utf-8"):
    line = line.strip()
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1); sec[k.strip()] = v.strip()
FOLDER, KEY = sec["YANDEX_FOLDER_ID"], sec["YANDEX_API_KEY"]
HDR = {"Authorization": "Api-Key " + KEY, "Content-Type": "application/json", "x-folder-id": FOLDER}
LLM = "https://llm.api.cloud.yandex.net/v1/chat/completions"

KINDS = ["конкретная выработка", "сводный разрез", "стратиграфическое подразделение",
         "регион в целом", "неясно"]
SCHEMA = {"type": "object", "additionalProperties": False,
          "required": ["kind", "why", "sure"],
          "properties": {"kind": {"type": "string", "enum": KINDS},
                         "why": {"type": "string"},
                         "sure": {"type": "string", "enum": ["высокая", "средняя", "низкая"]}}}
SYS = (
    "Ты геолог-четвертичник. По названию объекта и цитатам из публикации определи, ЧТО ИМЕННО описано.\n"
    "• «конкретная выработка» — реально пройденный объект в точке: скважина, шурф, расчистка, карьер, "
    "естественное обнажение. Признаки: номер выработки, глубина, описание слоёв сверху вниз.\n"
    "• «сводный разрез» — обобщённая схема по нескольким выработкам участка; «опорный разрез», "
    "«сводная колонка», «типовой разрез».\n"
    "• «стратиграфическое подразделение» — НАЗВАНИЕ слоя, свиты, горизонта или лёсса, образованное от "
    "топонима («борисоглебский лёсс», «армавирская свита», «роменская почва»). Место лишь дало имя слою, "
    "самостоятельный разрез там НЕ описан.\n"
    "• «регион в целом» — площадь, район, область, междуречье, бассейн: речь о территории, а не о точке "
    "(«лёссы Приазовья», «в районе Самары развиты…»).\n"
    "• «неясно» — цитат не хватает.\n"
    "Суди ТОЛЬКО по приведённым цитатам, не домысливай. why — одна короткая фраза с обоснованием.")

def http(body, tries=6):
    for a in range(tries):
        req = urllib.request.Request(LLM, data=json.dumps(body).encode(), method="POST")
        for k, v in HDR.items(): req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=180) as r: return r.read().decode()
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and a < tries - 1:
                time.sleep(min(20, 2 ** a) + random.uniform(0, 1.5)); continue
            raise
        except Exception:
            if a < tries - 1: time.sleep(min(20, 2 ** a) + random.uniform(0, 1.5)); continue
            raise

# ---------- собираем объекты (по координате, как маркеры на карте) ----------
wb = openpyxl.load_workbook(XLSX, read_only=True); ws = wb.active
rows = ws.iter_rows(values_only=True); H = list(next(rows)); C = {n: H.index(n) for n in H}
objs = {}
for r in rows:
    la, lo = r[C["lat"]], r[C["lon"]]
    if la in (None, "", "ND") or str(la) == "None": continue
    key = f"{round(float(la),6)},{round(float(lo),6)}"
    o = objs.setdefault(key, {"loc": set(), "adm": set(), "ev": [], "exc": set()})
    o["loc"].add(str(r[C["nearest_locality"]] or "").strip())
    a = str(r[C["administrative_unit"]] or "").strip()
    if a and a != "ND": o["adm"].add(a)
    e = str(r[C["excavation_type"]] or "").strip()
    if e and e != "ND": o["exc"].add(e)
    for p in str(r[C["evidence"]] or "").split("||"):
        p = p.strip()
        if p and p not in o["ev"]: o["ev"].append(p)
wb.close()
print(f"объектов к классификации: {len(objs)}")

res = json.load(open(OUT, encoding="utf-8")) if OUT.exists() else {}
todo = [(k, v) for k, v in objs.items() if k not in res]
if LIMIT: todo = todo[:LIMIT]
print(f"осталось: {len(todo)} (уже сделано {len(res)})")

stat = collections.Counter(); lock = threading.Lock(); t0 = time.time()

def classify(item):
    key, o = item
    names = "; ".join(sorted(o["loc"])[:3])
    adm = "; ".join(sorted(o["adm"])[:2]) or "не указана"
    exc = "; ".join(sorted(o["exc"])[:3]) or "не указан"
    ev = "\n".join("• " + e[:200] for e in o["ev"][:8]) or "(цитат нет)"
    user = (f"Объект: {names}\nАдминистративная единица: {adm}\nТип вскрытия по тексту: {exc}\n"
            f"Цитаты из публикаций:\n{ev}\n\nЧто именно описано?")
    body = {"model": f"gpt://{FOLDER}/{MODEL}/latest", "temperature": 0, "max_tokens": 700,
            "messages": [{"role": "system", "content": SYS}, {"role": "user", "content": user}],
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "kind", "strict": True, "schema": SCHEMA}}}
    try:
        d = json.loads(http(body))
        c = d.get("choices", [{}])[0].get("message", {}).get("content")
        r = json.loads(c) if c else None
    except Exception as e:
        with lock:
            stat["err"] += 1
            if stat["err"] <= 5: print(f"  ! {names[:30]}: {str(e)[:70]}", flush=True)
        return
    if not r:
        with lock: stat["empty"] += 1
        return
    with lock:
        res[key] = {"name": sorted(o["loc"])[0] if o["loc"] else "", **r}
        stat[r["kind"]] += 1; stat["done"] += 1
        if stat["done"] % 100 == 0:
            json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
            print(f"  [{stat['done']}/{len(todo)}] {(time.time()-t0)/60:.1f} мин | "
                  f"{ {k: v for k, v in stat.items() if k in KINDS} }", flush=True)

with concurrent.futures.ThreadPoolExecutor(WORKERS) as ex:
    list(ex.map(classify, todo))

json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
c = collections.Counter(v["kind"] for v in res.values())
print(f"\nГОТОВО за {(time.time()-t0)/60:.1f} мин. классифицировано: {len(res)}, ошибок {stat['err']}")
for k, n in c.most_common(): print(f"   {n:5}  {k}")
print(f"-> {OUT.name}")
