# ЛОКАЛЬНЫЙ OCR ФОТО-СТРАНИЦ (папка = публикация). Потоково В ПАМЯТИ: качает фото по одному ->
# downsample (Pillow) -> исходник ВЫБРАСЫВАЕТ -> копит мелкий чанк -> img2pdf -> Vision async OCR.
# НИЧЕГО не пишет на диск (кроме результата в бакет). Пишет ocrtext/img/<safe>__pX-Y.json (схема как 35)
# -> потом:  python 33_local_extract_from_bucket.py img
# Вход: recon_image_folders.json (ключ img_folders: [[path, n_img], ...]).
# Запуск: VISION_MAX=3 DOC_WORKERS=4 python 50_ocr_images.py
import os, io, re, sys, json, time, base64, threading, random, urllib.request, urllib.parse, urllib.error, collections, concurrent.futures
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sec = {}
for line in open(os.path.join(HERE, ".secrets"), encoding="utf-8"):
    line = line.strip()
    if "=" in line and not line.startswith("#"): k, v = line.split("=", 1); sec[k] = v
FOLDER = sec["YANDEX_FOLDER_ID"]; KEY = sec["YANDEX_API_KEY"]; BUCKET = sec["BUCKET"]
HDR = {"Authorization": "Api-Key " + KEY, "Content-Type": "application/json", "x-folder-id": FOLDER}
OCR_ASYNC = "https://ocr.api.cloud.yandex.net/ocr/v1/recognizeTextAsync"
OCR_GET = "https://ocr.api.cloud.yandex.net/ocr/v1/getRecognition"
OP = "https://operation.api.cloud.yandex.net/operations/"
DISK = "https://cloud-api.yandex.net/v1/disk/public/resources"
PK = "https://disk.yandex.ru/d/jKyK_ioYmjusVQ"
IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}

VISION_MAX = int(os.environ.get("VISION_MAX", 3))    # ГЛОБАЛЬНО одновременных OCR-операций
DOC_WORKERS = int(os.environ.get("DOC_WORKERS", 4))  # папок параллельно
DL_WORKERS = int(os.environ.get("DL_WORKERS", 6))    # скачиваний фото параллельно ВНУТРИ чанка
CHUNK = int(os.environ.get("CHUNK", 20))             # фото на один OCR-вызов (=1 мелкий PDF)
MAXSIDE = int(os.environ.get("MAXSIDE", 2200))       # длинная сторона после downsample (~180 DPI книжной страницы)
JPEGQ = int(os.environ.get("JPEGQ", 75))
VIS = threading.Semaphore(VISION_MAX)
_stat = collections.Counter(); _lock = threading.Lock()

import boto3
s3 = boto3.client("s3", endpoint_url="https://storage.yandexcloud.net", region_name="ru-central1",
                  aws_access_key_id=sec["AWS_ACCESS_KEY_ID"], aws_secret_access_key=sec["AWS_SECRET_ACCESS_KEY"])

def http(url, body=None, method="GET", timeout=180, tries=10):   # для OCR (с авторизацией)
    data = json.dumps(body).encode() if body is not None else None
    for a in range(tries):
        req = urllib.request.Request(url, data=data, method=method)
        for k, v in HDR.items(): req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r: return r.read().decode()
        except urllib.error.HTTPError as e:
            if e.code == 429:
                with _lock: _stat["429"] += 1
            if e.code in (429, 500, 502, 503) and a < tries - 1:
                ra = e.headers.get("Retry-After")
                time.sleep((float(ra) if (ra and ra.isdigit()) else min(30, 2 ** a)) + random.uniform(0, 1.5)); continue
            raise
        except urllib.error.URLError:
            if a < tries - 1: time.sleep(min(30, 2 ** a) + random.uniform(0, 1.5)); continue
            raise

def ext(n):
    i = n.rfind("."); return n[i:].lower() if i >= 0 else ""

def natkey(name):                                    # естественная сортировка: IMG_9 < IMG_10
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]

def list_images(path):                               # ПРЯМЫЕ фото папки (без рекурсии), отсортированы
    out = []; off = 0
    while True:
        q = urllib.parse.urlencode({"public_key": PK, "path": path, "limit": 1000, "offset": off,
                                    "fields": "_embedded.items.name,_embedded.items.type,_embedded.items.path"})
        for _ in range(4):
            try:
                d = json.load(urllib.request.urlopen(DISK + "?" + q, timeout=45)); break
            except Exception: time.sleep(1.0); d = {}
        items = d.get("_embedded", {}).get("items", [])
        if not items: break
        for it in items:
            if it.get("type") == "file" and ext(it["name"]) in IMG_EXT:
                out.append((it["name"], it["path"]))
        off += len(items)
        if len(items) < 1000: break
    return sorted(out, key=lambda x: natkey(x[0]))

def file_href(path):
    q = urllib.parse.urlencode({"public_key": PK, "path": path})
    return json.load(urllib.request.urlopen(DISK + "/download?" + q, timeout=60))["href"]

def downsample_one(fpath):                           # качает 1 фото -> сжатый JPEG-байт; исходник не сохраняется
    from PIL import Image
    raw = urllib.request.urlopen(file_href(fpath), timeout=600).read()
    im = Image.open(io.BytesIO(raw))
    if getattr(im, "n_frames", 1) > 1: im.seek(0)    # многостраничный tif -> первый кадр
    if im.mode not in ("RGB", "L"): im = im.convert("RGB")
    w, h = im.size
    sc = MAXSIDE / max(w, h)
    if sc < 1: im = im.resize((max(1, int(w * sc)), max(1, int(h * sc))), Image.LANCZOS)
    b = io.BytesIO(); im.save(b, "JPEG", quality=JPEGQ)
    im.close(); del raw
    return b.getvalue()

def ocr_pdf_async(b):                                # под глобальным семафором VIS
    with VIS:
        op = json.loads(http(OCR_ASYNC, {"mimeType": "application/pdf", "languageCodes": ["ru", "en"],
                                         "model": "page-column-sort", "content": base64.b64encode(b).decode()}, "POST", 180))["id"]
        for _ in range(200):
            if json.loads(http(OP + op, None, "GET", 30)).get("done"): break
            time.sleep(3)
        pages = []
        for ln in http(OCR_GET + "?operationId=" + op, None, "GET", 180).splitlines():
            if ln.strip().startswith("{"):
                try: pages.append((json.loads(ln).get("result", {}) or {}).get("textAnnotation", {}).get("fullText", ""))
                except Exception: pass
        return pages

def safe_of(path):
    s = path.strip("/").replace("/", "__")
    for ch in ' ?*:<>|"\\\t\n\r': s = s.replace(ch, "_")
    return s[:90]

def key_of(path, pfrom, pto):
    return f"ocrtext/img/{safe_of(path)}__p{pfrom}-{pto}.json"

def exists(k):
    try: s3.head_object(Bucket=BUCKET, Key=k); return True
    except Exception: return False

def process_folder(path, n_expected):
    imgs = list_images(path)
    if not imgs:
        with _lock: _stat["empty"] += 1
        return
    chunks = [(i, imgs[i:i + CHUNK]) for i in range(0, len(imgs), CHUNK)]
    todo = [(st, ch) for st, ch in chunks if not exists(key_of(path, st, st + len(ch) - 1))]
    if not todo:
        with _lock: _stat["skip_folder"] += 1
        return
    tag = path.rsplit("/", 1)[-1][:42]
    for st, ch in todo:
        try:
            # качаем+сжимаем фото чанка параллельно; исходники не оседают
            with concurrent.futures.ThreadPoolExecutor(DL_WORKERS) as ex:
                res = list(ex.map(lambda np: (np[0], _safe_downsample(np[1])), list(enumerate(p for _, p in ch))))
            jpgs = [j for _, j in sorted(res, key=lambda x: x[0]) if j]
            if not jpgs:
                with _lock: print(f"  пусто-чанк {tag} p{st}: все фото битые", flush=True); continue
            import img2pdf
            pdf = img2pdf.convert(jpgs)
            pages = ocr_pdf_async(pdf)
            kept = [{"idx": st + i, "text": t} for i, t in enumerate(pages) if len(t.strip()) > 200]
            out = {"path": path, "page_from": st, "n_pages": len(pages), "n_kept": len(kept), "pages": kept}
            s3.put_object(Bucket=BUCKET, Key=key_of(path, st, st + len(ch) - 1),
                          Body=json.dumps(out, ensure_ascii=False).encode("utf-8"))
            del pdf, jpgs, pages
            with _lock:
                _stat["chunks"] += 1
                print(f"[{_stat['chunks']}] {tag} p{st}-{st+len(ch)-1} img={len(ch)} kept={kept and len(kept)} 429s={_stat['429']}", flush=True)
        except Exception as e:
            with _lock: print(f"  OCR-ОШИБКА {tag} p{st}: {str(e)[:70]}", flush=True)
    with _lock:
        _stat["done_folder"] += 1
        print(f"  ✓ папка [{_stat['done_folder']}]: {tag} ({len(imgs)} фото)", flush=True)

def _safe_downsample(fpath):
    for a in range(3):
        try: return downsample_one(fpath)
        except Exception:
            if a < 2: time.sleep(1.0 + random.uniform(0, 1.0))
    with _lock: _stat["bad_img"] += 1
    return None

def main():
    inp = sys.argv[1] if len(sys.argv) > 1 else "recon_image_folders.json"
    data = json.load(open(os.path.join(HERE, inp), encoding="utf-8"))
    folders = data["img_folders"] if isinstance(data, dict) else data   # [[path, n], ...]
    only = os.environ.get("ONLY")                                        # ONLY="Галай" -> только папки с подстрокой
    if only: folders = [f for f in folders if only.lower() in f[0].lower()]
    limit = int(os.environ.get("LIMIT", 0))
    if limit: folders = folders[:limit]
    total_pages = sum(n for _, n in folders)
    print(f"вход: {inp} | папок: {len(folders)} | фото всего: {total_pages} | "
          f"VISION_MAX={VISION_MAX} DOC_WORKERS={DOC_WORKERS} CHUNK={CHUNK} MAXSIDE={MAXSIDE}", flush=True)
    with concurrent.futures.ThreadPoolExecutor(DOC_WORKERS) as ex:
        list(ex.map(lambda pn: process_folder(pn[0], pn[1]), folders))
    print(f"ГОТОВО. папок={_stat['done_folder']} чанков={_stat['chunks']} уже-были={_stat['skip_folder']} "
          f"пустых={_stat['empty']} битых-фото={_stat['bad_img']} 429_всего={_stat['429']}", flush=True)

if __name__ == "__main__":
    main()
