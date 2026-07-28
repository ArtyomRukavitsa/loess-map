# DEM-высота по координатам геокодированных разрезов (open-meteo, батчами по 100).
# Пишет sidecar dem_elevation.json {"lat,lon": elev_m}. НЕ трогает xlsx (безопасно параллельно с др. задачами).
import openpyxl, json, sys, time, os, pathlib, urllib.request, urllib.parse

sys.stdout.reconfigure(encoding="utf-8")
HERE = pathlib.Path(__file__).parent
OUT = HERE / "dem_elevation.json"

wb = openpyxl.load_workbook(os.environ.get("SRC", "records_clean_geo.xlsx")); ws = wb.active
H = [c.value for c in ws[1]]; C = {n: H.index(n) for n in H}
coords = []
seen = set()
for r in list(ws.values)[1:]:
    la, lo = r[C["lat"]], r[C["lon"]]
    if la in (None, "", "ND"):
        continue
    k = f"{la},{lo}"
    if k not in seen:
        seen.add(k); coords.append((float(la), float(lo)))

dem = json.load(open(OUT, encoding="utf-8")) if OUT.exists() else {}
todo = [(la, lo) for la, lo in coords if f"{la},{lo}" not in dem]
print(f"уникальных координат: {len(coords)} | уже в кэше: {len(coords)-len(todo)} | запросить: {len(todo)}", flush=True)

B = 100
for i in range(0, len(todo), B):
    chunk = todo[i:i + B]
    lats = ",".join(str(la) for la, lo in chunk)
    lons = ",".join(str(lo) for la, lo in chunk)
    url = "https://api.open-meteo.com/v1/elevation?" + urllib.parse.urlencode({"latitude": lats, "longitude": lons})
    ok = False
    for attempt in range(4):
        try:
            d = json.load(urllib.request.urlopen(url, timeout=40))
            elevs = d.get("elevation", [])
            for (la, lo), e in zip(chunk, elevs):
                dem[f"{la},{lo}"] = e
            ok = True; break
        except Exception as ex:
            time.sleep(2 * (attempt + 1))
    if not ok:
        print(f"  чанк {i//B} не удался", flush=True)
    if (i // B) % 5 == 0:
        json.dump(dem, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
        print(f"  {min(i+B, len(todo))}/{len(todo)}", flush=True)
    time.sleep(0.3)

json.dump(dem, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
vals = [v for v in dem.values() if isinstance(v, (int, float))]
print(f"\nГОТОВО. DEM-высот: {len(dem)} | диапазон {min(vals):.0f}..{max(vals):.0f} м -> dem_elevation.json")
