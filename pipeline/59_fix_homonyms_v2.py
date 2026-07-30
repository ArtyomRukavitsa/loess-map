# Чинит класс геокод-омонимов в v2 (жалоба коллег: «Восточный Красный Яр» оказался в Самарской обл.).
# Корень: у 56% геокодированных строк administrative_unit=ND -> геокод по голому имени -> берётся не тот тёзка.
# Ответ обычно ЕСТЬ в наших же данных, поэтому используем два новых сигнала:
#   (1) ПРОТЯЖКА РЕГИОНА — тот же топоним в другой строке (желательно того же источника) уже имеет админ-единицу;
#   (2) ФОРМЫ РЕЛЬЕФА В ЦИТАТАХ — «бэровский бугор» бывает только в Прикаспии, «долина Десны» — на Десне и т.д.
# Координата вне подсказанной области -> перегеокод с ограничением; не вышло -> координата снимается (лучше пусто, чем неверно).
# Диагностика без запросов:  DRY=1 python 59_fix_homonyms_v2.py
# Правка:                    python 59_fix_homonyms_v2.py
import openpyxl, json, time, sys, os, re, math, pathlib, urllib.request, urllib.parse, collections
sys.stdout.reconfigure(encoding="utf-8")
HERE = pathlib.Path(__file__).parent
XLSX = HERE / "records_clean_geo_v2.xlsx"
CACHE = HERE / "geocache_region.json"
C_SEC = HERE / "geocache_sections.json"
DRY = os.environ.get("DRY", "0") == "1"
UA = "loess-sections/1.0 (research)"
CIS = "ru,ua,kz,by,md,ge,az,am,uz,kg,tj,tm,ee,lv,lt"
SETTLE = {"city", "town", "village", "hamlet", "municipality", "administrative", "suburb",
          "borough", "isolated_dwelling", "locality", "quarter"}
ADMIN_RE = re.compile(r"(област|край|республик|обл\.|governorate|region)", re.I)

# --- формы рельефа и региональные термины -> область поиска (lat_min, lat_max, lon_min, lon_max) ---
LANDFORMS = [
    ("Прикаспий (бэровские бугры)", (45.0, 48.6, 45.0, 50.6), ["бэровск", "бэровый бугор", "бугор бэра"]),
    ("Прикаспийская низменность",   (44.6, 50.0, 44.5, 52.0), ["прикаспийск", "северный каспий", "волго-ахтуб"]),
    ("Ергени",                      (46.3, 49.2, 43.2, 45.6), ["ергенин", "ергени"]),
    ("Поволжье",                    (45.0, 57.0, 42.0, 51.4), ["поволж", "заволж", "нижневолж", "волжск"]),
    ("Терско-Кумская низменность",  (43.0, 45.6, 44.0, 47.6), ["терско-кумск", "терск", "кумск"]),
    ("Десна",                       (51.0, 54.6, 30.8, 35.6), ["десн", "деснинск", "подесенье"]),
    ("Ока",                         (53.4, 56.6, 35.0, 43.0), ["окск", "приок"]),
    ("Днепр",                       (46.4, 54.0, 27.5, 35.5), ["днепр", "приднепров"]),
    ("Днестр",                      (46.0, 49.4, 25.4, 30.4), ["днестр", "приднестров"]),
    ("Дон",                         (46.0, 52.6, 37.6, 44.4), ["придонь", "донск", "нижний дон"]),
    ("Кубань / Предкавказье",       (43.3, 46.6, 37.0, 45.2), ["прикубан", "кубанск", "предкавказ", "ставропольск"]),
    ("Крым",                        (44.3, 46.3, 32.4, 36.8), ["крымск", "керченск", "тарханкут"]),
    ("Кулунда / Приобье",           (50.0, 56.6, 76.0, 86.6), ["кулундинск", "приобск", "приобье"]),
    ("Фергана",                     (39.6, 42.0, 68.6, 74.0), ["ферганск", "ферган"]),
    ("Апшерон",                     (39.8, 41.0, 48.6, 50.6), ["апшеронск", "апшерон"]),
    ("Волынь / Подолия",            (48.4, 51.8, 23.4, 29.0), ["волынск", "подольск", "подолия"]),
]

def landform_hint(text):
    t = str(text or "").lower().replace("ё", "е")
    for name, bbox, kws in LANDFORMS:
        if any(k in t for k in kws): return name, bbox
    return None, None

def inbox(la, lo, b): return b[0] <= la <= b[1] and b[2] <= lo <= b[3]

def hav(a, b):
    R = 6371; p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = math.radians(b[0] - a[0]), math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(min(1, math.sqrt(h)))

cache = json.load(open(CACHE, encoding="utf-8")) if CACHE.exists() else {}
c_sec = json.load(open(C_SEC, encoding="utf-8")) if C_SEC.exists() else {}
_req = [0]

def _nomi(params):
    r = urllib.request.Request("https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(params),
                               headers={"User-Agent": UA})
    return json.load(urllib.request.urlopen(r, timeout=30))

def geocode_box(name, bbox):
    key = name + "|" + str(bbox)
    if key in cache: return cache[key]
    la0, la1, lo0, lo1 = bbox; res = None
    try:
        for it in _nomi({"q": name, "format": "json", "limit": 5, "countrycodes": CIS,
                         "viewbox": f"{lo0},{la1},{lo1},{la0}", "bounded": 1}):
            if it.get("class") in ("place", "boundary") and it.get("type") in SETTLE:
                la, lo = float(it["lat"]), float(it["lon"])
                if inbox(la, lo, bbox): res = [la, lo]; break
    except Exception:
        res = None
    cache[key] = res; _req[0] += 1; time.sleep(1.05); return res

# «Восточный Красный Яр» как населённый пункт не ищется — пробуем и без уточняющего слова
STRIP_RE = re.compile(r"^(восточн|западн|северн|южн|верхн|нижн|больш|мал|нов|стар|средн)\w*\s+", re.I)

def geocode_box_smart(name, bbox):
    res = geocode_box(name, bbox)
    if res: return res, name
    short = STRIP_RE.sub("", name).strip()
    if short and short.lower() != name.lower():
        res = geocode_box(short, bbox)
        if res: return res, short
    return None, name

def geocode_admin(name, admin):
    key = name + "|" + admin
    if key in c_sec: return c_sec[key]
    res = None
    try:
        for it in _nomi({"q": f"{name}, {admin}", "format": "json", "limit": 5, "countrycodes": CIS}):
            if it.get("class") in ("place", "boundary") and it.get("type") in SETTLE:
                la, lo = float(it["lat"]), float(it["lon"])
                if 41 <= la <= 82 and 19 <= lo <= 180: res = [la, lo]; break
    except Exception:
        res = None
    c_sec[key] = res; _req[0] += 1; time.sleep(1.05); return res

# ---------- загрузка ----------
wb = openpyxl.load_workbook(XLSX); ws = wb.active
rows = list(ws.values); H = list(rows[0]); C = {n: H.index(n) for n in H}
data = [list(r) for r in rows[1:]]
def norm_name(s): return str(s or "").strip().lower().replace("ё", "е")

# индекс: имя -> админ-единицы (и с какими источниками встречались)
name_admin = collections.defaultdict(collections.Counter)
name_src_admin = collections.defaultdict(list)     # имя -> [(набор источников, админ)]
for r in data:
    adm = str(r[C["administrative_unit"]] or "").strip()
    if not adm or adm in ("ND", "None"): continue
    nm = norm_name(r[C["nearest_locality"]])
    name_admin[nm][adm] += 1
    srcs = {s.strip() for s in str(r[C["sources"]] or "").split("|") if s.strip()}
    name_src_admin[nm].append((srcs, adm))

def pick_admin(nm, srcs):
    """Админ-единица для строки без неё. strong=True только если по корпусу вариант ОДИН —
    иначе частый топоним («Степное», «Клепки») может утянуть верную точку в чужой регион."""
    allr = {a for a in name_admin.get(nm, {}) if ADMIN_RE.search(a)}
    same = [a for ss, a in name_src_admin.get(nm, []) if ss & srcs]
    real = [a for a in same if ADMIN_RE.search(a)]
    strong = len(allr) == 1
    if len(set(real)) == 1: return real[0], "тот же источник", strong
    if len(set(same)) == 1: return same[0], "тот же источник", strong
    if len(allr) == 1: return next(iter(allr)), "единственный вариант в корпусе", True
    return None, None, False

# ---------- проход ----------
geo_idx = [i for i, r in enumerate(data)
           if str(r[C["lat"]]) not in ("None", "", "ND") and r[C["lat"]] is not None]
cand = [i for i in geo_idx if str(r_adm := (data[i][C["administrative_unit"]] or "")).strip() in ("", "ND", "None")]
print(f"геокодировано строк: {len(geo_idx)} | без админ-единицы (риск омонима): {len(cand)}")

by_prop = by_land = by_weak = 0
plan = []            # (i, способ, подсказка, пояснение)
for i in cand:
    r = data[i]
    nm = norm_name(r[C["nearest_locality"]])
    srcs = {s.strip() for s in str(r[C["sources"]] or "").split("|") if s.strip()}
    adm, why, strong = pick_admin(nm, srcs)
    if adm and strong:                                # 1) надёжная админ-единица — самый прямой ответ
        plan.append((i, "admin", adm, why)); by_prop += 1; continue
    # 2) форма рельефа в цитате — сильнее расплывчатой подсказки вроде «Нижнее Поволжье»
    ctx = " ".join([str(r[C["evidence"]] or ""), str(r[C["geomorphic_position"]] or ""),
                    str(r[C["administrative_unit"]] or ""), str(r[C["sources"]] or "")])
    lname, bbox = landform_hint(ctx)
    if bbox and not inbox(float(r[C["lat"]]), float(r[C["lon"]]), bbox):
        plan.append((i, "bbox", bbox, lname)); by_land += 1; continue
    if adm:                                           # 3) тёзка неоднозначен — не двигаем, только помечаем
        plan.append((i, "flag", adm, "топоним неоднозначен — только пометка")); by_weak += 1

print(f"надёжная протяжка региона (двигаем): {by_prop} | противоречие форме рельефа (двигаем): {by_land}")
print(f"неоднозначный топоним (только пометка, без запросов): {by_weak}")
print(f"итого к проверке: {len(plan)} | из них с запросами в Nominatim: {by_prop + by_land}")
if DRY:
    print("\n--- примеры (сухой прогон) ---")
    for i, kind, hint, why in plan[:15]:
        r = data[i]
        print(f"  {r[C['nearest_locality']]}  ({r[C['lat']]}, {r[C['lon']]})  <- {kind}: {hint if kind=='admin' else why} [{why if kind=='admin' else 'вне области'}]")
    sys.exit(0)

n_fixed = n_kept = n_dropped = 0
for k, (i, kind, hint, why) in enumerate(plan, 1):
    r = data[i]
    name = str(r[C["nearest_locality"]]).strip()
    cur = [float(r[C["lat"]]), float(r[C["lon"]])]
    if kind == "flag":                               # спорный тёзка: координату не трогаем, помечаем для проверки
        r[C["loc_confidence"]] = "uncertain"; n_kept += 1; continue
    if kind == "admin":
        new, used = geocode_admin(name, hint), name
    else:
        new, used = geocode_box_smart(name, hint)
    if new:
        if hav(cur, new) > 50:                       # сдвиг существенный -> это был другой тёзка
            r[C["lat"]], r[C["lon"]] = round(new[0], 5), round(new[1], 5)
            n_fixed += 1
            if n_fixed <= 25:
                via = "" if used == name else f", по «{used}»"
                print(f"  ИСПРАВЛЕНО {name}: {cur[0]:.3f},{cur[1]:.3f} -> {new[0]:.3f},{new[1]:.3f}  ({why}{via})")
        else:
            n_kept += 1
    elif kind == "bbox":                             # цитата прямо противоречит координате, замены нет -> снимаем
        r[C["lat"]], r[C["lon"]] = "ND", "ND"
        r[C["loc_confidence"]] = "uncertain"
        n_dropped += 1
    else:
        r[C["loc_confidence"]] = "uncertain"; n_kept += 1
    if k % 25 == 0:
        json.dump(cache, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False)
        json.dump(c_sec, open(C_SEC, "w", encoding="utf-8"), ensure_ascii=False)
        print(f"  ...{k}/{len(plan)} (исправлено {n_fixed}, снято {n_dropped}, запросов {_req[0]})", flush=True)

json.dump(cache, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False)
json.dump(c_sec, open(C_SEC, "w", encoding="utf-8"), ensure_ascii=False)
import shutil
shutil.copy(XLSX, XLSX.with_suffix(".before_homonyms.xlsx"))
wb2 = openpyxl.Workbook(); w = wb2.active; w.title = "sections"; w.append(H)
for r in data: w.append(r)
wb2.save(XLSX)
print(f"\nИТОГ: исправлено {n_fixed} | подтверждено {n_kept} | снято координат {n_dropped} | запросов {_req[0]}")
print(f"-> {XLSX.name} обновлён (бэкап .before_homonyms.xlsx)")
