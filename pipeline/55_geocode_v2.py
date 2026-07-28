# Геокод v2: records_clean_v2.xlsx -> records_clean_geo_v2.xlsx (+lat,lon,loc_confidence).
# Переиспользует кэши: geocache_sections (привязка), geocache_region (фикс омонимов), geocache_ambig (флаг).
# Новые локальности бьёт в Nominatim (~1/сек). Резюм по кэшам.
import openpyxl, json, time, sys, math, re, pathlib, urllib.request, urllib.parse
sys.stdout.reconfigure(encoding="utf-8")
HERE = pathlib.Path(__file__).parent
UA = "loess-sections/1.0 (research)"
CIS = "ru,ua,kz,by,md,ge,az,am,uz,kg,tj,tm,ee,lv,lt"
SETTLE = {"city", "town", "village", "hamlet", "municipality", "administrative", "suburb",
          "borough", "isolated_dwelling", "locality", "quarter"}
def load(f):
    p = HERE / f
    return json.load(open(p, encoding="utf-8")) if p.exists() else {}
c_sec = load("geocache_sections.json"); c_reg = load("geocache_region.json"); c_amb = load("geocache_ambig.json")

REGIONS = [
    ("Приазовье", (45.3, 47.9, 34.3, 40.6), ["приазов", "азовск", "таганрог", "ейск"]),
    ("Предкавказье", (43.3, 46.6, 37.0, 45.2), ["предкавказ", "ставропол", "кубан", "краснодар", "пятигор", "прикубан"]),
    ("Кавказ", (41.0, 44.6, 39.0, 48.6), ["кавказ", "дагестан", "чечн", "осети", "кабард", "карачаев", "адыг", "эльбрус", "терск"]),
    ("Крым", (44.3, 46.3, 32.4, 36.8), ["крым", "керчен", "тарханкут"]),
    ("Нижний Дон", (46.0, 50.6, 38.4, 44.2), ["нижн дон", "ростов", "донск", "приазовск", "маныч"]),
    ("Украина", (44.0, 52.6, 22.0, 40.6), ["украин", "днепр", "днестр", "киев", "полтав", "харьк", "одесс", "подол"]),
    ("Молдавия", (45.4, 48.6, 26.4, 30.3), ["молдав", "бессараб", "кишин"]),
    ("Западная Сибирь", (49.0, 62.0, 60.0, 90.0), ["западн сибир", "новосибир", "алтай", "приобь", "прииртыш", "омск", "томск", "барнаул", "кулунд"]),
    ("Средняя Азия", (36.0, 45.6, 55.0, 75.5), ["средн ази", "таджик", "узбек", "киргиз", "туркмен", "памир", "душанбе", "ташкент", "фергана", "самарканд"]),
    ("Поволжье", (45.0, 57.0, 42.0, 51.2), ["поволж", "прикасп", "саратов", "самар", "волгоград", "астрахан", "ерген", "заволж"]),
    ("Азербайджан", (38.3, 42.1, 44.5, 51.0), ["азербайдж", "апшерон", "куринск"]),
    ("Забайкалье", (49.0, 56.5, 105.0, 121.0), ["забайкал", "бурят", "чит"]),
]
def region_hint(sources):
    s = str(sources or "").lower()
    for name, bbox, kws in REGIONS:
        if any(k in s for k in kws): return bbox
    return None
def inbox(la, lo, b): return b[0] <= la <= b[1] and b[2] <= lo <= b[3]

def req(url):
    r = urllib.request.Request(url, headers={"User-Agent": UA})
    return json.load(urllib.request.urlopen(r, timeout=30))

def geocode(name, admin):
    key = name + "|" + (admin or "")
    if key in c_sec: return c_sec[key]
    q = name + (", " + admin if admin else "")
    res = None
    try:
        for it in req("https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
                {"q": q, "format": "json", "limit": 5, "countrycodes": CIS})):
            if it.get("class") in ("place", "boundary") and it.get("type") in SETTLE:
                la, lo = float(it["lat"]), float(it["lon"])
                if 41 <= la <= 82 and 19 <= lo <= 180: res = [la, lo]; break
    except Exception: res = None
    c_sec[key] = res; time.sleep(1.05); return res

def geocode_box(name, bbox):
    key = name + "|" + str(bbox)
    if key in c_reg: return c_reg[key]
    la0, la1, lo0, lo1 = bbox; res = None
    try:
        for it in req("https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
                {"q": name, "format": "json", "limit": 5, "countrycodes": CIS,
                 "viewbox": f"{lo0},{la1},{lo1},{la0}", "bounded": 1})):
            if it.get("class") in ("place", "boundary") and it.get("type") in SETTLE:
                la, lo = float(it["lat"]), float(it["lon"])
                if inbox(la, lo, bbox): res = [la, lo]; break
    except Exception: res = None
    c_reg[key] = res; time.sleep(1.05); return res

def hav(a, b):
    R = 6371; p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp = math.radians(b[0] - a[0]); dl = math.radians(b[1] - a[1])
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(x))
def candidates(name):
    if name in c_amb: return c_amb[name]
    pts = []
    try:
        for it in req("https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
                {"q": name, "format": "json", "limit": 10, "countrycodes": CIS})):
            if it.get("class") in ("place", "boundary") and it.get("type") in SETTLE:
                la, lo = round(float(it["lat"]), 1), round(float(it["lon"]), 1)
                if 41 <= la <= 82 and 19 <= lo <= 180 and [la, lo] not in pts: pts.append([la, lo])
    except Exception: pts = []
    c_amb[name] = pts; time.sleep(1.05); return pts
def ambiguous(u): return len(u) >= 2 and max(hav(a, b) for i, a in enumerate(u) for b in u[i + 1:]) > 150

wb = openpyxl.load_workbook("records_clean_v2.xlsx"); ws = wb.active
rows = list(ws.values); H = list(rows[0]); C = {n: H.index(n) for n in H}; data = rows[1:]
def _nrec(r):
    try: return int(r[C["n_records"]])
    except Exception: return 0
data = sorted(data, key=lambda r: -_nrec(r))
FOREIGN = ["китай", "сша", "аляска", "канад", "герман", "польш", "франц", "англ", "испан", "итал",
           "африк", "танзан", "малави", "австрал", "япон", "монгол", "иран", "турц", "плато", "сахар"]
def cyr_foreign(loc): return any(w in loc.lower() for w in FOREIGN)

out = []; geo_n = fixed = dropped = unc = nq = 0
for i, r in enumerate(data):
    loc = str(r[C["nearest_locality"]]).strip(); adm = str(r[C["administrative_unit"]]).strip()
    adm = "" if adm in ("ND", "None", "") else adm
    latlon = None; conf = ""
    if not cyr_foreign(loc):
        k = loc + "|" + adm
        if k not in c_sec: nq += 1
        latlon = geocode(loc, adm)
        if latlon:
            bbox = region_hint(r[C["sources"]])
            if bbox and not inbox(latlon[0], latlon[1], bbox):     # омоним вне региона -> фикс
                fx = geocode_box(loc, bbox)
                if fx: latlon = fx; fixed += 1
                else: latlon = None; dropped += 1
        if latlon:
            geo_n += 1
            if adm: conf = "ok"
            else:
                if loc not in c_amb: nq += 1
                conf = "uncertain" if ambiguous(candidates(loc)) else "ok"
                unc += conf == "uncertain"
    out.append((r, latlon, conf))
    if nq and nq % 50 == 0:
        for f, cc in [("geocache_sections.json", c_sec), ("geocache_region.json", c_reg), ("geocache_ambig.json", c_amb)]:
            json.dump(cc, open(HERE / f, "w", encoding="utf-8"), ensure_ascii=False)
        print(f"  {i+1}/{len(data)} | геокод {geo_n} | fixed {fixed} dropped {dropped} unc {unc} | новых запросов {nq}", flush=True)

for f, cc in [("geocache_sections.json", c_sec), ("geocache_region.json", c_reg), ("geocache_ambig.json", c_amb)]:
    json.dump(cc, open(HERE / f, "w", encoding="utf-8"), ensure_ascii=False)
ILL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
def xl(v): return ILL.sub("", str(v)) if v is not None else ""
wb2 = openpyxl.Workbook(); w = wb2.active; w.title = "sections"; w.append(list(H) + ["lat", "lon", "loc_confidence"])
for r, latlon, conf in out:
    w.append([xl(v) for v in r] + ([round(latlon[0], 5), round(latlon[1], 5), conf] if latlon else ["ND", "ND", ""]))
wb2.save("records_clean_geo_v2.xlsx")
print(f"\nГОТОВО. разрезов: {len(data)} | геокодировано: {geo_n} | омоним-фикс {fixed} снято {dropped} | uncertain {unc}")
print(f"новых Nominatim-запросов: {nq} -> records_clean_geo_v2.xlsx")
