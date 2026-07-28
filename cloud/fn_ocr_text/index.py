# Cloud Function: fn_ocr_text — OCR документа/диапазона -> ТЕКСТЫ страниц в бакет (для batch-извлечения). БЕЗ LLM.
# PDF: pypdf вырезает диапазон -> async Vision OCR (каждая страница OCR-ится 1 раз, без re-OCR). Картинка: sync OCR.
# Отделяет OCR (дёшево, вне лимита sync-gen) от извлечения (batch). Хранит текст -> переиспользуем, не OCR-им дважды.
# Вход:  {"public_key","path"[,"page_from","page_to","run_id"]}
# Выход: {"path","n_pages","n_kept","key"[,"error"]} ; пишет ocrtext/<run>/<safe>[__pX-Y].json
# Env: YC_FOLDER_ID, YC_API_KEY, BUCKET, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
import os, io, json, time, base64, random, urllib.request, urllib.parse, urllib.error

FOLDER = os.environ["YC_FOLDER_ID"]; APIKEY = os.environ["YC_API_KEY"]; BUCKET = os.environ.get("BUCKET")
OCR = "https://ocr.api.cloud.yandex.net/ocr/v1/recognizeText"
OCR_ASYNC = "https://ocr.api.cloud.yandex.net/ocr/v1/recognizeTextAsync"
OCR_GET = "https://ocr.api.cloud.yandex.net/ocr/v1/getRecognition"
OP = "https://operation.api.cloud.yandex.net/operations/"
DISK = "https://cloud-api.yandex.net/v1/disk/public/resources"
HDR = {"Authorization": "Api-Key " + APIKEY, "Content-Type": "application/json", "x-folder-id": FOLDER}
IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".gif"}

_s3 = None
def put(key, obj):
    global _s3
    if not BUCKET: return
    if _s3 is None:
        import boto3
        _s3 = boto3.client("s3", endpoint_url="https://storage.yandexcloud.net", region_name="ru-central1")
    _s3.put_object(Bucket=BUCKET, Key=key, Body=json.dumps(obj, ensure_ascii=False).encode("utf-8"))

def http(url, body=None, method="GET", timeout=120, tries=8):
    data = json.dumps(body).encode() if body is not None else None
    for a in range(tries):
        req = urllib.request.Request(url, data=data, method=method)
        for k, v in HDR.items(): req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r: return r.read().decode()
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and a < tries - 1:
                ra = e.headers.get("Retry-After")                 # экспонента+джиттер+Retry-After — впитываем 429 Vision
                time.sleep((float(ra) if (ra and ra.isdigit()) else min(30, 2 ** a)) + random.uniform(0, 1.5)); continue
            raise
        except urllib.error.URLError:
            if a < tries - 1: time.sleep(min(30, 2 ** a) + random.uniform(0, 1.5)); continue
            raise

def disk_download(pk, path):
    q = urllib.parse.urlencode({"public_key": pk, "path": path})
    href = json.load(urllib.request.urlopen(DISK + "/download?" + q, timeout=60))["href"]
    return urllib.request.urlopen(href, timeout=300).read()

DPI = int(os.environ.get("OCR_DPI", 180))
def downsample_range(data, pfrom, pto, dpi=DPI, q=75):        # рендер страниц в JPEG(dpi) -> компактный PDF (гиганты -> норма)
    import fitz, img2pdf
    doc = fitz.open(stream=data, filetype="pdf")
    jpgs = []
    for i in range(pfrom, min(pto, doc.page_count)):
        pix = doc[i].get_pixmap(dpi=dpi)
        jpgs.append(pix.tobytes("jpeg", jpg_quality=q))
    doc.close()
    return img2pdf.convert(jpgs)

def prep_image(b, max_side=2500, q=85):
    from PIL import Image
    im = Image.open(io.BytesIO(b)).convert("RGB"); w, h = im.size; s = min(1.0, max_side / max(w, h))
    if s < 1.0: im = im.resize((int(w * s), int(h * s)))
    o = io.BytesIO(); im.save(o, "JPEG", quality=q); return o.getvalue()

def ocr_image_sync(b):
    d = json.loads(http(OCR, {"mimeType": "image/jpeg", "languageCodes": ["ru", "en"], "model": "page-column-sort",
                              "content": base64.b64encode(prep_image(b)).decode()}, "POST", 120))
    return [(d.get("result", {}) or {}).get("textAnnotation", {}).get("fullText", "")]

def ocr_pdf_async(b):
    op = json.loads(http(OCR_ASYNC, {"mimeType": "application/pdf", "languageCodes": ["ru", "en"],
                                     "model": "page-column-sort", "content": base64.b64encode(b).decode()}, "POST", 120))["id"]
    for _ in range(200):
        if json.loads(http(OP + op, None, "GET", 30)).get("done"): break
        time.sleep(3)
    pages = []
    for ln in http(OCR_GET + "?operationId=" + op, None, "GET", 180).splitlines():
        if ln.strip().startswith("{"):
            try: pages.append((json.loads(ln).get("result", {}) or {}).get("textAnnotation", {}).get("fullText", ""))
            except Exception: pass
    return pages

def handler(event, context):
    path = event.get("path", "?")
    try:
        pk = event["public_key"]; run_id = str(event.get("run_id", "ocr"))
        pfrom = int(event.get("page_from", 0)); pto = event.get("page_to")
        pto = int(pto) if pto is not None else None
        ext = "." + path.rsplit(".", 1)[-1].lower() if "." in path else ""
        data = disk_download(pk, path)
        if ext in IMG_EXT:
            pages, base = ocr_image_sync(data), 0
        elif ext == ".pdf":
            pt = pto if pto is not None else pfrom + 60
            data = downsample_range(data, pfrom, pt)          # рендер+JPEG диапазона -> компактный PDF (гиганты -> норма)
            pages, base = ocr_pdf_async(data), pfrom
        else:
            return {"path": path, "error": f"формат {ext}", "n_pages": 0, "n_kept": 0, "key": None}
        kept = [{"idx": base + i, "text": t} for i, t in enumerate(pages) if len(t.strip()) > 200]
        safe = path.rsplit("/", 1)[-1].replace(" ", "_")[:80]
        rng = f"__p{pfrom}-{pfrom + len(pages)}" if pto is not None else ""
        key = f"ocrtext/{run_id}/{safe}{rng}.json"
        put(key, {"path": path, "page_from": pfrom, "n_pages": len(pages), "n_kept": len(kept), "pages": kept})
        return {"path": path, "n_pages": len(pages), "n_kept": len(kept), "key": key}
    except Exception as e:
        return {"path": path, "error": str(e)[:150], "n_pages": 0, "n_kept": 0, "key": None}
