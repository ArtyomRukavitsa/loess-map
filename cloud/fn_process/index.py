# Cloud Function: fn_process — обработка загруженной публикации БЕЗ участия ноутбука и без ВМ.
# Тот же конвейер, что и локальный 57_process_uploads.py: OCR (Vision) -> извлечение (AI Studio) ->
# привязка фраз к страницам -> геокод -> предложения для проверки.
# Запускается триггером Object Storage на появление файла uploads/<job>/source.* ,
# а также по таймеру (тогда сама подбирает незавершённые задачи) или HTTP-вызовом {"job_id": "..."}.
#
# ВАЖНО: долгоживущую функцию сервис может остановить досрочно, поэтому обработка ВОЗОБНОВЛЯЕМАЯ —
# распознанные страницы сохраняются кусками, и повторный запуск продолжает с места остановки.
#
# ENV: YC_FOLDER_ID, YC_API_KEY, BUCKET, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
#      MODELS (опц., по умолчанию deepseek-v32:3,qwen3.6-35b-a3b:1), CHUNK (опц., 40), MAX_PAGES (опц., 400)
import os, io, re, json, time, base64, random, itertools, threading, collections
import urllib.request, urllib.parse, urllib.error, concurrent.futures
import boto3

FOLDER = os.environ["YC_FOLDER_ID"]; APIKEY = os.environ["YC_API_KEY"]
BUCKET = os.environ.get("BUCKET", "loess-results")
PREFIX = "uploads/"
CHUNK = int(os.environ.get("CHUNK", 40))
MAX_PAGES = int(os.environ.get("MAX_PAGES", 400))
DPI = int(os.environ.get("OCR_DPI", 180))
TIME_BUDGET = int(os.environ.get("TIME_BUDGET", 2400))       # с; дальше сохраняемся и выходим (продолжит следующий запуск)
_t0 = time.time()
def left(): return TIME_BUDGET - (time.time() - _t0)

s3 = boto3.client("s3", endpoint_url="https://storage.yandexcloud.net", region_name="ru-central1",
                  aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
                  aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"])

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
    print(f"[{jid}] {st.get('status')}: {st.get('msg','')}", flush=True)
    return st

# ---------------- OCR (Yandex Vision) ----------------
OCR_SYNC = "https://ocr.api.cloud.yandex.net/ocr/v1/recognizeText"
OCR_ASYNC = "https://ocr.api.cloud.yandex.net/ocr/v1/recognizeTextAsync"
OCR_GET = "https://ocr.api.cloud.yandex.net/ocr/v1/getRecognition"
OP = "https://operation.api.cloud.yandex.net/operations/"
HDR = {"Authorization": "Api-Key " + APIKEY, "Content-Type": "application/json", "x-folder-id": FOLDER}
IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".gif"}

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

def downsample(data, pfrom, pto):
    import fitz, img2pdf
    doc = fitz.open(stream=data, filetype="pdf")
    jpgs = [doc[i].get_pixmap(dpi=DPI).tobytes("jpeg", jpg_quality=75)
            for i in range(pfrom, min(pto, doc.page_count))]
    doc.close()
    return img2pdf.convert(jpgs) if jpgs else None

def page_count(data):
    import fitz
    doc = fitz.open(stream=data, filetype="pdf"); n = doc.page_count; doc.close(); return n

def ocr_pdf(b):
    op = json.loads(http(OCR_ASYNC, {"mimeType": "application/pdf", "languageCodes": ["ru", "en"],
                                     "model": "page-column-sort", "content": base64.b64encode(b).decode()},
                         "POST", 120))["id"]
    for _ in range(200):
        if json.loads(http(OP + op, None, "GET", 30)).get("done"): break
        time.sleep(3)
    out = []
    for ln in http(OCR_GET + "?operationId=" + op, None, "GET", 180).splitlines():
        if ln.strip().startswith("{"):
            try: out.append((json.loads(ln).get("result", {}) or {}).get("textAnnotation", {}).get("fullText", ""))
            except Exception: pass
    return out

def ocr_image(b):
    from PIL import Image
    im = Image.open(io.BytesIO(b)).convert("RGB"); w, h = im.size
    sc = min(1.0, 2500 / max(w, h))
    if sc < 1.0: im = im.resize((int(w * sc), int(h * sc)))
    o = io.BytesIO(); im.save(o, "JPEG", quality=85)
    d = json.loads(http(OCR_SYNC, {"mimeType": "image/jpeg", "languageCodes": ["ru", "en"],
                                   "model": "page-column-sort",
                                   "content": base64.b64encode(o.getvalue()).decode()}, "POST", 120))
    return [(d.get("result", {}) or {}).get("textAnnotation", {}).get("fullText", "")]

# ---------------- извлечение (та же схема и промпт, что на основном корпусе) ----------------
LLM = "https://llm.api.cloud.yandex.net/v1/chat/completions"
_spec = [s.split(":") for s in os.environ.get("MODELS", "deepseek-v32:3,qwen3.6-35b-a3b:1").split(",")]
MODELS = [m[0] for m in _spec]
WEIGHTS = {m[0]: (int(m[1]) if len(m) > 1 else 1) for m in _spec}
CYCLE = []; _pool = dict(WEIGHTS)
while sum(_pool.values()) > 0:
    for m in MODELS:
        if _pool[m] > 0: CYCLE.append(m); _pool[m] -= 1
_rr = itertools.count()
PER_MODEL = int(os.environ.get("PER_MODEL", 6))
MODEL_SEM = {m: threading.Semaphore(PER_MODEL) for m in MODELS}
_err = {"n": 0, "last": ""}
_err_lock = threading.Lock()

def field(v): return {"type": "object", "additionalProperties": False, "required": ["value", "evidence"],
                      "properties": {"value": v, "evidence": {"type": "string"}}}
DEP = ["loess", "loess-like loam", "loess-like silty loam", "cover loam", "paleosol",
       "alluvium", "proluvium", "slope deposits", "till", "fluvioglacial",
       "lacustrine", "marine deposits", "aeolian sand", "eluvium", "volcanic ash"]
STR = ["Lower Pleistocene", "Middle Pleistocene", "Upper Pleistocene", "Holocene"]
DAT = ["14C", "OSL", "TL", "magnetostratigraphy", "(U-Th)/He"]
EXC = ["outcrop", "borehole", "pit", "trench", "clearing", "quarry", "unspecified"]
THI = {"type": "object", "additionalProperties": False, "required": ["kind", "value_m", "evidence"],
       "properties": {"kind": {"type": "string", "enum": ["studied", "visible", "borehole_depth", "unspecified"]},
                      "value_m": {"type": "string"}, "evidence": {"type": "string"}}}
REC = {"type": "object", "additionalProperties": False,
       "required": ["nearest_locality", "administrative_unit", "excavation_type", "geomorphic_position",
                    "deposit_raw_terms", "type_of_deposits", "thickness", "absolute_elevation_m",
                    "stratigraphic_position", "dating_methods", "source_kind"],
       "properties": {
           "nearest_locality": field({"type": "string"}),
           "administrative_unit": field({"type": "string"}),
           "excavation_type": field({"type": "string", "enum": EXC}),
           "geomorphic_position": field({"type": "string"}),
           "deposit_raw_terms": field({"type": "array", "items": {"type": "string"}}),
           "type_of_deposits": field({"type": "array", "items": {"type": "string", "enum": DEP}}),
           "thickness": {"type": "array", "items": THI},
           "absolute_elevation_m": field({"type": "string"}),
           "stratigraphic_position": field({"type": "array", "items": {"type": "string", "enum": STR}}),
           "dating_methods": field({"type": "array", "items": {"type": "string", "enum": DAT}}),
           "source_kind": {"type": "string", "enum": ["prose", "table", "caption", "mixed"]}}}
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["records"],
          "properties": {"records": {"type": "array", "items": REC}}}
SYS = ("Ты — извлекатель научных данных по лёссам/четвертичке. Из фрагмента извлеки записи о КОНКРЕТНЫХ разрезах с данными. "
       "ВАЖНО: ищи значения И в прозе, И в ТАБЛИЦАХ, И в ПОДПИСЯХ к рисункам/стратиграфическим колонкам — не только в основном тексте. "
       "Если таблица содержит столбцы разрез/мощность/высота/датировка — извлеки каждую строку как отдельную запись с её значениями. "
       "nearest_locality — ТОЛЬКО реальный топоним (село/город/река/урочище). НЕ считай за локацию фамилии авторов, номера разрезов, "
       "названия глав/таблиц, заголовки. "
       "excavation_type — тип вскрытия: outcrop(обнажение) / borehole(скважина) / pit(шурф) / trench(расчистка/канава) / quarry(карьер) / unspecified. "
       "geomorphic_position — геоморфологическая позиция ДОСЛОВНО как в тексте (напр. 'вторая надпойменная терраса', 'водораздел', 'склон балки', 'пойма'). "
       "type_of_deposits — сопоставь с категорией из списка. deposit_raw_terms — сохрани ДОСЛОВНЫЙ русский термин(ы) как в тексте "
       "(напр. 'покровный суглинок', 'лёссовидный суглинок', 'делювиальные отложения'). "
       "thickness — массив измерений; у каждого kind: studied (мощность изученной толщи) / visible (видимая мощность обнажения) / "
       "borehole_depth (глубина скважины или расчистки) / unspecified (тип мощности не указан); value_m — число или диапазон. "
       "source_kind — откуда взяты данные записи: prose / table / caption / mixed. "
       "На каждое поле — КОРОТКАЯ цитата evidence (до ~20 слов, не копируй абзацы); нет основания -> 'ND'/[]. Не выдумывать. Координаты не извлекать. "
       "Названия кириллицей. Стадии -> Lower/Middle/Upper Pleistocene/Holocene. Нет разреза -> records: [].")

def extract_page(text):
    if not text or len(text.strip()) <= 200: return []
    m = CYCLE[next(_rr) % len(CYCLE)]
    try:
        with MODEL_SEM[m]:
            resp = http(LLM, {"model": f"gpt://{FOLDER}/{m}/latest", "temperature": 0, "max_tokens": 5000,
                              "messages": [{"role": "system", "content": SYS},
                                           {"role": "user", "content": f'Фрагмент:\n"""\n{text[:6000]}\n"""\nИзвлеки записи.'}],
                              "response_format": {"type": "json_schema",
                                                  "json_schema": {"name": "recs", "strict": True, "schema": SCHEMA}}},
                        "POST", 180)
        c = json.loads(resp).get("choices", [{}])[0].get("message", {}).get("content")
        return json.loads(c).get("records", []) if c else []
    except Exception as e:
        # Ошибки считаем: если модель не ответила НИ РАЗУ, это не «разрезов нет», а сбой — и он должен быть виден
        with _err_lock: _err["n"] += 1; _err["last"] = str(e)[:150]
        print("extract error:", str(e)[:150], flush=True)
        return []

# ---------------- привязка фразы к странице ----------------
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
    best, sc0 = None, 0
    for idx, txt in pages_norm:
        sc = len(tok & set(txt.split()))
        if sc > sc0: sc0, best = sc, idx
    return best if sc0 >= max(3, int(0.6 * len(tok))) else None

# ---------------- геокод (кэш живёт в бакете) ----------------
UA = "loess-sections/1.0 (research)"
CIS = "ru,ua,kz,by,md,ge,az,am,uz,kg,tj,tm,ee,lv,lt"
SETTLE = {"city", "town", "village", "hamlet", "municipality", "administrative", "suburb",
          "borough", "isolated_dwelling", "locality", "quarter"}
GC_KEY = "caches/geocache_upload.json"

def geocode(name, admin, cache):
    key = name + "|" + (admin or "")
    if key in cache: return cache[key]
    res = None
    try:
        q = urllib.parse.urlencode({"q": name + (", " + admin if admin else ""), "format": "json",
                                    "limit": 5, "countrycodes": CIS})
        r = urllib.request.Request("https://nominatim.openstreetmap.org/search?" + q, headers={"User-Agent": UA})
        for it in json.load(urllib.request.urlopen(r, timeout=30)):
            if it.get("class") in ("place", "boundary") and it.get("type") in SETTLE:
                la, lo = float(it["lat"]), float(it["lon"])
                if 41 <= la <= 82 and 19 <= lo <= 180: res = [la, lo]; break
    except Exception:
        res = None
    cache[key] = res
    time.sleep(1.05)
    return res

# ---------------- сборка предложений ----------------
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
        th = [f"{t.get('kind','unspecified')}: {t.get('value_m')} м"
              for r in rs for t in (r.get("thickness") or []) if t.get("value_m") and t.get("value_m") != "ND"]
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

# ---------------- обработка одной задачи ----------------
def process(jid):
    st = s3_get(f"{PREFIX}{jid}/status.json") or {}
    fname = st.get("filename", "upload.pdf")
    ext = ("." + fname.rsplit(".", 1)[-1].lower()) if "." in fname else ".pdf"
    ocr_key = f"ocrtext/upload/{jid}.json"

    prev = s3_get(ocr_key, {}) or {}
    pages = [(p["idx"], p["text"]) for p in prev.get("pages", [])]
    done_upto = prev.get("done_upto", 0)                     # сколько страниц уже распознано (для возобновления)

    if not prev.get("complete"):
        set_status(jid, status="processing", msg="скачиваю файл")
        data = s3.get_object(Bucket=BUCKET, Key=f"{PREFIX}{jid}/source{ext}")["Body"].read()
        if ext in IMG_EXT:
            pages = [(i, t) for i, t in enumerate(ocr_image(data)) if len(t.strip()) > 200]
            total, done_upto = 1, 1
        else:
            total = min(page_count(data), MAX_PAGES)
            while done_upto < total:
                if left() < 300:                             # не успеваем — сохраняемся, продолжит следующий запуск
                    s3_put(ocr_key, {"path": f"upload/{jid}/{fname}", "done_upto": done_upto,
                                     "complete": False, "n_pages": len(pages),
                                     "pages": [{"idx": i, "text": t} for i, t in pages]})
                    return set_status(jid, status="queued",
                                      msg=f"распознано {done_upto} из {total} стр., продолжу автоматически")
                stop = min(done_upto + CHUNK, total)
                set_status(jid, status="processing", msg=f"распознавание {done_upto + 1}–{stop} из {total} стр.")
                blob = downsample(data, done_upto, stop)
                if blob:
                    for i, t in enumerate(ocr_pdf(blob)):
                        if len(t.strip()) > 200: pages.append((done_upto + i, t))
                done_upto = stop
        s3_put(ocr_key, {"path": f"upload/{jid}/{fname}", "done_upto": done_upto, "complete": True,
                         "n_pages": len(pages), "n_kept": len(pages),
                         "pages": [{"idx": i, "text": t} for i, t in pages]})

    if not pages:
        return set_status(jid, status="error", msg="распознавание не дало текста (скан пустой или нечитаемый)")

    set_status(jid, status="processing", msg=f"извлекаю данные ({len(pages)} стр.)")
    texts = [t for _, t in sorted(pages)]
    recs = []
    with concurrent.futures.ThreadPoolExecutor(len(MODELS) * PER_MODEL) as ex:
        for part in ex.map(extract_page, texts):
            recs += part
    if not recs:
        if _err["n"]:                       # модель не ответила ни разу — это сбой, а не пустая публикация
            return set_status(jid, status="error", n_proposals=0,
                              msg=f"извлечение не удалось ({_err['n']} ошибок): {_err['last'][:110]}")
        return set_status(jid, status="ready", msg="разрезы в публикации не найдены", n_proposals=0)

    props = build_proposals(recs, [(i, norm(t)) for i, t in pages])
    set_status(jid, status="processing", msg=f"определяю координаты ({len(props)} объектов)")
    cache = s3_get(GC_KEY, {}) or {}
    for i, p in enumerate(props):
        c = geocode(p["locality"], None if p["admin"] == "ND" else p["admin"], cache)
        p["lat"], p["lon"] = (c[0], c[1]) if c else (None, None)
        p["i"] = i
    s3_put(GC_KEY, cache)
    s3_put(f"{PREFIX}{jid}/proposals.json", props)
    geo = sum(1 for p in props if p["lat"] is not None)
    return set_status(jid, status="ready", n_proposals=len(props),
                      msg=f"готово: {len(props)} объектов ({geo} с координатами) — нужна проверка")

def pending():
    """Задачи, ожидающие обработки (для запуска по таймеру и дообработки прерванных)."""
    out, tok = [], None
    while True:
        kw = {"Bucket": BUCKET, "Prefix": PREFIX, "MaxKeys": 1000}
        if tok: kw["ContinuationToken"] = tok
        r = s3.list_objects_v2(**kw)
        for o in r.get("Contents", []):
            if not o["Key"].endswith("/status.json"): continue
            st = s3_get(o["Key"]) or {}
            if st.get("status") == "queued":
                out.append((st.get("job_id") or o["Key"].split("/")[1], o["LastModified"]))
        tok = r.get("NextContinuationToken")
        if not r.get("IsTruncated"): break
    out.sort(key=lambda x: x[1])
    return [j for j, _ in out]

CORS = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type"}

def handler(event, context):
    # функцию вызывают и триггеры (там httpMethod нет), и кнопка со страницы — второй случай требует CORS
    if str((event or {}).get("httpMethod") or "").upper() == "OPTIONS":
        return {"statusCode": 200, "headers": {**CORS, "Content-Type": "application/json"},
                "body": json.dumps({"ok": True})}
    jobs = []
    # 1) триггер Object Storage: пришёл ключ загруженного файла
    for m in (event or {}).get("messages", []):
        oid = (m.get("details") or {}).get("object_id", "")
        if oid.startswith(PREFIX) and "/source" in oid:
            jobs.append(oid.split("/")[1])
    # 2) явный вызов {"job_id": "..."}
    body = event.get("body") if isinstance(event, dict) else None
    if body:
        try:
            d = json.loads(base64.b64decode(body).decode() if event.get("isBase64Encoded") else body)
            if d.get("job_id"): jobs.append(d["job_id"])
        except Exception:
            pass
    if isinstance(event, dict) and event.get("job_id"): jobs.append(event["job_id"])
    # 3) по таймеру — подобрать всё, что ждёт (в т.ч. прерванное)
    if not jobs: jobs = pending()

    done = []
    for jid in dict.fromkeys(jobs):
        if left() < 240:
            print("остаток времени мал, остальные задачи возьмёт следующий запуск", flush=True); break
        try:
            process(jid); done.append(jid)
        except Exception as e:
            set_status(jid, status="error", msg=str(e)[:200])
            print(f"ОШИБКА {jid}: {e}", flush=True)
    return {"statusCode": 200,
            "headers": {**CORS, "Content-Type": "application/json"},
            "body": json.dumps({"processed": done}, ensure_ascii=False)}
