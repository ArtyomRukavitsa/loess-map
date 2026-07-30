# Шаг v2: привязка разрез -> страница скана матчингом evidence по OCR-страницам (без LLM, $0).
# Вход: records_clean_geo_v2.xlsx (evidence + sources) + OCR-страницы из Object Storage (ocrtext/*).
# Выход: page_links_v2.json  { "lat,lon": [ {"src": <публикация>, "pages":[N,...], "approx": bool} ] }
#   ключ = markerId билдера (round(lat,6),round(lon,6)); page — 1-based индекс страницы В СКАНЕ.
import os, re, json, collections, concurrent.futures
import openpyxl, boto3

HERE = os.path.dirname(os.path.abspath(__file__))
sec = {}
for line in open(os.path.join(HERE, ".secrets"), encoding="utf-8"):
    line = line.strip()
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1); sec[k.strip()] = v.strip()
s3 = boto3.client("s3", endpoint_url="https://storage.yandexcloud.net", region_name="ru-central1",
                  aws_access_key_id=sec["AWS_ACCESS_KEY_ID"], aws_secret_access_key=sec["AWS_SECRET_ACCESS_KEY"])
B = sec.get("BUCKET", "loess-results")

def norm(s):
    s = (s or "").lower().replace("ё", "е")
    return re.sub(r"[^0-9a-zа-я]+", " ", s).strip()

UPLOAD_MARK = re.compile(r"\s*\(загружено пользователем\)\s*$", re.I)

def norm_name(s):  # нормализация имени публикации для сопоставления sources <-> OCR path
    s = UPLOAD_MARK.sub("", str(s or ""))          # у загруженных в таблице есть пометка — для сопоставления убираем
    return re.sub(r"\s+", " ", s.strip().lower())

# ONLY_SRC="Публикация1.pdf|Публикация2.pdf" — инкрементальный режим: качаем OCR ТОЛЬКО этих публикаций
# и дополняем существующий page_links_v2.json. Полный пересчёт тянет 2600+ объектов (~3.5 мин),
# при публикации же добавляются точки из одной-двух публикаций — там достаточно нескольких объектов.
ONLY_SRC = [s.strip() for s in os.environ.get("ONLY_SRC", "").split("|") if s.strip()]

# --- 1) индекс OCR-страниц по публикациям ---
print(f"[1/3] тяну OCR-страницы из Object Storage{' (только нужные публикации)' if ONLY_SRC else ''}...", flush=True)
keys = []
for run in ("arch", "img", "ocr", "upload"):     # upload — публикации, загруженные пользователями
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=B, Prefix=f"ocrtext/{run}/"):
        keys += [o["Key"] for o in page.get("Contents", []) if o["Key"].endswith(".json")]
if ONLY_SRC:
    # У архивных объектов имя ключа повторяет имя файла — фильтруем по нему, не скачивая тела.
    # У загруженных ключ — это идентификатор задачи (ocrtext/upload/<job>.json), имя файла лежит внутри,
    # поэтому такие объекты берём все: их единицы.
    want = {re.sub(r"\s+", "_", norm_name(s))[:70] for s in ONLY_SRC}
    keys = [k for k in keys
            if k.startswith("ocrtext/upload/")
            or any(w[:40] in re.sub(r"\s+", "_", k.rsplit("/", 1)[-1].lower()) for w in want)]
    print(f"      отобрано объектов: {len(keys)}", flush=True)
def fetch(k):
    try: return json.loads(s3.get_object(Bucket=B, Key=k)["Body"].read())
    except Exception: return {}
with concurrent.futures.ThreadPoolExecutor(24) as ex:
    objs = list(ex.map(fetch, keys))

pubs = {}  # norm_name(basename) -> [(idx, normed_text)]
for obj in objs:
    p = obj.get("path", "")
    base = os.path.basename(p.replace("\\", "/"))
    pages = [(pg.get("idx", 0), norm(pg.get("text", ""))) for pg in obj.get("pages", [])]
    if base and pages:
        pubs.setdefault(norm_name(base), []).extend(pages)
print(f"      публикаций с OCR: {len(pubs)} | объектов: {len(objs)}", flush=True)

def lookup_pub(src):
    n = norm_name(src)
    if n in pubs: return pubs[n]
    stem = n.rsplit(".", 1)[0]
    for k in pubs:                       # запасной: по совпадению начала имени
        if k.startswith(stem[:25]) or stem.startswith(k.rsplit(".", 1)[0][:25]):
            return pubs[k]
    return None

def find_page(pages, ev):
    ev = norm(ev)
    if len(ev) < 12: return None, None
    probe = ev[:60]
    for idx, txt in pages:
        if probe in txt: return idx, "exact"
    words = ev.split()
    for nn in (8, 6, 4):
        frag = " ".join(words[:nn])
        if len(frag) < 12: continue
        for idx, txt in pages:
            if frag in txt: return idx, "exact"
    ev_tok = set(w for w in words if len(w) >= 5)
    if len(ev_tok) < 3: ev_tok = set(w for w in words if len(w) >= 4)
    if len(ev_tok) < 3: return None, None
    best, best_sc = None, 0
    for idx, txt in pages:
        sc = len(ev_tok & set(txt.split()))
        if sc > best_sc: best_sc, best = sc, idx
    if best_sc >= max(3, int(0.6 * len(ev_tok))):
        return best, "fuzzy"
    return None, None

# --- 2) идём по строкам xlsx, матчим evidence по источникам строки ---
print("[2/3] матчу evidence по страницам...", flush=True)
wb = openpyxl.load_workbook(os.path.join(HERE, "records_clean_geo_v2.xlsx"), read_only=True)
ws = wb.active
rows = ws.iter_rows(values_only=True)
hdr = list(next(rows))
ci = {h: i for i, h in enumerate(hdr)}

# на маркер (координата) -> src -> {pages:set, approx:bool}
links = collections.defaultdict(lambda: collections.defaultdict(lambda: {"pages": set(), "approx": True}))
n_rows = n_marked = 0
for r in rows:
    lat, lon = r[ci["lat"]], r[ci["lon"]]
    if lat is None or lon is None or str(lat) in ("", "None"): continue
    try: key = f"{round(float(lat), 6)},{round(float(lon), 6)}"
    except Exception: continue
    n_rows += 1
    srcs = [s.strip() for s in str(r[ci["sources"]] or "").split("|") if s.strip()]
    evs = [e.strip() for e in str(r[ci["evidence"]] or "").split("||") if e.strip()]
    src_pages = [(s, lookup_pub(s)) for s in srcs]
    src_pages = [(s, pg) for s, pg in src_pages if pg]
    if not src_pages or not evs: continue
    got = False
    for ev in evs:
        for s, pg in src_pages:
            idx, method = find_page(pg, ev)
            if idx is not None:
                d = links[key][s]
                d["pages"].add(idx + 1)                 # 1-based для человека
                if method == "exact": d["approx"] = False
                got = True
    if got: n_marked += 1

# --- 3) сериализация ---
PLF = os.path.join(HERE, "page_links_v2.json")
out = {}
for key, srcmap in links.items():
    lst = []
    for s, d in srcmap.items():
        if d["pages"]:
            lst.append({"src": s, "pages": sorted(d["pages"]), "approx": d["approx"]})
    if lst:
        lst.sort(key=lambda x: (x["approx"], x["src"]))
        out[key] = lst
if ONLY_SRC and os.path.exists(PLF):          # инкрементально — дополняем прежний файл, а не затираем его
    prev = json.load(open(PLF, encoding="utf-8"))
    added = 0
    for key, lst in out.items():
        cur = prev.get(key, [])
        have = {e["src"] for e in cur}
        new = [e for e in lst if e["src"] not in have]
        if new: prev[key] = cur + new; added += len(new)
    print(f"      дополнено: +{added} записей о страницах, маркеров в файле {len(prev)}", flush=True)
    out = prev
json.dump(out, open(PLF, "w", encoding="utf-8"), ensure_ascii=False)
print(f"[3/3] маркеров со страницами: {len(out)} | строк с привязкой: {n_marked}/{n_rows} "
      f"({100*n_marked/max(n_rows,1):.0f}%) -> page_links_v2.json", flush=True)
