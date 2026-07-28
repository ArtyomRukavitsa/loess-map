# ЛОКАЛЬНЫЙ extract из УЖЕ-ГОТОВЫХ текстов в бакете (ocrtext/<run>/*.json). Только qwen, глобальный потолок.
# Ни скачивания PDF, ни OCR -> быстро. Резюм по файлам. Пишет arch_local/<safe>.json. Запуск: python 33_..._bucket.py ocr
import os, sys, json, time, threading, random, urllib.request, urllib.error, collections, concurrent.futures
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "arch_local"); os.makedirs(OUT, exist_ok=True)
sec = {}
for line in open(os.path.join(HERE, ".secrets"), encoding="utf-8"):
    line = line.strip()
    if "=" in line and not line.startswith("#"): k, v = line.split("=", 1); sec[k] = v
import itertools
FOLDER = sec["YANDEX_FOLDER_ID"]; KEY = sec["YANDEX_API_KEY"]
# МУЛЬТИ-МОДЕЛЬ со ВЗВЕШЕННЫМ round-robin: qwen меньшую долю (просьба Yandex — он ест много токенов),
# основную нагрузку тянут deepseek+qwen3.6 (по качеству на уровне qwen). Формат MODELS: "model:weight,...".
_spec = [s.split(":") for s in os.environ.get("MODELS", "qwen3-235b-a22b-fp8:1,deepseek-v32:3,qwen3.6-35b-a3b:3,gpt-oss-120b:1").split(",")]
MODELS = [m[0] for m in _spec]
WEIGHTS = {m[0]: (int(m[1]) if len(m) > 1 else 1) for m in _spec}
CYCLE = []                                                    # взвешенная последовательность (интерливинг)
_pool = dict(WEIGHTS)
while sum(_pool.values()) > 0:
    for m in MODELS:
        if _pool[m] > 0: CYCLE.append(m); _pool[m] -= 1
PER_MODEL = int(os.environ.get("PER_MODEL", 8))               # одновременных на КАЖДУЮ модель
LLM = "https://llm.api.cloud.yandex.net/v1/chat/completions"
HDR = {"Authorization": "Api-Key " + KEY, "Content-Type": "application/json", "x-folder-id": FOLDER}

MAX_LLM = int(os.environ.get("MAX_LLM", len(MODELS) * PER_MODEL))   # всего потоков = модели × на-модель
DOC_WORKERS = int(os.environ.get("DOC_WORKERS", 6))
PEEK = int(os.environ.get("PEEK", 15))               # relevance-gate: проба первых N стр (+пара из середины)
GATE_MIN = int(os.environ.get("GATE_MIN", 1))        # <GATE_MIN разрезов в пробе -> НЕрелевантный, скип остального
LLM_POOL = concurrent.futures.ThreadPoolExecutor(MAX_LLM)
_stat = collections.Counter(); _lock = threading.Lock()
MODEL_SEM = {m: threading.Semaphore(PER_MODEL) for m in MODELS}     # свой семафор на каждую модель (своя квота)
_rr = itertools.count()

def http(url, body, timeout=180, tries=16):
    for a in range(tries):
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST")
        for k, v in HDR.items(): req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r: return r.read().decode()
        except urllib.error.HTTPError as e:
            if e.code == 429:
                with _lock: _stat["429"] += 1
            if e.code in (429, 500, 502, 503) and a < tries - 1:
                ra = e.headers.get("Retry-After")
                time.sleep((float(ra) if (ra and ra.isdigit()) else min(8, 2 ** a)) + random.uniform(0, 1.5)); continue  # кэп 8с — чаще пробуем слот
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

def extract_page(page):
    if not page or len(page.strip()) <= 200: return [], 0
    m = CYCLE[next(_rr) % len(CYCLE)]                          # взвешенный round-robin (qwen — меньшая доля)
    try:
        with MODEL_SEM[m]:                                    # не более PER_MODEL одновременно на эту модель
            d = json.loads(http(LLM, {"model": f"gpt://{FOLDER}/{m}/latest", "temperature": 0, "max_tokens": 3000,
                                      "messages": [{"role": "system", "content": SYS}, {"role": "user", "content": f'Фрагмент:\n"""\n{page[:6000]}\n"""\nИзвлеки записи.'}],
                                      "response_format": {"type": "json_schema", "json_schema": {"name": "recs", "strict": True, "schema": SCHEMA}}}))
        with _lock: _stat["m_" + m] += 1
        toks = d.get("usage", {}).get("total_tokens", 0); c = d["choices"][0]["message"].get("content")
        return (json.loads(c).get("records", []) if c else []), toks
    except Exception:
        return [], 0

def process_doc(path, pages):                                  # pages: list[(idx, text)]
    safe = path.rsplit("/", 1)[-1]
    for ch in ' ?*:<>|"\\/\t\n\r': safe = safe.replace(ch, "_")  # запрещённые в именах файлов Windows
    safe = safe[:80]
    out_path = os.path.join(OUT, safe + ".json")
    if os.path.exists(out_path):
        with _lock: _stat["skip"] += 1
        return
    t0 = time.time()
    seen = {}
    for idx, t in pages:
        seen.setdefault(idx, t)                                # дедуп по номеру страницы (старые+новые ocrtext-куски)
    texts = [seen[i] for i in sorted(seen)]
    n = len(texts)

    def extract_idxs(idxs):
        futs = [LLM_POOL.submit(extract_page, texts[i]) for i in idxs]
        rr = [f.result() for f in futs]
        return [r for rl, _ in rr for r in rl], sum(t for _, t in rr)

    skipped = False; nx = n
    if n <= PEEK + 5:                                          # мелкий док — извлекаем весь, гейт не нужен
        recs, toks = extract_idxs(range(n))
    else:
        peek = list(range(PEEK)) + [n // 2, (3 * n) // 4]      # проба: первые PEEK + пара из середины/дальше
        precs, ptoks = extract_idxs(peek)
        if len(precs) < GATE_MIN:                              # 0 разрезов в пробе -> НЕрелевантный -> скип остального
            recs, toks, skipped, nx = precs, ptoks, True, len(peek)
        else:
            rest = [i for i in range(n) if i not in set(peek)]
            rrecs, rtoks = extract_idxs(rest)
            recs, toks = precs + rrecs, ptoks + rtoks
    by = collections.defaultdict(list)
    for r in recs:
        loc = (r.get("nearest_locality", {}) or {}).get("value", "ND")
        if loc not in ("ND", "", None): by[loc].append(r)
    out = {"path": path, "n_pages": n, "n_pages_extracted": nx, "skipped_low_relevance": skipped,
           "n_records": len(recs), "n_sites": len(by), "tokens": toks, "sec": round(time.time() - t0, 1),
           "sites": sorted(by)[:30], "records": recs}
    json.dump(out, open(out_path, "w", encoding="utf-8"), ensure_ascii=False)
    with _lock:
        _stat["done"] += 1; _stat["skipped"] += (1 if skipped else 0)
        tag = f" СКИП(проба {nx}/{n})" if skipped else ""
        print(f"[{_stat['done']}] {safe[:44]} стр={n}{tag} recs={len(recs)} tok={toks} 429s={_stat['429']} {out.get('sec')}с", flush=True)

def main():
    RUN = sys.argv[1] if len(sys.argv) > 1 else "ocr"
    import boto3
    s3 = boto3.client("s3", endpoint_url="https://storage.yandexcloud.net", region_name="ru-central1",
                      aws_access_key_id=sec["AWS_ACCESS_KEY_ID"], aws_secret_access_key=sec["AWS_SECRET_ACCESS_KEY"])
    keys = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=sec["BUCKET"], Prefix=f"ocrtext/{RUN}/"):
        keys += [o["Key"] for o in page.get("Contents", []) if o["Key"].endswith(".json")]
    def fetch(k):
        try: return json.loads(s3.get_object(Bucket=sec["BUCKET"], Key=k)["Body"].read())
        except Exception: return {}
    with concurrent.futures.ThreadPoolExecutor(24) as ex:      # параллельный пул текста -> быстрый старт
        objs = list(ex.map(fetch, keys))
    docs = collections.defaultdict(list)
    for obj in objs:
        for pg in obj.get("pages", []):
            docs[obj.get("path", "?")].append((pg.get("idx", 0), pg.get("text", "")))
    print(f"ocrtext-файлов: {len(objs)} | доков: {len(docs)} | страниц: {sum(len(v) for v in docs.values())} | MAX_LLM={MAX_LLM}", flush=True)
    with concurrent.futures.ThreadPoolExecutor(DOC_WORKERS) as ex:
        list(ex.map(lambda kv: process_doc(*kv), docs.items()))
    print(f"ГОТОВО. done={_stat['done']} (из них low-relevance-скип={_stat['skipped']}) уже-были={_stat['skip']} 429_всего={_stat['429']}", flush=True)
    print("вызовов по моделям:", {m: _stat["m_" + m] for m in MODELS}, flush=True)

if __name__ == "__main__":
    main()
