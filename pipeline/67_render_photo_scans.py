# Сканы для фото-публикаций (замечание геолога: «страницы открываются не для всех публикаций,
# например для Сафронова»). Часть работ лежит в архиве не PDF-файлом, а ПАПКОЙ СО СНИМКАМИ страниц.
# Рендерить там нечего — картинки уже есть, но связь «страница N -> файл» нигде не записана:
# в данных распознавания хранится путь к папке целиком.
#
# ПРОВЕРЕНО: число распознанных страниц точно совпадает с числом файлов в папке, а page_from идёт
# ровными блоками — значит снимки обрабатывались подряд, в порядке имён. Поэтому страница N — это
# N-й файл при сортировке по имени. Переделывать распознавание не нужно.
#
# Выход: те же scans/<slug>/pN.jpg, что и у обычных публикаций, + пополненный scan_index.json
#   DRY=1 python 67_render_photo_scans.py   — только посчитать объём
import os, io, re, sys, json, time, hashlib, pathlib, collections, concurrent.futures
import urllib.request, urllib.parse
sys.stdout.reconfigure(encoding="utf-8")
HERE = pathlib.Path(__file__).parent
DRY = os.environ.get("DRY", "0") == "1"
NEIGH = int(os.environ.get("NEIGH", 1))          # ±страницы для контекста, как у обычных сканов
MAXW = int(os.environ.get("MAXW", 1500))         # приводим к тому же порядку размера
QUALITY = int(os.environ.get("SCAN_Q", 72))
WORKERS = int(os.environ.get("WORKERS", 5))

sec = {}
for line in open(HERE / ".secrets", encoding="utf-8"):
    line = line.strip()
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1); sec[k.strip()] = v.strip()
import boto3
s3 = boto3.client("s3", endpoint_url="https://storage.yandexcloud.net", region_name="ru-central1",
                  aws_access_key_id=sec["AWS_ACCESS_KEY_ID"], aws_secret_access_key=sec["AWS_SECRET_ACCESS_KEY"])
BUCKET = sec.get("BUCKET", "loess-results")
SITE = os.environ.get("SITE_BUCKET", "loess-map")
PK = "https://disk.yandex.ru/d/jKyK_ioYmjusVQ"
DISK = "https://cloud-api.yandex.net/v1/disk/public/resources"
IMG_EXT = (".bmp", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".gif")

def slug(name):
    base = re.sub(r"[^0-9A-Za-zА-Яа-яЁё]+", "_", name.rsplit(".", 1)[0]).strip("_")[:52]
    return f"{base}_{hashlib.md5(name.encode('utf-8')).hexdigest()[:6]}"

# ---------- какие публикации без сканов ----------
pl = json.load(open(HERE / "page_links_v2.json", encoding="utf-8"))
idx = json.load(open(HERE / "scan_index.json", encoding="utf-8"))
UPLOAD_MARK = re.compile(r"\s*\(загружено пользователем\)\s*$", re.I)
need = collections.defaultdict(set)
for lst in pl.values():
    for e in lst:
        src = UPLOAD_MARK.sub("", e["src"])
        if src not in idx:
            need[src].update(e["pages"])
print(f"публикаций без сканов: {len(need)} | страниц с находками: {sum(len(v) for v in need.values())}")

# ---------- где их папки на Диске ----------
print("ищу пути папок в данных распознавания...", flush=True)
folders = {}
tok = None
while True:
    kw = {"Bucket": BUCKET, "Prefix": "ocrtext/img/", "MaxKeys": 1000}
    if tok: kw["ContinuationToken"] = tok
    r = s3.list_objects_v2(**kw)
    for o in r.get("Contents", []):
        try:
            d = json.loads(s3.get_object(Bucket=BUCKET, Key=o["Key"])["Body"].read())
        except Exception:
            continue
        p = str(d.get("path") or "")
        if p: folders.setdefault(os.path.basename(p.replace("\\", "/")), p)
    tok = r.get("NextContinuationToken")
    if not r.get("IsTruncated"): break
print(f"  папок известно: {len(folders)}")

todo = []
for src, pages in need.items():
    if src not in folders: continue
    pp = set(pages)
    for p in list(pages):                       # соседние страницы — тот же контекст, что и у PDF
        for d in range(1, NEIGH + 1):
            if p - d >= 1: pp.add(p - d)
            pp.add(p + d)
    todo.append((src, folders[src], sorted(pp)))
todo.sort(key=lambda x: -len(x[2]))
miss = len(need) - len(todo)
print(f"  сопоставлено публикаций: {len(todo)} (без папки: {miss}) | страниц к загрузке: {sum(len(t[2]) for t in todo)}")

if DRY:
    for src, path, pages in todo[:10]:
        print(f"   {len(pages):4} стр.  {src[:56]}")
    sys.exit(0)

def disk_list(path):
    items, offset = [], 0
    while True:
        q = urllib.parse.urlencode({"public_key": PK, "path": path, "limit": 200, "offset": offset})
        d = json.load(urllib.request.urlopen(DISK + "?" + q, timeout=90))
        emb = d.get("_embedded", {})
        got = emb.get("items", [])
        items += [i for i in got if i.get("type") == "file" and i["name"].lower().endswith(IMG_EXT)]
        offset += len(got)
        if len(got) < 200 or offset >= emb.get("total", 0): break
    items.sort(key=lambda i: i["name"])          # порядок имён = порядок распознавания
    return items

def fetch(url):
    return urllib.request.urlopen(url, timeout=300).read()

stat = collections.Counter()
t0 = time.time()
lock = __import__("threading").Lock()

def handle(item):
    src, path, pages = item
    sl = slug(src)
    have = idx.get(src, {})
    try:
        files = disk_list(path)
    except Exception as e:
        print(f"  ! {src[:40]}: {str(e)[:70]}", flush=True); return
    from PIL import Image
    got = {}
    for pg in pages:
        i = pg - 1                                # страница N — N-й файл по порядку
        if i < 0 or i >= len(files) or str(pg) in have: continue
        key = f"scans/{sl}/p{pg}.jpg"
        try:
            raw = fetch(files[i]["file"])
            im = Image.open(io.BytesIO(raw)).convert("RGB")
            w, h = im.size
            if w > MAXW: im = im.resize((MAXW, int(h * MAXW / w)))
            buf = io.BytesIO(); im.save(buf, "JPEG", quality=QUALITY)
            s3.put_object(Bucket=SITE, Key=key, Body=buf.getvalue(),
                          ContentType="image/jpeg", CacheControl="public, max-age=2592000")
            got[str(pg)] = key
        except Exception as e:
            with lock: stat["err"] += 1
            if stat["err"] <= 5: print(f"  ! {sl[:30]}/p{pg}: {str(e)[:60]}", flush=True)
    with lock:
        if got:
            idx.setdefault(src, {}).update(got)
            stat["pages"] += len(got); stat["pubs"] += 1
            json.dump(idx, open(HERE / "scan_index.json", "w", encoding="utf-8"), ensure_ascii=False)
            print(f"  [{stat['pubs']}/{len(todo)}] {src[:40]}: +{len(got)} стр. "
                  f"(всего {stat['pages']}, {(time.time()-t0)/60:.1f} мин)", flush=True)

with concurrent.futures.ThreadPoolExecutor(WORKERS) as ex:
    list(ex.map(handle, todo))

json.dump(idx, open(HERE / "scan_index.json", "w", encoding="utf-8"), ensure_ascii=False)
print(f"\nГОТОВО за {(time.time()-t0)/60:.1f} мин: публикаций {stat['pubs']}, страниц {stat['pages']}, ошибок {stat['err']}")
print(f"публикаций в индексе сканов: {len(idx)} | страниц всего: {sum(len(v) for v in idx.values())}")
print("дальше: 63_scan_lines.py (координаты строк для подсветки) и пересборка карты")
