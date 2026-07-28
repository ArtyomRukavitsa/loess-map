# Радиус точности по типу геокодированного объекта (reverse-геокод Nominatim по координате).
# Село ~1.5км / город ~8км / регион ~25км вместо плоских 5км. Пишет accuracy_radius.json {"lat,lon": radius_m}.
import openpyxl, json, time, sys, os, pathlib, urllib.request, urllib.parse

sys.stdout.reconfigure(encoding="utf-8")
HERE = pathlib.Path(__file__).parent
OUT = HERE / "accuracy_radius.json"
UA = "loess-sections/1.0 (research)"

RADIUS = {  # тип объекта -> радиус неопределённости, м
    "isolated_dwelling": 800, "farm": 800, "locality": 1200, "hamlet": 1200,
    "village": 1500, "quarter": 1500, "neighbourhood": 1500,
    "suburb": 2500, "borough": 2500, "municipality": 3000, "town": 4000,
    "city": 8000, "administrative": 15000, "county": 25000, "district": 25000,
    "state": 40000, "region": 40000,
}
DEFAULT = 5000

wb = openpyxl.load_workbook(os.environ.get("SRC", "records_clean_geo.xlsx")); ws = wb.active
H = [c.value for c in ws[1]]; C = {n: H.index(n) for n in H}
coords, seen = [], set()
for r in list(ws.values)[1:]:
    la, lo = r[C["lat"]], r[C["lon"]]
    if la in (None, "", "ND"):
        continue
    k = f"{la},{lo}"
    if k not in seen:
        seen.add(k); coords.append((la, lo))

acc = json.load(open(OUT, encoding="utf-8")) if OUT.exists() else {}
todo = [(la, lo) for la, lo in coords if f"{la},{lo}" not in acc]
print(f"уникальных координат: {len(coords)} | в кэше: {len(coords)-len(todo)} | запросить: {len(todo)}", flush=True)

def radius_of(la, lo):
    p = urllib.parse.urlencode({"lat": la, "lon": lo, "format": "json", "zoom": 14})
    try:
        req = urllib.request.Request("https://nominatim.openstreetmap.org/reverse?" + p, headers={"User-Agent": UA})
        d = json.load(urllib.request.urlopen(req, timeout=30))
        t = d.get("addresstype") or d.get("type") or ""
        return RADIUS.get(t, DEFAULT)
    except Exception:
        return DEFAULT

done = 0
for la, lo in todo:
    acc[f"{la},{lo}"] = radius_of(la, lo); done += 1
    time.sleep(1.05)
    if done % 50 == 0:
        json.dump(acc, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
        print(f"  {done}/{len(todo)}", flush=True)

json.dump(acc, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
import collections
dist = collections.Counter(acc.values())
print(f"\nГОТОВО. радиусов: {len(acc)} | распределение: {dict(sorted(dist.items()))} -> accuracy_radius.json")
