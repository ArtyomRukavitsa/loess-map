# ЛОКАЛЬНЫЙ OCR остатка. Качает каждый док ОДИН раз, режет все его куски, OCR через ГЛОБАЛЬНЫЙ потолок Vision.
# Пишет тексты в бакет ocrtext/ocr/<safe>__pX-Y.json (как fn-ocr-text) -> 33 подхватит. Резюм: готовые куски пропускает.
# Запуск: python 35_local_ocr.py ocr_input_1_remaining.json
import os, io, sys, json, time, base64, threading, random, urllib.request, urllib.parse, urllib.error, collections, concurrent.futures
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

VISION_MAX = int(os.environ.get("VISION_MAX", 2))    # ГЛОБАЛЬНО одновременных OCR-операций (жёсткий потолок)
DOC_WORKERS = int(os.environ.get("DOC_WORKERS", 3))
VIS = threading.Semaphore(VISION_MAX)
_stat = collections.Counter(); _lock = threading.Lock()

import boto3
s3 = boto3.client("s3", endpoint_url="https://storage.yandexcloud.net", region_name="ru-central1",
                  aws_access_key_id=sec["AWS_ACCESS_KEY_ID"], aws_secret_access_key=sec["AWS_SECRET_ACCESS_KEY"])

def http(url, body=None, method="GET", timeout=120, tries=10):
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

def disk_download(path):
    q = urllib.parse.urlencode({"public_key": PK, "path": path})
    href = json.load(urllib.request.urlopen(DISK + "/download?" + q, timeout=60))["href"]
    return urllib.request.urlopen(href, timeout=900).read()

def downsample_range(data, pfrom, pto, dpi=180, q=75):        # fitz (память-безопасно) + рендер 180 DPI JPEG -> мелкий PDF
    import fitz, img2pdf
    doc = fitz.open(stream=data, filetype="pdf")
    jpgs = [doc[i].get_pixmap(dpi=dpi).tobytes("jpeg", jpg_quality=q) for i in range(pfrom, min(pto, doc.page_count))]
    doc.close()
    return img2pdf.convert(jpgs)

def ocr_pdf_async(b):                                # под глобальным семафором VIS
    with VIS:
        op = json.loads(http(OCR_ASYNC, {"mimeType": "application/pdf", "languageCodes": ["ru", "en"], "model": "page-column-sort", "content": base64.b64encode(b).decode()}, "POST", 180))["id"]
        for _ in range(200):
            if json.loads(http(OP + op, None, "GET", 30)).get("done"): break
            time.sleep(3)
        pages = []
        for ln in http(OCR_GET + "?operationId=" + op, None, "GET", 180).splitlines():
            if ln.strip().startswith("{"):
                try: pages.append((json.loads(ln).get("result", {}) or {}).get("textAnnotation", {}).get("fullText", ""))
                except Exception: pass
        return pages

def key_of(path, pfrom, pto):
    return f"ocrtext/arch/{path.rsplit('/',1)[-1].replace(' ','_')[:80]}__p{pfrom}-{pto}.json"

def exists(k):
    try: s3.head_object(Bucket=BUCKET, Key=k); return True
    except Exception: return False

def process_doc(path, ranges):
    todo = [(pf, pt) for pf, pt in ranges if not exists(key_of(path, pf, pt))]
    if not todo:
        with _lock: _stat["skip_doc"] += 1
        return
    try:
        t0 = time.time(); data = disk_download(path)
        with _lock: print(f"  скачан {path.rsplit('/',1)[-1][:44]} {len(data)/1e6:.0f}МБ {round(time.time()-t0)}с, кусков={len(todo)}", flush=True)
    except Exception as e:
        with _lock: print(f"  СКАЧ-ОШИБКА {path.rsplit('/',1)[-1][:40]}: {str(e)[:60]}", flush=True)
        return
    for pf, pt in todo:
        try:
            sl = downsample_range(data, pf, pt)                # рендер+сжатие -> мелкий PDF (гиганты -> норма)
            pages = ocr_pdf_async(sl)
            kept = [{"idx": pf + i, "text": t} for i, t in enumerate(pages) if len(t.strip()) > 200]
            out = {"path": path, "page_from": pf, "n_pages": len(pages), "n_kept": len(kept), "pages": kept}
            s3.put_object(Bucket=BUCKET, Key=key_of(path, pf, pt), Body=json.dumps(out, ensure_ascii=False).encode("utf-8"))
            with _lock:
                _stat["done"] += 1
                print(f"[{_stat['done']}] {path.rsplit('/',1)[-1][:40]} p{pf}-{pt} стр={len(pages)} kept={len(kept)} 429s={_stat['429']}", flush=True)
        except Exception as e:
            with _lock: print(f"  OCR-ОШИБКА {path.rsplit('/',1)[-1][:36]} p{pf}-{pt}: {str(e)[:60]}", flush=True)

def main():
    inp = sys.argv[1] if len(sys.argv) > 1 else "ocr_input_1_remaining.json"
    items = json.load(open(os.path.join(HERE, inp), encoding="utf-8"))["items"]
    byd = collections.defaultdict(list)
    for it in items:
        byd[it["path"]].append((it.get("page_from", 0), it.get("page_to", 20)))
    print(f"вход: {inp} | доков: {len(byd)} | кусков: {len(items)} | VISION_MAX={VISION_MAX} DOC_WORKERS={DOC_WORKERS}", flush=True)
    with concurrent.futures.ThreadPoolExecutor(DOC_WORKERS) as ex:
        list(ex.map(lambda kv: process_doc(*kv), byd.items()))
    print(f"ГОТОВО. done={_stat['done']} skip_doc={_stat['skip_doc']} 429_всего={_stat['429']}", flush=True)

if __name__ == "__main__":
    main()
