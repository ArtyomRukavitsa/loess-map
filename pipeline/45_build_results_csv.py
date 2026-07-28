# Строит results.csv для карты: ТОЛЬКО геокодированные разрезы, gold-схема + столбец n_sources (надёжность).
# Вход records_clean_geo.xlsx -> loess_map_app/results.csv (utf-8-sig). Логика строки — как в 44_map_to_gold.
import openpyxl, csv, os, sys, re
sys.stdout.reconfigure(encoding="utf-8")
ILL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_ND = {"none", "nan", "", "nd", "no data", "null"}
def ndize(v):                                                # мусорные пустышки -> единый "ND"
    s = "" if v is None else str(v).strip()
    return "ND" if s.lower() in _ND else s
def xl(v): return ILL.sub("", str(v)) if v is not None else ""

GOLD = ["ID", "N", "E", "Accuracy (radius, m)", "Thickness, m", "Absolute elevation, m a.s.l.",
        "Type of excavation", "Geomorphological position modern", "Geomorphological position",
        "Name of geographic feature", "Nearest locality", "Administrative unit", "Type of deposits",
        "Stratigraphic position", "Chronological data available", "Dating method",
        "Number of magnetostratigraphy boundaries", "Number of  14С dates ", "Number of  OSL dates",
        "Number of  TL dates", "Number of (U–Th)/He dates ", "Publication 1", "DOI / link 1",
        "Publication 2", "DOI / link 2", "Publication 3", "DOI / link 3", "Publication 4", "DOI / link 4",
        "Principal investigator", "Comments", "Data contributor", "n_sources"]

wb = openpyxl.load_workbook("records_clean_geo.xlsx"); ws = wb.active
rows = list(ws.values); H = list(rows[0]); C = {n: H.index(n) for n in H}

def rng(lo, hi):
    lo = None if lo in ("ND", "", None) else lo; hi = None if hi in ("ND", "", None) else hi
    if lo is None and hi is None: return "ND"
    if lo is not None and hi is not None: return str(lo) if lo == hi else f"{lo}–{hi}"
    return f"≥{lo}" if lo is not None else f"≤{hi}"

out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "loess_map_app", "results.csv")
n = 0
with open(out, "w", encoding="utf-8-sig", newline="") as f:
    wr = csv.writer(f); wr.writerow(GOLD)
    for i, r in enumerate(rows[1:], 1):
        lat = r[C["lat"]]; lon = r[C["lon"]]
        if lat in ("ND", "", None): continue                  # только геокодированные
        srcs = [s.strip() for s in str(r[C["sources"]]).split("|") if s.strip()][:4]
        pubs = srcs + [""] * (4 - len(srcs))
        dating = str(r[C["dating_methods"]]).strip()
        nsrc = r[C["n_sources"]]
        row = [i, lat, lon, 5000,
               rng(r[C["thickness_min_m"]], r[C["thickness_max_m"]]),
               rng(r[C["elevation_min_m"]], r[C["elevation_max_m"]]),
               "ND", "ND", "ND",
               r[C["nearest_locality"]], r[C["nearest_locality"]], r[C["administrative_unit"]] or "ND",
               r[C["type_of_deposits"]] or "ND", r[C["stratigraphic_position"]] or "ND",
               "Yes" if dating and dating != "ND" else "No", dating or "ND",
               "ND", "ND", "ND", "ND", "ND",
               pubs[0], "ND", pubs[1], "ND", pubs[2], "ND", pubs[3], "ND",
               "ND", f"консолидировано из {nsrc} источн.; " + str(r[C["evidence"]])[:200],
               "auto-extraction (loess pipeline)", nsrc]
        wr.writerow([v if isinstance(v, (int, float)) else xl(ndize(v)) for v in row]); n += 1  # числа как есть, текст -> ND-гигиена
print(f"results.csv: {n} геокодированных разрезов -> {os.path.normpath(out)}")
