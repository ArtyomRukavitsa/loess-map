# Маппинг рабочей чистой БД (records_clean_geo.xlsx) в GOLD-схему заказчика (32 колонки). Выход records_gold.xlsx.
import openpyxl, sys, re
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
        "Stratigraphic position", "Chrofalselogical data available", "Dating method",
        "Number of magnetostratigraphy boundaries", "Number of  14С dates ", "Number of  OSL dates",
        "Number of  TL dates", "Number of (U–Th)/He dates ", "Publication 1", "DOI / link 1",
        "Publication 2", "DOI / link 2", "Publication 3", "DOI / link 3", "Publication 4", "DOI / link 4",
        "Principal investigator", "Comments", "Data contributor"]

wb = openpyxl.load_workbook("records_clean_geo.xlsx"); ws = wb.active
rows = list(ws.values); H = list(rows[0]); C = {n: H.index(n) for n in H}

def rng(lo, hi):                                              # диапазон "X–Y" / "X" / "ND"
    lo = None if lo in ("ND", "", None) else lo; hi = None if hi in ("ND", "", None) else hi
    if lo is None and hi is None: return "ND"
    if lo is not None and hi is not None: return str(lo) if lo == hi else f"{lo}–{hi}"
    return f"≥{lo}" if lo is not None else f"≤{hi}"

wb2 = openpyxl.Workbook(); w = wb2.active; w.title = "base"; w.append(GOLD)
n_geo = 0
for i, r in enumerate(rows[1:], 1):
    lat = r[C["lat"]]; lon = r[C["lon"]]; geo = lat not in ("ND", "", None)
    if geo: n_geo += 1
    srcs = [s.strip() for s in str(r[C["sources"]]).split("|") if s.strip()][:4]
    pubs = srcs + [""] * (4 - len(srcs))
    dating = str(r[C["dating_methods"]]).strip()
    row = [i,                                                 # ID
           lat if geo else "ND", lon if geo else "ND",       # N, E
           5000 if geo else "ND",                            # Accuracy (город-уровень)
           rng(r[C["thickness_min_m"]], r[C["thickness_max_m"]]),
           rng(r[C["elevation_min_m"]], r[C["elevation_max_m"]]),
           "ND", "ND", "ND",                                 # excavation, geomorph modern/pos
           r[C["nearest_locality"]], r[C["nearest_locality"]], r[C["administrative_unit"]] or "ND",
           r[C["type_of_deposits"]] or "ND", r[C["stratigraphic_position"]] or "ND",
           "Yes" if dating and dating != "ND" else "No",     # Chronological data available
           dating or "ND",
           "ND", "ND", "ND", "ND", "ND",                     # # magneto/14C/OSL/TL/UThHe (не считаем)
           pubs[0], "ND", pubs[1], "ND", pubs[2], "ND", pubs[3], "ND",
           "ND",                                             # Principal investigator
           f"консолидировано из {r[C['n_sources']]} источн.; " + str(r[C["evidence"]])[:200],  # Comments
           "auto-extraction (loess pipeline)"]               # Data contributor
    w.append([v if isinstance(v, (int, float)) else xl(ndize(v)) for v in row])  # числа как числа, текст -> ND-гигиена
wb2.save("records_gold.xlsx")
print(f"GOLD-таблица: {len(rows)-1} разрезов -> records_gold.xlsx | с координатами: {n_geo} | колонок: {len(GOLD)}")
