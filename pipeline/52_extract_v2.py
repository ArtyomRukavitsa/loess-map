# Пере-extract v2 из готовых OCR-текстов в бакете. Расширенная схема:
#   - deposit_raw_terms (ДОСЛОВНЫЙ термин — страховка, перемаппим таксономию потом без нового прогона)
#   - type_of_deposits (расширенный enum из частот корпуса)
#   - thickness типизирован: studied / visible / borehole_depth / unspecified
#   - source_kind: prose / table / caption / mixed
#   Промпт явно велит брать данные И из таблиц, И из подписей к рисункам.
# Вывод: arch_local_v2/<safe>.json (не трогает v1).
# Dry-run:  ONLY="Отказное" LIMIT=1 DRY=1 python 52_extract_v2.py arch
# Полный:   MODELS="deepseek-v32,qwen3.6-35b-a3b,gpt-oss-120b,qwen3-235b-a22b-fp8" python 52_extract_v2.py arch  (и img)
import os, sys, json, time, threading, random, urllib.request, urllib.error, collections, concurrent.futures, itertools
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "arch_local_v2"); os.makedirs(OUT, exist_ok=True)
sec = {}
for line in open(os.path.join(HERE, ".secrets"), encoding="utf-8"):
    line = line.strip()
    if "=" in line and not line.startswith("#"): k, v = line.split("=", 1); sec[k] = v
FOLDER = sec["YANDEX_FOLDER_ID"]; KEY = sec["YANDEX_API_KEY"]
_spec = [s.split(":") for s in os.environ.get("MODELS", "deepseek-v32:3,qwen3-235b-a22b-fp8:2,gpt-oss-120b:2").split(",")]
MODELS = [m[0] for m in _spec]
WEIGHTS = {m[0]: (int(m[1]) if len(m) > 1 else 1) for m in _spec}
CYCLE = []; _pool = dict(WEIGHTS)
while sum(_pool.values()) > 0:
    for m in MODELS:
        if _pool[m] > 0: CYCLE.append(m); _pool[m] -= 1
PER_MODEL = int(os.environ.get("PER_MODEL", 6))
LLM = "https://llm.api.cloud.yandex.net/v1/chat/completions"
HDR = {"Authorization": "Api-Key " + KEY, "Content-Type": "application/json", "x-folder-id": FOLDER}
MAX_LLM = int(os.environ.get("MAX_LLM", len(MODELS) * PER_MODEL))
DOC_WORKERS = int(os.environ.get("DOC_WORKERS", 6))
PEEK = int(os.environ.get("PEEK", 15)); GATE_MIN = int(os.environ.get("GATE_MIN", 1))
DRY = os.environ.get("DRY", "0") == "1"
LLM_POOL = concurrent.futures.ThreadPoolExecutor(MAX_LLM)
_stat = collections.Counter(); _lock = threading.Lock()
MODEL_SEM = {m: threading.Semaphore(PER_MODEL) for m in MODELS}
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
                time.sleep((float(ra) if (ra and ra.isdigit()) else min(8, 2 ** a)) + random.uniform(0, 1.5)); continue
            raise
        except urllib.error.URLError:
            if a < tries - 1: time.sleep(min(8, 2 ** a) + random.uniform(0, 1.5)); continue
            raise

def field(v): return {"type": "object", "additionalProperties": False, "required": ["value", "evidence"],
                      "properties": {"value": v, "evidence": {"type": "string"}}}

# --- расширенный enum ИЗ ЧАСТОТ КОРПУСА (предварительный; перемаппим по raw_terms позже) ---
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
           "excavation_type": field({"type": "string", "enum": EXC}),                   # тип вскрытия (кол.7)
           "geomorphic_position": field({"type": "string"}),                            # геоморфопозиция дословно (кол.8-9)
           "deposit_raw_terms": field({"type": "array", "items": {"type": "string"}}),  # ДОСЛОВНЫЕ термины
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

def extract_page(page):
    if not page or len(page.strip()) <= 200: return [], 0
    m = CYCLE[next(_rr) % len(CYCLE)]
    try:
        with MODEL_SEM[m]:
            resp = http(LLM, {"model": f"gpt://{FOLDER}/{m}/latest", "temperature": 0, "max_tokens": 5000,
                              "messages": [{"role": "system", "content": SYS},
                                           {"role": "user", "content": f'Фрагмент:\n"""\n{page[:6000]}\n"""\nИзвлеки записи.'}],
                              "response_format": {"type": "json_schema", "json_schema": {"name": "recs", "strict": True, "schema": SCHEMA}}})
    except Exception as e:
        if DRY:
            with _lock: print(f"  HTTP-ошибка [{m}]:", str(e)[:150], flush=True)
        return [], 0
    try:
        d = json.loads(resp)
    except Exception as e:
        if DRY:
            with _lock: print(f"  ENVELOPE-ошибка [{m}]:", str(e)[:70], "| raw:", resp[:160], flush=True)
        return [], 0
    with _lock: _stat["m_" + m] += 1
    toks = d.get("usage", {}).get("total_tokens", 0)
    ch = d.get("choices", [{}])[0]; c = ch.get("message", {}).get("content")
    if not c: return [], toks
    try:
        return json.loads(c).get("records", []), toks
    except Exception as e:
        if DRY:
            with _lock: print(f"  CONTENT-ошибка [{m}]:", str(e)[:60], "| finish:", ch.get("finish_reason"),
                              "| len:", len(c), "| хвост:", repr(c[-120:]), flush=True)
        return [], toks

def process_doc(path, pages):
    safe = path.rsplit("/", 1)[-1]
    for ch in ' ?*:<>|"\\/\t\n\r': safe = safe.replace(ch, "_")
    safe = safe[:80]
    out_path = os.path.join(OUT, safe + ".json")
    if os.path.exists(out_path) and not DRY:
        with _lock: _stat["skip"] += 1
        return
    t0 = time.time(); seen = {}
    for idx, t in pages: seen.setdefault(idx, t)
    texts = [seen[i] for i in sorted(seen)]; n = len(texts)

    def extract_idxs(idxs):
        futs = [LLM_POOL.submit(extract_page, texts[i]) for i in idxs]
        rr = [f.result() for f in futs]
        return [r for rl, _ in rr for r in rl], sum(t for _, t in rr)

    skipped = False; nx = n
    if n <= PEEK + 5:
        recs, toks = extract_idxs(range(n))
    else:
        peek = list(range(PEEK)) + [n // 2, (3 * n) // 4]
        precs, ptoks = extract_idxs(peek)
        if len(precs) < GATE_MIN:
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
    if not DRY:
        json.dump(out, open(out_path, "w", encoding="utf-8"), ensure_ascii=False)
    with _lock:
        _stat["done"] += 1; _stat["skipped"] += (1 if skipped else 0)
        tag = f" СКИП(проба {nx}/{n})" if skipped else ""
        print(f"[{_stat['done']}] {safe[:44]} стр={n}{tag} recs={len(recs)} tok={toks} 429s={_stat['429']} {out.get('sec')}с", flush=True)
    return out

def main():
    RUN = sys.argv[1] if len(sys.argv) > 1 else "arch"
    ONLY = os.environ.get("ONLY"); LIMIT = int(os.environ.get("LIMIT", 0))
    import boto3
    s3 = boto3.client("s3", endpoint_url="https://storage.yandexcloud.net", region_name="ru-central1",
                      aws_access_key_id=sec["AWS_ACCESS_KEY_ID"], aws_secret_access_key=sec["AWS_SECRET_ACCESS_KEY"])
    keys = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=sec["BUCKET"], Prefix=f"ocrtext/{RUN}/"):
        keys += [o["Key"] for o in page.get("Contents", []) if o["Key"].endswith(".json")]
    def fetch(k):
        try: return json.loads(s3.get_object(Bucket=sec["BUCKET"], Key=k)["Body"].read())
        except Exception: return {}
    with concurrent.futures.ThreadPoolExecutor(24) as ex:
        objs = list(ex.map(fetch, keys))
    docs = collections.defaultdict(list)
    for obj in objs:
        p = obj.get("path", "?")
        if ONLY and ONLY.lower() not in p.lower(): continue
        for pg in obj.get("pages", []):
            docs[p].append((pg.get("idx", 0), pg.get("text", "")))
    items = list(docs.items())
    if LIMIT: items = items[:LIMIT]
    print(f"[v2] RUN={RUN} доков: {len(items)} | страниц: {sum(len(pgs) for _, pgs in items)} | MAX_LLM={MAX_LLM} DRY={DRY}", flush=True)
    if DRY:
        for p, pages in items:
            out = process_doc(p, pages)
            print("\n=== SAMPLE RECORDS ===")
            for r in (out.get("records") or [])[:6]:
                loc = r.get("nearest_locality", {}).get("value")
                dep = r.get("type_of_deposits", {}).get("value")
                raw = r.get("deposit_raw_terms", {}).get("value")
                thi = [(t.get("kind"), t.get("value_m")) for t in r.get("thickness", [])]
                el = r.get("absolute_elevation_m", {}).get("value")
                exc = r.get("excavation_type", {}).get("value")
                geo = r.get("geomorphic_position", {}).get("value")
                sk = r.get("source_kind")
                print(f"  • {loc} | exc={exc} | geo={geo} | dep={dep} | raw={raw} | thick={thi} | elev={el} | src={sk}")
    else:
        with concurrent.futures.ThreadPoolExecutor(DOC_WORKERS) as ex:
            list(ex.map(lambda kv: process_doc(*kv), items))
    print(f"\nГОТОВО. done={_stat['done']} skip-low-rel={_stat['skipped']} уже-были={_stat['skip']} 429={_stat['429']}", flush=True)
    print("вызовов по моделям:", {m: _stat["m_" + m] for m in MODELS}, flush=True)

if __name__ == "__main__":
    main()
