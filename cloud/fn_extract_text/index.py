# Cloud Function: fn_extract_text — читает OCR-текст дока из бакета -> МУЛЬТИМОДЕЛЬНОЕ извлечение (round-robin,
# у каждой модели своя квота -> нагрузка делится -> 429 не роняет) -> записи в бакет. Relevance-gate. БЕЗ OCR/скачивания.
# Вход:  {"safe","run_id"[,"models","per_model","max_pages"]}  (safe = базовое имя дока)
# Выход: {"safe","path","n_pages","n_pages_extracted","skipped_low_relevance","n_records","tokens"}
# Пишет records/<run>/<safe>.json. Env: YC_FOLDER_ID, YC_API_KEY, BUCKET, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
import os, json, time, random, threading, itertools, urllib.request, urllib.error, concurrent.futures

FOLDER = os.environ["YC_FOLDER_ID"]; APIKEY = os.environ["YC_API_KEY"]; BUCKET = os.environ["BUCKET"]
LLM = "https://llm.api.cloud.yandex.net/v1/chat/completions"
HDR = {"Authorization": "Api-Key " + APIKEY, "Content-Type": "application/json", "x-folder-id": FOLDER}
MODELS_DEF = "qwen3-235b-a22b-fp8:1,deepseek-v32:3,qwen3.6-35b-a3b:3,gpt-oss-120b:1"  # qwen меньшая доля (просьба Yandex)

_s3 = None
def s3():
    global _s3
    if _s3 is None:
        import boto3
        _s3 = boto3.client("s3", endpoint_url="https://storage.yandexcloud.net", region_name="ru-central1")
    return _s3

def http(url, body, timeout=180, tries=16):
    for a in range(tries):
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST")
        for k, v in HDR.items(): req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r: return r.read().decode()
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and a < tries - 1:
                ra = e.headers.get("Retry-After")
                time.sleep((float(ra) if (ra and ra.isdigit()) else min(8, 2 ** a)) + random.uniform(0, 1.5)); continue
            raise
        except urllib.error.URLError:
            if a < tries - 1: time.sleep(min(8, 2 ** a) + random.uniform(0, 1.5)); continue
            raise

def field(v): return {"type": "object", "additionalProperties": False, "required": ["value", "evidence"], "properties": {"value": v, "evidence": {"type": "string"}}}
DEP = ["loess", "loess-like loam", "loess-like silty loam", "slope deposits", "alluvium", "till", "marine deposits", "volcanic ash"]
STR = ["Lower Pleistocene", "Middle Pleistocene", "Upper Pleistocene", "Holocene"]; DAT = ["14C", "OSL", "TL", "magnetostratigraphy", "(U-Th)/He"]
REC = {"type": "object", "additionalProperties": False, "required": ["nearest_locality", "administrative_unit", "thickness_m", "absolute_elevation_m", "type_of_deposits", "stratigraphic_position", "dating_methods"],
       "properties": {"nearest_locality": field({"type": "string"}), "administrative_unit": field({"type": "string"}), "thickness_m": field({"type": "string"}), "absolute_elevation_m": field({"type": "string"}),
                      "type_of_deposits": field({"type": "array", "items": {"type": "string", "enum": DEP}}), "stratigraphic_position": field({"type": "array", "items": {"type": "string", "enum": STR}}), "dating_methods": field({"type": "array", "items": {"type": "string", "enum": DAT}})}}
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["records"], "properties": {"records": {"type": "array", "items": REC}}}
SYS = ("Ты — извлекатель научных данных по лёссам/четвертичке. Из фрагмента извлеки записи о КОНКРЕТНЫХ разрезах с данными. "
       "На каждое поле — цитата (evidence); нет основания -> 'ND'/[]. Не выдумывать. Координаты не извлекать. Названия кириллицей. Стадии -> Lower/Middle/Upper Pleistocene/Holocene. Нет разреза -> records: [].")

def handler(event, context):
    safe = event["safe"]; run_id = str(event.get("run_id", "arch"))
    try:                                                      # резюм: уже извлечён -> пропускаем
        s3().head_object(Bucket=BUCKET, Key=f"records/{run_id}/{safe}.json")
        return {"safe": safe, "skipped_existing": True, "n_records": 0, "tokens": 0}
    except Exception:
        pass
    spec = [s.split(":") for s in event.get("models", MODELS_DEF).split(",")]
    models = [m[0] for m in spec]
    weights = {m[0]: (int(m[1]) if len(m) > 1 else 1) for m in spec}
    cycle = []; pool = dict(weights)                          # взвешенная последовательность (интерливинг)
    while sum(pool.values()) > 0:
        for m in models:
            if pool[m] > 0: cycle.append(m); pool[m] -= 1
    per_model = int(event.get("per_model", 2))
    max_pages = int(event.get("max_pages", 300)); PEEK = 15
    sems = {m: threading.Semaphore(per_model) for m in models}; rr = itertools.count()
    stat = {"429": 0}; lock = threading.Lock()

    def extract_page(page):
        if not page or len(page.strip()) <= 200: return [], 0
        m = cycle[next(rr) % len(cycle)]                      # взвешенный round-robin (qwen — меньшая доля)
        try:
            with sems[m]:
                d = json.loads(http(LLM, {"model": f"gpt://{FOLDER}/{m}/latest", "temperature": 0, "max_tokens": 3000,
                                          "messages": [{"role": "system", "content": SYS}, {"role": "user", "content": f'Фрагмент:\n"""\n{page[:6000]}\n"""\nИзвлеки записи.'}],
                                          "response_format": {"type": "json_schema", "json_schema": {"name": "recs", "strict": True, "schema": SCHEMA}}}))
            c = d["choices"][0]["message"].get("content"); return (json.loads(c).get("records", []) if c else []), d.get("usage", {}).get("total_tokens", 0)
        except Exception:
            return [], 0

    # собрать текст дока из бакета ocrtext/<run>/<safe>__*.json
    pages_by_idx = {}
    path = "?"
    for o in s3().list_objects_v2(Bucket=BUCKET, Prefix=f"ocrtext/{run_id}/{safe}").get("Contents", []):
        obj = json.loads(s3().get_object(Bucket=BUCKET, Key=o["Key"])["Body"].read()); path = obj.get("path", path)
        for pg in obj.get("pages", []): pages_by_idx.setdefault(pg.get("idx", 0), pg.get("text", ""))
    texts = [pages_by_idx[i] for i in sorted(pages_by_idx)][:max_pages]
    n = len(texts)

    t0 = time.time(); BUDGET = int(event.get("time_budget", 450))   # < таймаута функции -> не даём убить себя
    pool = concurrent.futures.ThreadPoolExecutor(len(models) * per_model)
    # извлечение: relevance-gate (проба) -> остальное; уважаем бюджет времени (пишем частично, не таймаутим)
    def ex(idxs):
        futs = [pool.submit(extract_page, texts[i]) for i in idxs]
        recs, toks = [], 0
        for f in futs:
            if time.time() - t0 > BUDGET: f.cancel(); continue
            try:
                rl, tk = f.result(timeout=max(1, BUDGET - (time.time() - t0))); recs += rl; toks += tk
            except Exception:
                pass
        return recs, toks
    skipped = False; nx = n
    if n <= PEEK + 5:
        recs, toks = ex(range(n))
    else:
        peek = list(range(PEEK)) + [n // 2, (3 * n) // 4]
        precs, ptoks = ex(peek)
        if len(precs) < 1:
            recs, toks, skipped, nx = precs, ptoks, True, len(peek)
        else:
            rest = [i for i in range(n) if i not in set(peek)]; rrecs, rtoks = ex(rest)
            recs, toks = precs + rrecs, ptoks + rtoks
    pool.shutdown(wait=False)

    out = {"safe": safe, "path": path, "n_pages": n, "n_pages_extracted": nx, "skipped_low_relevance": skipped,
           "n_records": len(recs), "tokens": toks, "records": recs}
    s3().put_object(Bucket=BUCKET, Key=f"records/{run_id}/{safe}.json", Body=json.dumps(out, ensure_ascii=False).encode("utf-8"))
    return {k: out[k] for k in ("safe", "path", "n_pages", "n_pages_extracted", "skipped_low_relevance", "n_records", "tokens")}
