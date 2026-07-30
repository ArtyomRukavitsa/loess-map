# Обработчик загруженных публикаций: очередь из Object Storage -> OCR -> extract(v2) -> страницы -> геокод -> предложения.
# Пользователь грузит файл через upload.html (fn_ingest выдаёт presigned URL), здесь файл подхватывается и обрабатывается
# ТЕМ ЖЕ конвейером, что и основной корпус, поэтому предложенные разрезы совместимы с картой.
# Запуск разово:      python 57_process_uploads.py
# Дежурный режим:     WATCH=1 python 57_process_uploads.py     (опрос очереди раз в 30 с)
# Один конкретный:    JOB=20260728-153000-ab12ef python 57_process_uploads.py
import os, io, sys, json, time, math, base64, random, shutil, pathlib, importlib.util
import urllib.request, urllib.parse, urllib.error, collections
sys.stdout.reconfigure(encoding="utf-8")
HERE = pathlib.Path(__file__).parent

sec = {}
for line in open(HERE / ".secrets", encoding="utf-8"):
    line = line.strip()
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1); sec[k.strip()] = v.strip()
FOLDER, APIKEY = sec["YANDEX_FOLDER_ID"], sec["YANDEX_API_KEY"]
BUCKET = sec.get("BUCKET", "loess-results")
PREFIX = "uploads/"
WATCH = os.environ.get("WATCH", "0") == "1"
ONLY_JOB = os.environ.get("JOB", "")
MAX_PAGES = int(os.environ.get("MAX_PAGES", 400))       # предохранитель на гигантские PDF
CHUNK = int(os.environ.get("CHUNK", 40))                # страниц за один вызов Vision (как в fn_ocr_text)

import boto3
s3 = boto3.client("s3", endpoint_url="https://storage.yandexcloud.net", region_name="ru-central1",
                  aws_access_key_id=sec["AWS_ACCESS_KEY_ID"], aws_secret_access_key=sec["AWS_SECRET_ACCESS_KEY"])

# ---------- Object Storage helpers ----------
def s3_get(key, default=None):
    try: return json.loads(s3.get_object(Bucket=BUCKET, Key=key)["Body"].read())
    except Exception: return default

def s3_put(key, obj):
    s3.put_object(Bucket=BUCKET, Key=key, Body=json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                  ContentType="application/json")

def set_status(jid, **kw):
    k = f"{PREFIX}{jid}/status.json"
    st = s3_get(k, {}) or {}
    st.update(kw); st["updated"] = int(time.time())
    s3_put(k, st)
    print(f"    [{jid}] {st.get('status')}: {st.get('msg','')}", flush=True)
    return st

# ---------- OCR (Yandex Vision) — та же схема, что в облачной fn_ocr_text ----------
OCR_SYNC = "https://ocr.api.cloud.yandex.net/ocr/v1/recognizeText"
OCR_ASYNC = "https://ocr.api.cloud.yandex.net/ocr/v1/recognizeTextAsync"
OCR_GET = "https://ocr.api.cloud.yandex.net/ocr/v1/getRecognition"
OP = "https://operation.api.cloud.yandex.net/operations/"
HDR = {"Authorization": "Api-Key " + APIKEY, "Content-Type": "application/json", "x-folder-id": FOLDER}
IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".gif"}
DPI = int(os.environ.get("OCR_DPI", 180))

def http(url, body=None, method="GET", timeout=180, tries=8):
    data = json.dumps(body).encode() if body is not None else None
    for a in range(tries):
        req = urllib.request.Request(url, data=data, method=method)
        for k, v in HDR.items(): req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r: return r.read().decode()
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and a < tries - 1:
                ra = e.headers.get("Retry-After")
                time.sleep((float(ra) if (ra and ra.isdigit()) else min(30, 2 ** a)) + random.uniform(0, 1.5)); continue
            raise
        except urllib.error.URLError:
            if a < tries - 1: time.sleep(min(30, 2 ** a) + random.uniform(0, 1.5)); continue
            raise

def downsample_range(data, pfrom, pto, dpi=DPI, q=75):   # рендер в JPEG -> компактный PDF (гиганты влезают в лимит Vision)
    import fitz, img2pdf
    doc = fitz.open(stream=data, filetype="pdf")
    jpgs = []
    for i in range(pfrom, min(pto, doc.page_count)):
        jpgs.append(doc[i].get_pixmap(dpi=dpi).tobytes("jpeg", jpg_quality=q))
    doc.close()
    return img2pdf.convert(jpgs) if jpgs else None

def pdf_page_count(data):
    import fitz
    doc = fitz.open(stream=data, filetype="pdf"); n = doc.page_count; doc.close(); return n

def ocr_pdf_async(b):
    op = json.loads(http(OCR_ASYNC, {"mimeType": "application/pdf", "languageCodes": ["ru", "en"],
                                     "model": "page-column-sort", "content": base64.b64encode(b).decode()},
                         "POST", 120))["id"]
    for _ in range(200):
        if json.loads(http(OP + op, None, "GET", 30)).get("done"): break
        time.sleep(3)
    pages = []
    for ln in http(OCR_GET + "?operationId=" + op, None, "GET", 180).splitlines():
        if ln.strip().startswith("{"):
            try: pages.append((json.loads(ln).get("result", {}) or {}).get("textAnnotation", {}).get("fullText", ""))
            except Exception: pass
    return pages

def ocr_image_sync(b):
    from PIL import Image
    im = Image.open(io.BytesIO(b)).convert("RGB"); w, h = im.size
    s = min(1.0, 2500 / max(w, h))
    if s < 1.0: im = im.resize((int(w * s), int(h * s)))
    o = io.BytesIO(); im.save(o, "JPEG", quality=85)
    d = json.loads(http(OCR_SYNC, {"mimeType": "image/jpeg", "languageCodes": ["ru", "en"],
                                   "model": "page-column-sort",
                                   "content": base64.b64encode(o.getvalue()).decode()}, "POST", 120))
    return [(d.get("result", {}) or {}).get("textAnnotation", {}).get("fullText", "")]

def ocr_file(data, ext, jid):
    """Возвращает [(idx, text)] — постранично, как в основном корпусе."""
    if ext in IMG_EXT:
        return [(i, t) for i, t in enumerate(ocr_image_sync(data)) if len(t.strip()) > 200]
    if ext != ".pdf":
        raise RuntimeError(f"формат {ext} не поддержан")
    total = min(pdf_page_count(data), MAX_PAGES)
    out = []
    for start in range(0, total, CHUNK):
        stop = min(start + CHUNK, total)
        set_status(jid, status="processing", msg=f"OCR страниц {start + 1}–{stop} из {total}")
        chunk = downsample_range(data, start, stop)
        if not chunk: continue
        for i, t in enumerate(ocr_pdf_async(chunk)):
            if len(t.strip()) > 200: out.append((start + i, t))
    return out

# ---------- извлечение: переиспользуем движок 52_extract_v2 ----------
os.environ.setdefault("MODELS", "deepseek-v32:3,qwen3.6-35b-a3b:1")   # связка из бенчмарка (быстрая и точная)
os.environ.setdefault("PER_MODEL", "6")
_spec = importlib.util.spec_from_file_location("extract_v2", HERE / "52_extract_v2.py")
EX = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(EX)

# ---------- привязка фразы к странице (как в 56_page_link_v2) ----------
import re
def norm(s):
    s = (s or "").lower().replace("ё", "е")
    return re.sub(r"[^0-9a-zа-я]+", " ", s).strip()

def find_page(pages_norm, ev):
    ev = norm(ev)
    if len(ev) < 12: return None
    probe = ev[:60]
    for idx, txt in pages_norm:
        if probe in txt: return idx
    words = ev.split()
    for n in (8, 6, 4):
        frag = " ".join(words[:n])
        if len(frag) < 12: continue
        for idx, txt in pages_norm:
            if frag in txt: return idx
    tok = set(w for w in words if len(w) >= 5) or set(w for w in words if len(w) >= 4)
    if len(tok) < 3: return None
    best, best_sc = None, 0
    for idx, txt in pages_norm:
        sc = len(tok & set(txt.split()))
        if sc > best_sc: best_sc, best = sc, idx
    return best if best_sc >= max(3, int(0.6 * len(tok))) else None

# ---------- геокод (кэши общие с 55_geocode_v2) ----------
UA = "loess-sections/1.0 (research)"
CIS = "ru,ua,kz,by,md,ge,az,am,uz,kg,tj,tm,ee,lv,lt"
SETTLE = {"city", "town", "village", "hamlet", "municipality", "administrative", "suburb",
          "borough", "isolated_dwelling", "locality", "quarter"}
_gc_path = HERE / "geocache_sections.json"
c_sec = json.load(open(_gc_path, encoding="utf-8")) if _gc_path.exists() else {}

def geocode(name, admin):
    key = name + "|" + (admin or "")
    if key in c_sec: return c_sec[key]
    q = name + (", " + admin if admin else "")
    res = None
    try:
        r = urllib.request.Request("https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
            {"q": q, "format": "json", "limit": 5, "countrycodes": CIS}), headers={"User-Agent": UA})
        for it in json.load(urllib.request.urlopen(r, timeout=30)):
            if it.get("class") in ("place", "boundary") and it.get("type") in SETTLE:
                la, lo = float(it["lat"]), float(it["lon"])
                if 41 <= la <= 82 and 19 <= lo <= 180: res = [la, lo]; break
    except Exception:
        res = None
    c_sec[key] = res
    time.sleep(1.05)                      # вежливый лимит Nominatim
    return res

# ---------- консолидация записей в предложения ----------
def val(r, f):
    v = (r.get(f) or {}).get("value")
    return v if v not in (None, "", "ND", []) else None

def uniq(seq, cap=None):
    out = list(dict.fromkeys([x for x in seq if x not in (None, "", "ND")]))
    return out[:cap] if cap else out

def build_proposals(recs, pages_norm):
    by = collections.defaultdict(list)
    for r in recs:
        loc = val(r, "nearest_locality")
        if loc: by[str(loc).strip()].append(r)
    props = []
    for loc, rs in sorted(by.items(), key=lambda kv: -len(kv[1])):
        ev, pages = [], set()
        for r in rs:
            for f, v in r.items():
                if isinstance(v, dict) and isinstance(v.get("evidence"), str):
                    e = v["evidence"].strip()
                    if e and e not in ("[]", "ND") and e not in ev:
                        ev.append(e)
                        p = find_page(pages_norm, e)
                        if p is not None: pages.add(p + 1)
        th = []
        for r in rs:
            for t in (r.get("thickness") or []):
                v = t.get("value_m")
                if v and v != "ND": th.append(f"{t.get('kind', 'unspecified')}: {v} м")
        admin = next((val(r, "administrative_unit") for r in rs if val(r, "administrative_unit")), None)
        props.append({
            "locality": loc, "admin": admin or "ND",
            "excavation": uniq([val(r, "excavation_type") for r in rs]),
            "geomorph": uniq([val(r, "geomorphic_position") for r in rs], 3),
            "deposits": uniq([d for r in rs for d in (val(r, "type_of_deposits") or [])]),
            "raw_terms": uniq([d for r in rs for d in (val(r, "deposit_raw_terms") or [])], 8),
            "thickness": uniq(th, 6),
            "elevation": next((val(r, "absolute_elevation_m") for r in rs if val(r, "absolute_elevation_m")), "ND"),
            "strat": uniq([d for r in rs for d in (val(r, "stratigraphic_position") or [])]),
            "dating": uniq([d for r in rs for d in (val(r, "dating_methods") or [])]),
            "source_kinds": uniq([r.get("source_kind") for r in rs]),
            "evidence": ev[:8], "pages": sorted(pages)[:10], "n_records": len(rs),
        })
    return props

# ---------- обработка одной задачи ----------
def process_job(jid, st):
    fname = st.get("filename", "upload.pdf")
    ext = ("." + fname.rsplit(".", 1)[-1].lower()) if "." in fname else ".pdf"
    print(f"\n=== задача {jid}: {fname} ===", flush=True)
    set_status(jid, status="processing", msg="скачиваю файл")
    key = f"{PREFIX}{jid}/source{ext}"
    data = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()

    pages = ocr_file(data, ext, jid)
    if not pages:
        return set_status(jid, status="error", msg="OCR не дал текста (скан пустой или нечитаемый)")
    # OCR-текст в бакет — чтобы переиспользовать и чтобы работала привязка страниц
    s3_put(f"ocrtext/upload/{jid}.json",
           {"path": f"upload/{jid}/{fname}", "page_from": 0, "n_pages": len(pages),
            "n_kept": len(pages), "pages": [{"idx": i, "text": t} for i, t in pages]})

    set_status(jid, status="processing", msg=f"извлекаю данные ({len(pages)} стр.)")
    job_out = HERE / "uploads_local" / jid
    job_out.mkdir(parents=True, exist_ok=True)
    EX.OUT = str(job_out)                                  # вывод движка — в папку задачи, основной корпус не трогаем
    out = EX.process_doc(f"upload/{jid}/{fname}", pages) or {}
    recs = out.get("records", [])
    if not recs:
        return set_status(jid, status="ready", msg="разрезы не найдены в публикации", n_proposals=0)

    pages_norm = [(i, norm(t)) for i, t in pages]
    props = build_proposals(recs, pages_norm)

    set_status(jid, status="processing", msg=f"геокодирую {len(props)} объектов")
    for i, p in enumerate(props):
        c = geocode(p["locality"], None if p["admin"] == "ND" else p["admin"])
        p["lat"], p["lon"] = (c[0], c[1]) if c else (None, None)
        p["i"] = i
    json.dump(c_sec, open(_gc_path, "w", encoding="utf-8"), ensure_ascii=False)

    s3_put(f"{PREFIX}{jid}/proposals.json", props)
    geo = sum(1 for p in props if p["lat"] is not None)
    set_status(jid, status="ready", n_proposals=len(props),
               msg=f"готово: {len(props)} объектов ({geo} с координатами) — нужна проверка")
    print(f"    предложений: {len(props)} | с координатами: {geo} | записей: {len(recs)}", flush=True)

def queued_jobs():
    jobs, tok = [], None
    while True:
        kw = {"Bucket": BUCKET, "Prefix": PREFIX, "MaxKeys": 1000}
        if tok: kw["ContinuationToken"] = tok
        r = s3.list_objects_v2(**kw)
        jobs += [o["Key"] for o in r.get("Contents", []) if o["Key"].endswith("/status.json")]
        tok = r.get("NextContinuationToken")
        if not r.get("IsTruncated"): break
    out = []
    for k in jobs:
        st = s3_get(k)
        if not st: continue
        jid = st.get("job_id") or k.split("/")[1]
        if ONLY_JOB and jid != ONLY_JOB: continue
        if st.get("status") == "queued" or (ONLY_JOB and st.get("status") != "processing"):
            out.append((jid, st))
    return out

def main():
    while True:
        jobs = queued_jobs()
        if jobs:
            print(f"в очереди: {len(jobs)}", flush=True)
            for jid, st in jobs:
                try:
                    process_job(jid, st)
                except Exception as e:
                    set_status(jid, status="error", msg=str(e)[:200])
                    print(f"    ОШИБКА {jid}: {e}", flush=True)
        elif not WATCH:
            print("очередь пуста", flush=True)
        if not WATCH: break
        time.sleep(30)

if __name__ == "__main__":
    main()
