# Координаты строк на отрендеренных сканах — чтобы просмотрщик подсвечивал найденную фразу.
# Распознаём ТУ САМУЮ картинку, которую показываем, поэтому координаты совпадают с ней один в один
# и ничего пересчитывать не нужно.
# Рядом со сканом кладём scans/<slug>/p<N>.lines.json:  {"w":ширина,"h":высота,"lines":[[текст,x0,y0,x1,y1],...]}
# Возобновляемо: уже посчитанные страницы пропускаются.
#   python 63_scan_lines.py            — все страницы из scan_index.json
#   LIMIT=20 python 63_scan_lines.py   — первые N (для пробы)
import os, sys, json, time, base64, random, pathlib, threading
import urllib.request, urllib.error, concurrent.futures
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
SITE = os.environ.get("SITE_BUCKET", "loess-map")
OCR = "https://ocr.api.cloud.yandex.net/ocr/v1/recognizeText"
HDR = {"Authorization": "Api-Key " + sec["YANDEX_API_KEY"], "Content-Type": "application/json",
       "x-folder-id": sec["YANDEX_FOLDER_ID"]}
WORKERS = int(os.environ.get("WORKERS", 8))
LIMIT = int(os.environ.get("LIMIT", 0))

def http(body, tries=6):
    for a in range(tries):
        req = urllib.request.Request(OCR, data=json.dumps(body).encode(), method="POST")
        for k, v in HDR.items(): req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=120) as r: return r.read().decode()
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and a < tries - 1:
                ra = e.headers.get("Retry-After")
                time.sleep((float(ra) if (ra and ra.isdigit()) else min(20, 2 ** a)) + random.uniform(0, 1.2)); continue
            raise
        except urllib.error.URLError:
            if a < tries - 1: time.sleep(min(20, 2 ** a) + random.uniform(0, 1.2)); continue
            raise

def box(bb):
    xs = [int(v["x"]) for v in bb["vertices"]]; ys = [int(v["y"]) for v in bb["vertices"]]
    return min(xs), min(ys), max(xs), max(ys)

idx = json.load(open(HERE / "scan_index.json", encoding="utf-8"))
tasks = [(src, pg, key) for src, pages in idx.items() for pg, key in pages.items()]
tasks.sort(key=lambda t: t[2])
if LIMIT: tasks = tasks[:LIMIT]
print(f"страниц-сканов: {len(tasks)} | потоков: {WORKERS}", flush=True)

_lock = threading.Lock()
stat = {"done": 0, "skip": 0, "err": 0, "lines": 0}
t0 = time.time()

def one(t):
    src, pg, key = t
    lk = key.rsplit(".", 1)[0] + ".lines.json"
    try:                                    # уже посчитано — пропускаем
        s3.head_object(Bucket=SITE, Key=lk)
        with _lock: stat["skip"] += 1
        return
    except Exception:
        pass
    try:
        img = s3.get_object(Bucket=SITE, Key=key)["Body"].read()
        d = json.loads(http({"mimeType": "image/jpeg", "languageCodes": ["ru", "en"],
                             "model": "page-column-sort", "content": base64.b64encode(img).decode()}))
        ta = (d.get("result", {}) or {}).get("textAnnotation", {}) or {}
        lines = []
        for b in ta.get("blocks", []):
            for ln in b.get("lines", []):
                txt = (ln.get("text") or "").strip()
                if not txt or not ln.get("boundingBox"): continue
                x0, y0, x1, y1 = box(ln["boundingBox"])
                lines.append([txt, x0, y0, x1, y1])
        out = {"w": int(ta.get("width") or 0), "h": int(ta.get("height") or 0), "lines": lines}
        s3.put_object(Bucket=SITE, Key=lk, Body=json.dumps(out, ensure_ascii=False).encode("utf-8"),
                      ContentType="application/json", CacheControl="public, max-age=2592000")
        with _lock:
            stat["done"] += 1; stat["lines"] += len(lines)
            n = stat["done"] + stat["skip"]
            if n % 100 == 0:
                el = time.time() - t0
                print(f"  [{n}/{len(tasks)}] посчитано {stat['done']}, пропущено {stat['skip']}, "
                      f"ошибок {stat['err']}, {el/60:.1f} мин", flush=True)
    except Exception as e:
        with _lock:
            stat["err"] += 1
            if stat["err"] <= 5: print("  !", key, str(e)[:100], flush=True)

with concurrent.futures.ThreadPoolExecutor(WORKERS) as ex:
    list(ex.map(one, tasks))

print(f"\nГОТОВО. посчитано {stat['done']} | пропущено {stat['skip']} | ошибок {stat['err']} | "
      f"строк всего {stat['lines']} | {(time.time()-t0)/60:.1f} мин", flush=True)
