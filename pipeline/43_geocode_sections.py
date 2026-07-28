# Геокодинг разрезов из records_clean.xlsx: локация(+регион) -> координаты (валидировано: нас.пункт СНГ, bbox, кэш).
# Скипает кириллическое-иностранное. Сортировка по n_records (важные первыми). Выход records_clean_geo.xlsx. Резюм по кэшу.
import openpyxl, json, time, sys, pathlib, re, urllib.request, urllib.parse
sys.stdout.reconfigure(encoding="utf-8")
HERE = pathlib.Path(__file__).parent
CACHE = HERE / "geocache_sections.json"
ILL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
def xl(v): return ILL.sub("", str(v)) if v is not None else ""

FOREIGN = ["китай", "сша", "аляска", "канад", "герман", "польш", "франц", "англ", "испан", "итал",
           "африк", "танзан", "малави", "австрал", "япон", "монгол", "иран", "турц", "плато", "сахар"]
def is_cyr_foreign(loc): return any(w in loc.lower() for w in FOREIGN)

wb = openpyxl.load_workbook("records_clean.xlsx"); ws = wb.active
rows = list(ws.values); H = list(rows[0]); data = rows[1:]
C = {n: H.index(n) for n in H}

cache = json.load(open(CACHE, encoding="utf-8")) if CACHE.exists() else {}
UA = "loess-sections/1.0 (research)"
CIS = "ru,ua,kz,by,md,ge,az,am,uz,kg,tj,tm,ee,lv,lt"
SETTLE = {"city", "town", "village", "hamlet", "municipality", "administrative", "suburb", "borough", "isolated_dwelling", "locality", "quarter"}
def geocode(name, admin):
    key = name + "|" + (admin or "")
    if key in cache: return cache[key]
    q = name + (", " + admin if admin else "")
    p = urllib.parse.urlencode({"q": q, "format": "json", "limit": 5, "countrycodes": CIS})
    res = None
    try:
        req = urllib.request.Request("https://nominatim.openstreetmap.org/search?" + p, headers={"User-Agent": UA})
        for it in json.load(urllib.request.urlopen(req, timeout=30)):
            if it.get("class") in ("place", "boundary") and it.get("type") in SETTLE:
                lat, lon = float(it["lat"]), float(it["lon"])
                if 41 <= lat <= 82 and 19 <= lon <= 180:
                    res = [lat, lon]; break
    except Exception:
        res = None
    cache[key] = res; time.sleep(1.05); return res

# сортировка по n_records (важные первыми)
data = sorted(data, key=lambda r: -(r[C["n_records"]] or 0))
geo_n = skip_f = done = 0
out = []
for i, r in enumerate(data):
    loc = str(r[C["nearest_locality"]]).strip(); adm = str(r[C["administrative_unit"]]).strip()
    adm = "" if adm in ("ND", "None", "") else adm
    if is_cyr_foreign(loc):
        skip_f += 1; latlon = None
    else:
        key = loc + "|" + adm
        if key not in cache: done += 1
        latlon = geocode(loc, adm)
        if latlon: geo_n += 1
    out.append((r, latlon))
    if done and done % 50 == 0:
        json.dump(cache, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False)
        print(f"  геокодировано {i+1}/{len(data)} (успешно {geo_n})", flush=True)
json.dump(cache, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False)

# запись с координатами
wb2 = openpyxl.Workbook(); w = wb2.active; w.title = "sections"
w.append(list(H) + ["lat", "lon"])
for r, latlon in out:
    w.append([xl(v) for v in r] + ([round(latlon[0], 5), round(latlon[1], 5)] if latlon else ["ND", "ND"]))
wb2.save("records_clean_geo.xlsx")
print(f"\nГОТОВО. разрезов: {len(data)} | геокодировано: {geo_n} | кирилл-иностр скип: {skip_f} -> records_clean_geo.xlsx")
