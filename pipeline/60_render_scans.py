# Рендер страниц исходных сканов для просмотра рядом с данными (запрос коллег: как «Поиск по архивам»).
# Рендерим НЕ весь архив, а только страницы, на которые ссылаются разрезы (page_links_v2.json) — 2949 из ~50000.
# PDF качается с Яндекс.Диска, нужные страницы -> JPEG -> бакет сайта, файл сразу удаляется (память не забиваем).
# Выход: scan_index.json {публикация: {страница: ключ в бакете}} + объекты scans/<slug>/p<N>.jpg
# Тест:   LIMIT=3 python 60_render_scans.py
# Полный: python 60_render_scans.py        (возобновляемо: уже загруженные страницы пропускаются)
import os, io, sys, json, time, hashlib, re, pathlib, urllib.request, urllib.parse, collections
sys.stdout.reconfigure(encoding="utf-8")
HERE = pathlib.Path(__file__).parent

sec = {}
for line in open(HERE / ".secrets", encoding="utf-8"):
    line = line.strip()
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1); sec[k.strip()] = v.strip()
import boto3
s3 = boto3.client("s3", endpoint_url="https://storage.yandexcloud.net", region_name="ru-central1",
                  aws_access_key_id=sec["AWS_ACCESS_KEY_ID"], aws_secret_access_key=sec["AWS_SECRET_ACCESS_KEY"])
SITE_BUCKET = os.environ.get("SITE_BUCKET", "loess-map")     # публичный бакет сайта — оттуда картинки видит карта
DISK = "https://cloud-api.yandex.net/v1/disk/public/resources"
PK = "https://disk.yandex.ru/d/jKyK_ioYmjusVQ"
DPI = int(os.environ.get("SCAN_DPI", 120))
QUALITY = int(os.environ.get("SCAN_Q", 72))
LIMIT = int(os.environ.get("LIMIT", 0))
INDEX = HERE / "scan_index.json"

def slug(name):
    base = re.sub(r"[^0-9A-Za-zА-Яа-яЁё]+", "_", name.rsplit(".", 1)[0]).strip("_")[:52]
    return f"{base}_{hashlib.md5(name.encode('utf-8')).hexdigest()[:6]}"

# --- какие страницы нужны ---
pl = json.load(open(HERE / "page_links_v2.json", encoding="utf-8"))
need = collections.defaultdict(set)
for lst in pl.values():
    for e in lst:
        need[e["src"]].update(e["pages"])

UPLOAD_MARK = re.compile(r"\s*\(загружено пользователем\)\s*$", re.I)
need = {UPLOAD_MARK.sub("", k): v for k, v in need.items()}   # пометку загрузки для поиска исходника убираем

# Соседние страницы: упоминание разреза часто на одной странице, а описание, координаты или рисунок —
# на соседней. Рендерим ±NEIGH страниц, чтобы в просмотрщике можно было листать контекст.
NEIGH = int(os.environ.get("NEIGH", 1))
if NEIGH:
    for src in list(need):
        extra = set()
        for p in need[src]:
            for d in range(1, NEIGH + 1):
                if p - d >= 1: extra.add(p - d)
                extra.add(p + d)
        need[src] |= extra

# --- где лежат исходники: архив на Диске ---
paths = {}
for f in ("priority_pdfs.json", "arch_docs.json"):
    p = HERE / f
    if not p.exists(): continue
    items = json.load(open(p, encoding="utf-8"))
    for it in (items if isinstance(items, list) else []):
        d = it.get("path") if isinstance(it, dict) else None
        if d: paths.setdefault(os.path.basename(d), d)

# --- ...и публикации, загруженные пользователями (лежат в бакете, не на Диске) ---
uploaded = {}          # имя файла -> ключ объекта в бакете
tok = None
while True:
    kw = {"Bucket": sec.get("BUCKET", "loess-results"), "Prefix": "uploads/", "MaxKeys": 1000}
    if tok: kw["ContinuationToken"] = tok
    rr = s3.list_objects_v2(**kw)
    for o in rr.get("Contents", []):
        if not o["Key"].endswith("/status.json"): continue
        try:
            st = json.loads(s3.get_object(Bucket=kw["Bucket"], Key=o["Key"])["Body"].read())
        except Exception:
            continue
        fn = st.get("filename")
        jid = st.get("job_id") or o["Key"].split("/")[1]
        if fn and st.get("status") == "ready":
            ext = ("." + fn.rsplit(".", 1)[-1].lower()) if "." in fn else ".pdf"
            uploaded[fn] = f"uploads/{jid}/source{ext}"
    tok = rr.get("NextContinuationToken")
    if not rr.get("IsTruncated"): break
if uploaded: print(f"загруженных пользователями публикаций: {len(uploaded)}", flush=True)

todo = [(src, sorted(pages)) for src, pages in need.items() if src in paths or src in uploaded]
todo.sort(key=lambda x: -len(x[1]))
skipped = len(need) - len(todo)
if LIMIT: todo = todo[:LIMIT]
print(f"публикаций к рендеру: {len(todo)} (пропущено без исходника: {skipped}) | "
      f"страниц: {sum(len(p) for _, p in todo)} | DPI={DPI} q={QUALITY}", flush=True)

index = json.load(open(INDEX, encoding="utf-8")) if INDEX.exists() else {}

def disk_download(path):
    q = urllib.parse.urlencode({"public_key": PK, "path": path})
    href = json.load(urllib.request.urlopen(DISK + "/download?" + q, timeout=60))["href"]
    return urllib.request.urlopen(href, timeout=900).read()

def render_and_put(blob, src, pages):
    import fitz
    got = {}
    doc = fitz.open(stream=blob, filetype="pdf")
    sl = slug(src)
    for pg in pages:
        i = pg - 1                                   # в page_links страницы 1-based, в PDF индекс с нуля
        if i < 0 or i >= doc.page_count: continue
        key = f"scans/{sl}/p{pg}.jpg"
        if index.get(src, {}).get(str(pg)) == key: got[str(pg)] = key; continue
        pix = doc[i].get_pixmap(dpi=DPI)
        s3.put_object(Bucket=SITE_BUCKET, Key=key, Body=pix.tobytes("jpeg", jpg_quality=QUALITY),
                      ContentType="image/jpeg", CacheControl="public, max-age=2592000")
        got[str(pg)] = key
    doc.close()
    return got

# Узкое место — скачивание с Диска, поэтому публикации обрабатываем параллельно.
import concurrent.futures, threading
WORKERS = int(os.environ.get("WORKERS", 6))
_lock = threading.Lock()
done_pages = done_pubs = 0
t0 = time.time()

def handle(item):
    global done_pages, done_pubs
    n, (src, pages) = item
    have = index.get(src, {})
    if all(str(p) in have for p in pages):
        with _lock: done_pubs += 1
        return
    try:
        if src in uploaded:      # загруженная публикация — исходник уже в бакете
            blob = s3.get_object(Bucket=sec.get("BUCKET", "loess-results"), Key=uploaded[src])["Body"].read()
        else:
            blob = disk_download(paths[src])
        got = render_and_put(blob, src, pages)
        del blob                                     # исходник больше не нужен — освобождаем сразу
    except Exception as e:
        print(f"  ! {src[:50]}: {str(e)[:110]}", flush=True)
        return
    with _lock:
        index.setdefault(src, {}).update(got)
        done_pages += len(got); done_pubs += 1
        if done_pubs % 10 == 0 or done_pubs == len(todo):
            json.dump(index, open(INDEX, "w", encoding="utf-8"), ensure_ascii=False)
            el = time.time() - t0
            print(f"  [{done_pubs}/{len(todo)}] страниц {done_pages}, {el/60:.1f} мин "
                  f"(осталось ~{el/max(done_pubs,1)*(len(todo)-done_pubs)/60:.0f} мин)", flush=True)

with concurrent.futures.ThreadPoolExecutor(WORKERS) as ex:
    list(ex.map(handle, enumerate(todo, 1)))

json.dump(index, open(INDEX, "w", encoding="utf-8"), ensure_ascii=False)
tot = sum(len(v) for v in index.values())
print(f"\nГОТОВО. публикаций в индексе: {len(index)} | страниц-сканов: {tot} -> scan_index.json")
print(f"базовый URL: https://storage.yandexcloud.net/{SITE_BUCKET}/<ключ>")
