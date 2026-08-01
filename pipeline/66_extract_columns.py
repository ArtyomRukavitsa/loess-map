# Извлечение данных со стратиграфических колонок (просьба геолога, п.4 третьей итерации:
# «на колонках показаны наиболее важные сведения, а текстовая часть обрабатывается лучше графики»).
#
# Что делаем: находим страницы с колонками по подписям в уже распознанном тексте, перерендериваем
# ИМЕННО ИХ в 300 DPI и читаем мультимодальной моделью.
# ПОЧЕМУ 300, а не наши обычные 120: проверено на разрезе скв. 219 Патракеевка — при 120 модель
# прочитала горизонт как «Меловой (казанский)» вместо «Микулинский (казанцевский)», то есть мел
# вместо плейстоцена. При 300 та же страница читается верно.
#
# ЧТО БЕРЁМ НАДЁЖНО: название разреза, номер выработки (скв./шурф), названия горизонтов.
# ЧЕГО НЕ БЕРЁМ: послойные глубины и мощности — границы слоёв на рисунках заданы графически,
# без подписей, их надо мерить по шкале; модель этого не делает. Это отдельная задача.
#
# Выход: column_data.json — {"<slug>/pN": {...}}
#   LIMIT=5 python 66_extract_columns.py   — проба на нескольких страницах
import os, io, re, sys, json, time, base64, pathlib, collections, concurrent.futures
import urllib.request, urllib.parse, urllib.error
sys.stdout.reconfigure(encoding="utf-8")
HERE = pathlib.Path(__file__).parent
OUT = HERE / "column_data.json"
LIMIT = int(os.environ.get("LIMIT", 0))
DPI = int(os.environ.get("COL_DPI", 300))
WORKERS = int(os.environ.get("WORKERS", 3))
MODEL = os.environ.get("COL_MODEL", "qwen3.6-35b-a3b")   # единственная из линейки, что реально видит картинку

sec = {}
for line in open(HERE / ".secrets", encoding="utf-8"):
    line = line.strip()
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1); sec[k.strip()] = v.strip()
FOLDER, KEY = sec["YANDEX_FOLDER_ID"], sec["YANDEX_API_KEY"]
HDR = {"Authorization": "Api-Key " + KEY, "Content-Type": "application/json", "x-folder-id": FOLDER}
LLM = "https://llm.api.cloud.yandex.net/v1/chat/completions"
PK = "https://disk.yandex.ru/d/jKyK_ioYmjusVQ"
DISK = "https://cloud-api.yandex.net/v1/disk/public/resources"

import boto3
s3 = boto3.client("s3", endpoint_url="https://storage.yandexcloud.net", region_name="ru-central1",
                  aws_access_key_id=sec["AWS_ACCESS_KEY_ID"], aws_secret_access_key=sec["AWS_SECRET_ACCESS_KEY"])
SITE = "loess-map"

# ---------- где искать колонки ----------
CAP = re.compile(r"(литологическ\w*\s+колонк|стратиграфическ\w*\s+колонк|колонк\w*\s+разрез"
                 r"|строени\w*\s+разрез|схематическ\w*\s+разрез|геологическ\w*\s+разрез"
                 r"|разрез\s+скважин|сводн\w*\s+колонк|литолого-стратиграфическ)", re.I)

def slug_of(name):
    import hashlib
    base = re.sub(r"[^0-9A-Za-zА-Яа-яЁё]+", "_", name.rsplit(".", 1)[0]).strip("_")[:52]
    return f"{base}_{hashlib.md5(name.encode('utf-8')).hexdigest()[:6]}"

print("[1/4] ищу страницы с колонками...", flush=True)
keys, tok = [], None
while True:
    kw = {"Bucket": SITE, "Prefix": "scans/", "MaxKeys": 1000}
    if tok: kw["ContinuationToken"] = tok
    r = s3.list_objects_v2(**kw)
    keys += [o["Key"] for o in r.get("Contents", []) if o["Key"].endswith(".lines.json")]
    tok = r.get("NextContinuationToken")
    if not r.get("IsTruncated"): break

def has_column(key):
    try:
        d = json.loads(s3.get_object(Bucket=SITE, Key=key)["Body"].read())
    except Exception:
        return None
    txt = " ".join(l[0] for l in (d.get("lines") or []))
    return key if CAP.search(txt) else None

targets = []
with concurrent.futures.ThreadPoolExecutor(24) as ex:
    for r in ex.map(has_column, keys):
        if r: targets.append(r)
print(f"      страниц с колонками: {len(targets)}", flush=True)

# ---------- сопоставляем со страницами исходников ----------
scan_index = json.load(open(HERE / "scan_index.json", encoding="utf-8"))
slug2pub = {slug_of(pub): pub for pub in scan_index}
paths = {}
for f in ("priority_pdfs.json", "arch_docs.json"):
    p = HERE / f
    if not p.exists(): continue
    for it in json.load(open(p, encoding="utf-8")):
        d = it.get("path") if isinstance(it, dict) else None
        if d: paths.setdefault(os.path.basename(d), d)

by_pub = collections.defaultdict(list)
skipped = 0
for k in targets:
    parts = k.replace("scans/", "").replace(".lines.json", "").split("/")
    slug, pg = parts[0], int(parts[1][1:])
    pub = slug2pub.get(slug)
    if not pub or pub not in paths:
        skipped += 1; continue
    by_pub[pub].append((slug, pg))
print(f"      публикаций с исходником: {len(by_pub)} | без исходника пропущено страниц: {skipped}", flush=True)

todo = [(pub, pages) for pub, pages in by_pub.items()]
todo.sort(key=lambda x: -len(x[1]))
if LIMIT:
    trimmed, n = [], 0
    for pub, pages in todo:
        take = pages[:max(0, LIMIT - n)]
        if take: trimmed.append((pub, take)); n += len(take)
        if n >= LIMIT: break
    todo = trimmed
total_pages = sum(len(p) for _, p in todo)
print(f"      к обработке страниц: {total_pages}", flush=True)

# ---------- схема и запрос ----------
SCHEMA = {"type": "object", "additionalProperties": False,
          "required": ["has_column", "section_name", "excavation_id", "stratigraphic_units", "note"],
          "properties": {"has_column": {"type": "boolean"},
                         "section_name": {"type": "string"},
                         "excavation_id": {"type": "string"},
                         "stratigraphic_units": {"type": "array", "items": {"type": "string"}},
                         "note": {"type": "string"}}}
SYS = ("Ты читаешь РИСУНОК из геологической статьи. Если на нём есть литологическая или стратиграфическая "
       "колонка (разрез, скважина, обнажение), извлеки данные. Смотри подпись к рисунку и названия "
       "подразделений и горизонтов вдоль колонки. "
       "section_name — название разреза или пункта из подписи (например «Патракеевка»); "
       "excavation_id — номер скважины, шурфа или расчистки, если указан (например «219»); "
       "stratigraphic_units — названия горизонтов и подразделений ДОСЛОВНО, как подписаны "
       "(например «микулинский (казанцевский)», «верхний плейстоцен»). "
       "Ничего не выдумывать: чего не видно — пустая строка или пустой массив. "
       "has_column=false, если стратиграфической колонки на рисунке нет.")

MAX_BYTES = int(os.environ.get("MAX_BYTES", 1_300_000))   # выше сервис отвечает HTTP 400 (проверено:
                                                          # 2174 КБ — отказ, 1126 КБ — работает)

def to_jpeg(page, dpi=DPI):
    """Рендерим как можно крупнее, но в пределах допустимого размера: сперва жмём качеством,
    только потом уменьшаем разрешение — оно решает, читается ли текст на колонке."""
    for q in (85, 70, 55):
        b = page.get_pixmap(dpi=dpi).tobytes("jpeg", jpg_quality=q)
        if len(b) <= MAX_BYTES: return b, dpi, q
    for d in (240, 200, 170):
        b = page.get_pixmap(dpi=d).tobytes("jpeg", jpg_quality=70)
        if len(b) <= MAX_BYTES: return b, d, 70
    return page.get_pixmap(dpi=150).tobytes("jpeg", jpg_quality=65), 150, 65

def ask(jpg):
    body = {"model": f"gpt://{FOLDER}/{MODEL}/latest", "temperature": 0, "max_tokens": 12000,
            "messages": [{"role": "system", "content": SYS},
                         {"role": "user", "content": [
                             {"type": "text", "text": "Извлеки данные колонки с этого рисунка."},
                             {"type": "image_url",
                              "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpg).decode()}}]}],
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "col", "strict": True, "schema": SCHEMA}}}
    for attempt in range(3):
        req = urllib.request.Request(LLM, data=json.dumps(body).encode(), method="POST")
        for k, v in HDR.items(): req.add_header(k, v)
        try:
            d = json.loads(urllib.request.urlopen(req, timeout=300).read())
            c = d.get("choices", [{}])[0].get("message", {}).get("content")
            if c: return json.loads(c)
        except Exception as e:
            if attempt == 2: raise
            time.sleep(4 * (attempt + 1))
    return None

def disk_download(path):
    q = urllib.parse.urlencode({"public_key": PK, "path": path})
    href = json.load(urllib.request.urlopen(DISK + "/download?" + q, timeout=60))["href"]
    return urllib.request.urlopen(href, timeout=900).read()

res = json.load(open(OUT, encoding="utf-8")) if OUT.exists() else {}
stat = collections.Counter()
t0 = time.time()

def handle(item):
    pub, pages = item
    pages = [(s, p) for s, p in pages if f"{s}/p{p}" not in res]
    if not pages: return
    try:
        blob = disk_download(paths[pub])
    except Exception as e:
        print(f"  ! {pub[:40]}: {str(e)[:70]}", flush=True); return
    import fitz
    doc = fitz.open(stream=blob, filetype="pdf")
    for slug, pg in pages:
        i = pg - 1
        if i < 0 or i >= doc.page_count: continue
        try:
            jpg, used_dpi, used_q = to_jpeg(doc[i])
            r = ask(jpg)
        except Exception as e:
            stat["err"] += 1
            print(f"  ! {slug[:34]}/p{pg}: {str(e)[:70]}", flush=True); continue
        if not r:
            # Раньше этот случай молча увеличивал счётчик — в логе было видно 4 ошибки вместо 45.
            # Сбой без следа выглядит как отсутствие проблемы, поэтому пишем явно.
            stat["err"] += 1
            print(f"  ! {slug[:34]}/p{pg}: пустой ответ модели "
                  f"({used_dpi} DPI, {len(jpg)//1024} КБ)", flush=True)
            continue
        res[f"{slug}/p{pg}"] = {"pub": pub, "page": pg, **r}
        stat["done"] += 1
        stat["found"] += 1 if r.get("has_column") else 0
        if stat["done"] % 10 == 0:
            json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
            print(f"  [{stat['done']}/{total_pages}] с колонкой {stat['found']}, ошибок {stat['err']}, "
                  f"{(time.time()-t0)/60:.1f} мин", flush=True)
    doc.close()

print(f"[2/4] читаю колонки моделью {MODEL} при {DPI} DPI...", flush=True)
with concurrent.futures.ThreadPoolExecutor(WORKERS) as ex:
    list(ex.map(handle, todo))

json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
withcol = [v for v in res.values() if v.get("has_column")]
named = [v for v in withcol if v.get("section_name")]
exc = [v for v in withcol if v.get("excavation_id")]
print(f"\n[3/4] ГОТОВО за {(time.time()-t0)/60:.1f} мин: страниц обработано {len(res)}, "
      f"с колонкой {len(withcol)}, ошибок {stat['err']}")
print(f"      с названием разреза: {len(named)} | с номером выработки: {len(exc)}")
print(f"[4/4] -> {OUT.name}")
for v in withcol[:10]:
    print(f"   {str(v.get('section_name'))[:26]:26} | выработка {str(v.get('excavation_id'))[:8]:8} | "
          f"{', '.join(v.get('stratigraphic_units') or [])[:60]}")
