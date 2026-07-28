# Собирает карту в дизайне/фильтрах заказчика: генерит DATA/CATEGORIES/SUMMARY из наших данных
# (records_clean_geo.xlsx) в ИХ схеме и подставляет в ИХ HTML-шаблон (заменяя 3 const-строки).
# Всё остальное (вёрстка, стили, JS, фильтры OpenLayers) — из шаблона заказчика.
import openpyxl, json, os, sys, math, collections, re

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = r"C:\Users\rukav\Downloads\Telegram Desktop\loess_interactive_map_v6_accuracy_circles.html"
SRC = os.path.join(HERE, "..", "loess_pipeline", "records_clean_geo_v2.xlsx")
EVF = os.path.join(HERE, "..", "loess_pipeline", "section_evidence.json")
EV = json.load(open(EVF, encoding="utf-8")) if os.path.exists(EVF) else {}  # {locality_lower: {dep:[фразы], grounded}}
DEMF = os.path.join(HERE, "..", "loess_pipeline", "dem_elevation.json")
_dem = json.load(open(DEMF, encoding="utf-8")) if os.path.exists(DEMF) else {}
DEM = {}  # {(lat5,lon5): elev} — расчётная высота по цифровой модели рельефа
for _k, _v in _dem.items():
    try:
        _la, _lo = _k.split(","); DEM[(round(float(_la), 5), round(float(_lo), 5))] = _v
    except Exception:
        pass
ACCF = os.path.join(HERE, "..", "loess_pipeline", "accuracy_radius.json")
_acc = json.load(open(ACCF, encoding="utf-8")) if os.path.exists(ACCF) else {}
ACC = {}  # {(lat5,lon5): radius_m} — радиус точности по типу объекта (село/город/регион)
for _k, _v in _acc.items():
    try:
        _la, _lo = _k.split(","); ACC[(round(float(_la), 5), round(float(_lo), 5))] = _v
    except Exception:
        pass


def acc_bin(a):                               # бины точности как в шаблоне заказчика
    if a < 20: return "0–20 m"
    if a < 100: return "20–100 m"
    if a < 500: return "100–500 m"
    if a < 1000: return "500–1,000 m"
    if a < 2000: return "1–2 km"
    if a < 5000: return "2–5 km"
    return "≥5 km"
DASH = "–"  # en-dash, как в шаблоне

# канонический порядок значений (как в CATEGORIES заказчика) — для стабильных легенд
ORD_DEP = ["loess", "loess-like loam", "loess-like silty loam", "cover loam", "paleosol",
           "alluvium", "proluvium", "slope deposits", "till", "fluvioglacial",
           "lacustrine", "marine deposits", "aeolian sand", "eluvium", "volcanic ash", "alluvial sands"]
ORD_STR = ["Upper Pleistocene", "Middle Pleistocene", "Lower Pleistocene", "Holocene", "Lower Pliocene"]
ORD_DAT = ["ND", "OSL", "magnetostratigraphy", "14C", "TL", "(U-Th)/He", "(U–Th)/He"]


def fnum(v):
    try:
        return float(str(v).replace(",", "."))
    except Exception:
        return None


def split_tags(v):
    if v in (None, "", "ND"):
        return []
    return [t.strip() for t in str(v).split(";") if t.strip() and t.strip() != "ND"]


def parse_range(s):                            # "2.04–75" / "2.04" / "≥5" / "ND" -> (min, max)
    s = str(s or "").strip()
    if s in ("", "ND", "None"):
        return None, None
    nums = [float(x) for x in re.findall(r"\d+[.,]?\d*", s.replace(",", ".")) if x not in (".", "")]
    if not nums:
        return None, None
    if "≥" in s or ">" in s:
        return min(nums), None
    if "≤" in s or "<" in s:
        return None, max(nums)
    return min(nums), max(nums)


def bin20(v):
    if v is None:
        return None
    lo = int(math.floor(v / 20.0) * 20)
    return f"{lo}{DASH}{lo + 20} m"


def bins20_span(lo, hi):
    if lo is None and hi is None:
        return []
    lo = hi if lo is None else lo
    hi = lo if hi is None else hi
    if hi < lo:
        lo, hi = hi, lo
    a = int(math.floor(lo / 20.0) * 20)
    b = int(math.floor(hi / 20.0) * 20)
    return [f"{x}{DASH}{x + 20} m" for x in range(a, b + 20, 20)]


def bin5(v):                                  # мощность — мелкие классы по 5 м (просьба коллег)
    if v is None:
        return None
    lo = int(math.floor(v / 5.0) * 5)
    return f"{lo}{DASH}{lo + 5} m"


def bins5_span(lo, hi):
    if lo is None and hi is None:
        return []
    lo = hi if lo is None else lo
    hi = lo if hi is None else hi
    if hi < lo:
        lo, hi = hi, lo
    a = int(math.floor(lo / 5.0) * 5)
    b = int(math.floor(hi / 5.0) * 5)
    return [f"{x}{DASH}{x + 5} m" for x in range(a, b + 5, 5)]


def order_by(vals, canon):
    present = set(vals)
    out = [c for c in canon if c in present]
    out += sorted(v for v in present if v not in canon)
    return out


def bin_lo(label):  # для числовой сортировки диапазонов "X–Y m"; ND — в конец
    if label == "ND":
        return 10 ** 9
    try:
        return int(label.split(DASH)[0].strip())
    except Exception:
        return 0


wb = openpyxl.load_workbook(SRC)
ws = wb.active
H = [c.value for c in ws[1]]
C = {n: i for i, n in enumerate(H)}
groups = collections.defaultdict(list)
rid = 0
for r in list(ws.values)[1:]:
    lat, lon = fnum(r[C["lat"]]), fnum(r[C["lon"]])
    if lat is None or lon is None:
        continue
    rid += 1
    th_studied = str(r[C["thickness_studied"]] or "ND").strip()          # v2: мощность 4 типами (строки-диапазоны)
    th_visible = str(r[C["thickness_visible"]] or "ND").strip()
    th_borehole = str(r[C["thickness_borehole_depth"]] or "ND").strip()
    th_unspec = str(r[C["thickness_unspecified"]] or "ND").strip()
    _allth = [parse_range(x) for x in (th_studied, th_visible, th_borehole, th_unspec)]
    thmin = min([lo for lo, hi in _allth if lo is not None], default=None)
    thmax = max([hi for lo, hi in _allth if hi is not None], default=None)
    elmin, elmax = fnum(r[C["elevation_min_m"]]), fnum(r[C["elevation_max_m"]])
    loc = str(r[C["nearest_locality"]] or "ND").strip()
    adm = str(r[C["administrative_unit"]] or "ND").strip() or "ND"
    dep = split_tags(r[C["type_of_deposits"]])
    raw = split_tags(r[C["deposit_raw_terms"]])
    strat = split_tags(r[C["stratigraphic_position"]])
    dat = split_tags(r[C["dating_methods"]])
    srck = split_tags(r[C["source_kinds"]])
    exc_tags = split_tags(r[C["excavation_type"]]) or ["ND"]     # склеенную строку -> отдельные теги (иначе 23 комбо)
    exc = "; ".join(exc_tags) if exc_tags != ["ND"] else "ND"
    geomp = str(r[C["geomorphic_position"]] or "ND").strip() or "ND"
    chrono = "Yes" if dat else "No"
    pubs = [s.strip() for s in str(r[C["sources"]] or "").split("|") if s.strip()]
    feat = f"{loc}, {adm}" if adm != "ND" else loc
    rec = {
        "id": str(rid), "lat": lat, "lon": lon,
        "accuracy": ACC.get((round(lat, 5), round(lon, 5)), 5000),
        "thickness": thmax if thmax is not None else thmin,
        "elevation": elmax if elmax is not None else elmin,
        "typeExcavationRaw": exc, "typeExcavationTags": exc_tags,
        "geomModern": "ND", "geomPosition": geomp,
        "rawTerms": raw, "sourceKinds": srck,
        "thStudied": th_studied, "thVisible": th_visible, "thBorehole": th_borehole, "thUnspec": th_unspec,
        "featureName": feat, "locality": loc, "admin": adm,
        "depositRaw": "; ".join(dep) if dep else "ND", "depositTags": dep or ["ND"],
        "stratRaw": "; ".join(strat) if strat else "ND", "stratTags": strat or ["ND"],
        "chronoRaw": "true" if chrono == "Yes" else "false", "chrono": chrono,
        "datingRaw": "; ".join(dat) if dat else "ND", "datingTags": dat or ["ND"],
        "nMag": 0, "nC14": 0, "nOSL": 0, "nTL": 0, "nUThHe": 0,
        "publication1": pubs[0] if pubs else "ND", "doi1": "ND",
        "principalInvestigator": "ND", "comments": "",
        "contributor": "auto-extraction (loess pipeline)",
        "_thmin": thmin, "_thmax": thmax, "_elmin": elmin, "_elmax": elmax,
        "_locconf": (r[C["loc_confidence"]] if "loc_confidence" in C else "ok"),
        "_nsrc": int(fnum(r[C["n_sources"]]) or 1),
        "elevationRange": bin20((elmin if elmin is not None else elmax)),
        "thicknessRange": bin5((thmax if thmax is not None else thmin)),
        "accuracyRange": acc_bin(ACC.get((round(lat, 5), round(lon, 5)), 5000)),
    }
    groups[(round(lat, 6), round(lon, 6))].append(rec)

DATA = []
cat = {k: set() for k in ("typeExcavation", "depositType", "stratigraphicPosition", "datingMethod",
                          "chronologicalData", "elevationRanges", "thicknessRanges", "accuracyRanges")}
for (lat, lon), recs in groups.items():
    def uni(key):
        s = []
        for r in recs:
            for t in r[key]:
                if t not in s:
                    s.append(t)
        return s
    thmins = [r["_thmin"] for r in recs if r["_thmin"] is not None]
    thmaxs = [r["_thmax"] for r in recs if r["_thmax"] is not None]
    elmins = [r["_elmin"] for r in recs if r["_elmin"] is not None]
    elmaxs = [r["_elmax"] for r in recs if r["_elmax"] is not None]
    minTh = min(thmins) if thmins else None
    maxTh = max(thmaxs) if thmaxs else None
    minEl = min(elmins) if elmins else None
    maxEl = max(elmaxs) if elmaxs else None
    elevRanges, thickRanges = [], []
    for r in recs:
        for b in bins20_span(r["_elmin"], r["_elmax"]):
            if b not in elevRanges:
                elevRanges.append(b)
        tb = bin5(r["_thmax"] if r["_thmax"] is not None else r["_thmin"])  # один 5-м класс по мощности (иначе список взрывается)
        if tb and tb not in thickRanges:
            thickRanges.append(tb)
    if not elevRanges:                        # нет высоты -> тег ND, иначе фильтр скроет маркер
        elevRanges = ["ND"]
    if not thickRanges:
        thickRanges = ["ND"]
    dep, strat, dat, chrono = uni("depositTags"), uni("stratTags"), uni("datingTags"), []
    for r in recs:
        if r["chrono"] not in chrono:
            chrono.append(r["chrono"])
    localities = list(dict.fromkeys(r["locality"] for r in recs))
    admins = list(dict.fromkeys(r["admin"] for r in recs))
    features = list(dict.fromkeys(r["featureName"] for r in recs))
    pubs = list(dict.fromkeys(r["publication1"] for r in recs if r["publication1"] != "ND"))
    mid_el = (minEl + maxEl) / 2 if (minEl is not None and maxEl is not None) else None
    mid_th = (minTh + maxTh) / 2 if (minTh is not None and maxTh is not None) else None
    search = " ".join([recs[0]["id"], "; ".join(localities), "; ".join(admins),
                       "; ".join(dep), "; ".join(strat), "; ".join(dat),
                       (pubs[0] if pubs else "")]).lower()
    mev, mgrounded = [], False                 # цитаты-обоснования по локальностям (для ручной проверки)
    for lc in localities:
        e = EV.get(lc.lower().strip())
        if e:
            for p in e["dep"]:
                if p not in mev and len(mev) < 3:
                    mev.append(p)
            mgrounded = mgrounded or e["grounded"]
    loc_unc = any(r.get("_locconf") == "uncertain" for r in recs)  # надёжность ЛОКАЦИИ (отдельная ось)
    maxNsrc = max((r.get("_nsrc", 1) for r in recs), default=1)
    # Data confidence — уверенность РАСПОЗНАВАНИЯ типа отложений (не путать с локацией), просьба коллег п.2
    if mgrounded and maxNsrc >= 3:
        conf = "high"
    elif mgrounded or maxNsrc >= 2 or mev:
        conf = "medium"
    else:
        conf = "low"
    accs = [r["accuracy"] for r in recs]       # радиус точности по типу объекта (разный, не плоские 5км)
    minAcc, maxAcc = min(accs), max(accs)
    accRanges = []
    for _a in accs:
        _b = acc_bin(_a)
        if _b not in accRanges:
            accRanges.append(_b)
    excT = uni("typeExcavationTags") or ["ND"]     # v2: реальный тип вскрытия
    geomPositions = [g for g in dict.fromkeys(r["geomPosition"] for r in recs) if g != "ND"]
    rawTerms = uni("rawTerms")
    sourceKinds = uni("sourceKinds")
    def _thd(key):
        vals = [r[key] for r in recs if r[key] not in ("ND", "", "None")]
        return "; ".join(dict.fromkeys(vals)) if vals else "ND"
    thStudied, thVisible, thBorehole, thUnspec = _thd("thStudied"), _thd("thVisible"), _thd("thBorehole"), _thd("thUnspec")
    m = {
        "markerId": f"{lat},{lon}", "lat": lat, "lon": lon,
        "title": localities[0] if localities else features[0],
        "ids": [r["id"] for r in recs], "sourceCount": len(recs),
        "records": [{"id": r["id"], "typeExcavationRaw": r["typeExcavationRaw"],  # только поля, что читает попап-таблица
                     "depositRaw": r["depositRaw"], "stratRaw": r["stratRaw"],
                     "thickness": r["thickness"], "elevation": r["elevation"], "accuracy": r["accuracy"]} for r in recs],
        "typeExcavation": excT, "depositType": dep or ["ND"],
        "stratigraphicPosition": strat or ["ND"], "datingMethod": dat or ["ND"],
        "chronologicalData": chrono or ["No"],
        "elevationRanges": elevRanges, "thicknessRanges": thickRanges, "accuracyRanges": accRanges,
        "localities": localities, "admins": admins, "features": features,
        "publications": pubs, "dois": [],
        "geomPositions": geomPositions, "rawTerms": rawTerms, "sourceKinds": sourceKinds,
        "thStudied": thStudied, "thVisible": thVisible, "thBorehole": thBorehole, "thUnspec": thUnspec,
        "minAccuracy": minAcc, "maxAccuracy": maxAcc,
        "minElevation": minEl, "maxElevation": maxEl, "minThickness": minTh, "maxThickness": maxTh,
        "representativeElevationRange": bin20(mid_el), "representativeThicknessRange": bin5(mid_th),
        "representativeAccuracyRange": acc_bin(maxAcc),
        "searchText": search,
        "evidence": mev, "grounded": mgrounded,
        "locUncertain": loc_unc,
        "elevDem": DEM.get((round(lat, 5), round(lon, 5))),  # расчётная высота (DEM), если нет опубликованной
        "confidence": conf,
    }
    DATA.append(m)
    for t in excT: cat["typeExcavation"].add(t)
    for t in dep or ["ND"]: cat["depositType"].add(t)
    for t in strat or ["ND"]: cat["stratigraphicPosition"].add(t)
    for t in dat or ["ND"]: cat["datingMethod"].add(t)
    for t in (chrono or ["No"]): cat["chronologicalData"].add(t)
    for t in elevRanges: cat["elevationRanges"].add(t)
    for t in thickRanges: cat["thicknessRanges"].add(t)
    for _b in accRanges: cat["accuracyRanges"].add(_b)

CATEGORIES = {
    "typeExcavation": order_by(cat["typeExcavation"], ["outcrop", "borehole", "pit", "trench", "clearing", "quarry", "ND"]),
    "depositType": order_by(cat["depositType"], ORD_DEP),
    "stratigraphicPosition": order_by(cat["stratigraphicPosition"], ORD_STR),
    "datingMethod": order_by(cat["datingMethod"], ORD_DAT),
    "chronologicalData": order_by(cat["chronologicalData"], ["No", "Yes"]),
    "elevationRanges": sorted(cat["elevationRanges"], key=bin_lo),
    "thicknessRanges": sorted(cat["thicknessRanges"], key=bin_lo),
    "accuracyRanges": order_by(cat["accuracyRanges"],
                               ["0–20 m", "20–100 m", "100–500 m", "500–1,000 m", "1–2 km", "2–5 km", "≥5 km"]),
}
dup = sum(1 for m in DATA if len(m["records"]) > 1)
_accs = [m["maxAccuracy"] for m in DATA] or [5000]
SUMMARY = {"rawRecords": rid, "markers": len(DATA), "duplicateGroups": dup,
           "accuracyPresent": rid, "accuracyMin": float(min(_accs)), "accuracyMax": float(max(_accs))}

# --- подстановка в шаблон заказчика ---
def jsdump(o):
    return json.dumps(o, ensure_ascii=False, separators=(",", ":"))

lines = open(TEMPLATE, encoding="utf-8").read().split("\n")
out = []
repl = {"const DATA =": "const DATA = " + jsdump(DATA) + ";",
        "const CATEGORIES =": "const CATEGORIES = " + jsdump(CATEGORIES) + ";",
        "const SUMMARY =": "const SUMMARY = " + jsdump(SUMMARY) + ";"}
done = set()
for ln in lines:
    hit = next((p for p in repl if ln.lstrip().startswith(p)), None)
    if hit:
        out.append(repl[hit]); done.add(hit)
    else:
        out.append(ln)
assert len(done) == 3, f"заменено не всё: {done}"
html = "\n".join(out)

def patch(h, old, new):                       # правки шаблона по просьбам коллег (с проверкой совпадения)
    assert old in h, "патч не найден: " + old[:55]
    return h.replace(old, new)

# #1 подложка по умолчанию — нейтральная физическая без гос.границ (Esri World Physical)
html = patch(html,
    'const baseLayers = {\n  positron: new ol.layer.Tile({\n    visible: true,',
    'const baseLayers = {\n  physical: new ol.layer.Tile({\n    visible: true,\n'
    '    source: new ol.source.XYZ({ url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Physical_Map/MapServer/tile/{z}/{y}/{x}", attributions: "Tiles &copy; Esri", crossOrigin: "anonymous" })\n  }),\n'
    '  positron: new ol.layer.Tile({\n    visible: false,')
html = patch(html, 'layers: [baseLayers.positron,', 'layers: [baseLayers.physical, baseLayers.positron,')
html = patch(html,
    '<select id="basemapSelect">\n        <option value="positron">CartoDB Positron</option>',
    '<select id="basemapSelect">\n        <option value="physical">Physical relief (no borders)</option>\n        <option value="positron">CartoDB Positron</option>')
# #5 ранжирование: честный лейбл (большой радиус = худшая точность)
html = patch(html, '<option value="maxAccuracy">Maximum accuracy radius</option>',
                   '<option value="maxAccuracy">Coordinate precision (largest radius = worst)</option>')

# #2 попап: индикатор обоснованности + цитата-обоснование (ручная проверка аномалий — просьба коллег)
html = patch(html,
    '''      <div class="popup-key">Chronological data</div><div>${joinVals(m.chronologicalData)}</div>''',
    '''      <div class="popup-key">Chronological data</div><div>${joinVals(m.chronologicalData)}</div>
      <div class="popup-key">Deposit grounding</div><div>${m.grounded ? "quote in source" : "⚠ no explicit loess quote"}</div>
      <div class="popup-key">Location confidence</div><div>${m.locUncertain ? "⚠ uncertain (homonymous name)" : "ok"}</div>
      <div class="popup-key">Data confidence</div><div><b>${m.confidence}</b></div>''')

# #3 попап: расчётная высота по DEM, если нет опубликованной (с пометкой)
html = patch(html,
    '''      <div class="popup-key">Absolute elevation</div><div>${elevationText}</div>''',
    '''      <div class="popup-key">Absolute elevation</div><div>${(m.minElevation != null || m.maxElevation != null) ? elevationText : (m.elevDem != null ? fmtNum(m.elevDem, 0, " m") + " · DEM (computed)" : "ND")}</div>''')
html = patch(html,
    '''    ${(m.publications && m.publications.length) ? `<div><b>Publication</b><br>${escapeHtml(m.publications[0])}</div>` : ""}''',
    '''    ${(m.publications && m.publications.length) ? `<div><b>Publication</b><br>${escapeHtml(m.publications[0])}</div>` : ""}
    ${(m.evidence && m.evidence.length) ? `<div style="margin-top:8px"><b>Source phrase (for verification)</b><br>${m.evidence.map(e => `<span class="subtle">• ${escapeHtml(e)}</span>`).join("<br>")}</div>` : ""}''')

# v2: геоморфопозиция (после Excavation type — теперь реального)
html = patch(html,
    '''      <div class="popup-key">Excavation type</div><div>${joinVals(m.typeExcavation)}</div>''',
    '''      <div class="popup-key">Excavation type</div><div>${joinVals(m.typeExcavation)}</div>
      <div class="popup-key">Geomorphic position</div><div>${(m.geomPositions && m.geomPositions.length) ? escapeHtml(m.geomPositions.join("; ")) : "ND"}</div>''')
# v2: дословные термины отложений (для проверки лёсс/лёссовидный/покровный)
html = patch(html,
    '''      <div class="popup-key">Deposit type</div><div>${joinVals(m.depositType)}</div>''',
    '''      <div class="popup-key">Deposit type</div><div>${joinVals(m.depositType)}</div>
      <div class="popup-key">Deposit (source terms)</div><div>${(m.rawTerms && m.rawTerms.length) ? escapeHtml(m.rawTerms.join("; ")) : "ND"}</div>''')
# v2: мощность по типам (изученная / видимая / глубина скважины)
html = patch(html,
    '''      <div class="popup-key">Thickness / depth</div><div>${thicknessText}</div>''',
    '''      <div class="popup-key">Thickness / depth</div><div>${[["studied",m.thStudied],["visible",m.thVisible],["borehole",m.thBorehole],["general",m.thUnspec]].filter(x=>x[1]&&x[1]!="ND").map(x=>`${x[0]}: ${escapeHtml(x[1])} m`).join("<br>") || thicknessText}</div>''')

# === ВЕРИФИКАЦИЯ (ручная проверка данных) — кнопки в карточке + вызов Cloud Function fn_verify ===
# VERIFY_API пустой -> фоллбэк на localStorage (демо). Заполнить URL функции после её деплоя.
VERIFY_API_URL = os.environ.get("VERIFY_API", "")   # можно задать через env при сборке
VERIFY_JS = '''const VERIFY_API = "__VERIFY_API__";
function _vfKey(sid){ return "verify_" + sid; }
async function loadVerify(sid){
  let list = [];
  try {
    if (VERIFY_API) { const r = await fetch(VERIFY_API + "?section_id=" + encodeURIComponent(sid)); list = (await r.json()).corrections || []; }
    else { list = JSON.parse(localStorage.getItem(_vfKey(sid)) || "[]"); }
  } catch(e) {}
  const el = document.getElementById("vf-list");
  if (el) el.innerHTML = list.length
    ? ("<b>Проверки (" + list.length + "):</b><br>" + list.map(c => "• " + escapeHtml(c.field) + ": <b>" + escapeHtml(c.verdict) + "</b>" + (c.comment ? " — " + escapeHtml(c.comment) : "")).join("<br>"))
    : "<span class='subtle'>проверок пока нет</span>";
}
async function submitVerify(sid, verdict){
  const field = (document.getElementById("vf-field") || {}).value || "overall";
  const comment = (document.getElementById("vf-comment") || {}).value || "";
  const rec = { section_id: sid, field: field, verdict: verdict, comment: comment };
  try {
    if (VERIFY_API) { await fetch(VERIFY_API, { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify(rec) }); }
    else { const k = _vfKey(sid), cur = JSON.parse(localStorage.getItem(k) || "[]"); cur.push(Object.assign({ ts: Math.floor(Date.now()/1000) }, rec)); localStorage.setItem(k, JSON.stringify(cur)); }
    const ci = document.getElementById("vf-comment"); if (ci) ci.value = "";
    loadVerify(sid);
  } catch(e) { alert("Не удалось сохранить: " + e); }
}
function vfBlock(sid){
  const s = escapeHtml(sid);
  return `<div style="margin-top:12px;border-top:1px solid #e5e7eb;padding-top:10px">
    <b>\\u2714 Проверка данных</b>
    <select id="vf-field" style="width:100%;margin:6px 0;padding:5px;border:1px solid #d1d5db;border-radius:6px">
      <option value="overall">Разрез в целом</option>
      <option value="location">Координаты/локация</option>
      <option value="deposit">Тип отложений</option>
      <option value="thickness">Мощность</option>
      <option value="elevation">Высота</option>
      <option value="dating">Датирование</option>
    </select>
    <div style="display:flex;gap:5px;margin-bottom:5px">
      <button onclick="submitVerify('${s}','correct')" style="flex:1;background:#dcfce7;border:1px solid #86efac;border-radius:6px;padding:5px;cursor:pointer">\\u2713 верно</button>
      <button onclick="submitVerify('${s}','partial')" style="flex:1;background:#fef9c3;border:1px solid #fde047;border-radius:6px;padding:5px;cursor:pointer">~ частично</button>
      <button onclick="submitVerify('${s}','incorrect')" style="flex:1;background:#fee2e2;border:1px solid #fca5a5;border-radius:6px;padding:5px;cursor:pointer">\\u2717 неверно</button>
    </div>
    <input id="vf-comment" placeholder="комментарий" style="width:100%;padding:5px;border:1px solid #d1d5db;border-radius:6px">
    <div id="vf-list" style="margin-top:7px;font-size:12px"></div>
  </div>`;
}
function showPopup(m, coordinate) {'''.replace("__VERIFY_API__", VERIFY_API_URL)
html = patch(html, 'function showPopup(m, coordinate) {', VERIFY_JS)
html = patch(html, 'overlay.setPosition(coordinate);',
             'overlay.setPosition(coordinate);\n  popupContent.innerHTML += vfBlock(m.markerId);\n  loadVerify(m.markerId);')

open(os.path.join(HERE, "index.html"), "w", encoding="utf-8").write(html)
kb = len(html.encode("utf-8")) / 1024
print(f"index.html (дизайн заказчика): маркеров {len(DATA)}, записей {rid}, дублей-групп {dup}, {kb:.0f} КБ")
print("CATEGORIES:", {k: len(v) for k, v in CATEGORIES.items()})
