# Собирает карту в дизайне/фильтрах заказчика: генерит DATA/CATEGORIES/SUMMARY из наших данных
# (records_clean_geo.xlsx) в ИХ схеме и подставляет в ИХ HTML-шаблон (заменяя 3 const-строки).
# Всё остальное (вёрстка, стили, JS, фильтры OpenLayers) — из шаблона заказчика.
import openpyxl, json, os, sys, math, collections, re

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
# Каталоги задаются через окружение — тогда один и тот же сборщик работает локально и в облачной функции
# (там данные скачиваются во временную папку). По умолчанию — обычная раскладка проекта.
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(HERE, "..", "loess_pipeline"))
OUT_DIR = os.environ.get("OUT_DIR", HERE)
TEMPLATE = os.environ.get("TEMPLATE", os.path.join(HERE, "template_customer_map.html"))
SRC = os.environ.get("SRC_XLSX", os.path.join(DATA_DIR, "records_clean_geo_v2.xlsx"))
EVF = os.path.join(DATA_DIR, "section_evidence.json")
EV = json.load(open(EVF, encoding="utf-8")) if os.path.exists(EVF) else {}  # {locality_lower: {dep:[фразы], grounded}}
DEMF = os.path.join(DATA_DIR, "dem_elevation.json")
_dem = json.load(open(DEMF, encoding="utf-8")) if os.path.exists(DEMF) else {}
DEM = {}  # {(lat5,lon5): elev} — расчётная высота по цифровой модели рельефа
for _k, _v in _dem.items():
    try:
        _la, _lo = _k.split(","); DEM[(round(float(_la), 5), round(float(_lo), 5))] = _v
    except Exception:
        pass
ACCF = os.path.join(DATA_DIR, "accuracy_radius.json")
_acc = json.load(open(ACCF, encoding="utf-8")) if os.path.exists(ACCF) else {}
ACC = {}  # {(lat5,lon5): radius_m} — радиус точности по типу объекта (село/город/регион)
for _k, _v in _acc.items():
    try:
        _la, _lo = _k.split(","); ACC[(round(float(_la), 5), round(float(_lo), 5))] = _v
    except Exception:
        pass
PLF = os.path.join(DATA_DIR, "page_links_v2.json")
PAGELINKS = json.load(open(PLF, encoding="utf-8")) if os.path.exists(PLF) else {}  # {"lat,lon": [{src,pages,approx}]}
KINDF = os.path.join(DATA_DIR, "object_kind.json")
# Что именно описано в публикации — главная сложность по мнению геолога: «конкретная выработка,
# сводный разрез, стратиграфическое подразделение или регион в целом». Определено моделью по цитатам.
OBJKIND = json.load(open(KINDF, encoding="utf-8")) if os.path.exists(KINDF) else {}
COLF = os.path.join(DATA_DIR, "column_data.json")
# Данные со стратиграфических колонок. Привязываем СТРОГО ПО СТРАНИЦЕ: колонка извлечена
# с конкретной страницы конкретной публикации, а у нас уже есть точная связь объект→публикация→страница.
# Привязка по названию не годится — «Колотова балка» склеивалась с любой «балкой».
COLS = {k: v for k, v in (json.load(open(COLF, encoding="utf-8")) if os.path.exists(COLF) else {}).items()
        if v.get("has_column")}
ROTF = os.path.join(DATA_DIR, "rotated_pages.json")
_rot = json.load(open(ROTF, encoding="utf-8")) if os.path.exists(ROTF) else {}
ROTPUB = _rot.get("publications", {})   # {slug публикации: угол} — книги, отсканированные боком целиком
SCF = os.path.join(DATA_DIR, "scan_index.json")
SCANIDX = json.load(open(SCF, encoding="utf-8")) if os.path.exists(SCF) else {}    # {публикация: {страница: ключ}}

import re as _re
UPLOAD_MARK = _re.compile(r"\s*\(загружено пользователем\)\s*$", _re.I)

def scan_slug(name):                       # тот же slug, что у 60_render_scans.py
    import hashlib
    base = _re.sub(r"[^0-9A-Za-zА-Яа-яЁё]+", "_", name.rsplit(".", 1)[0]).strip("_")[:52]
    return f"{base}_{hashlib.md5(name.encode('utf-8')).hexdigest()[:6]}"

# Замечание геолога: смешиваются конкретные горные выработки и сводные (обобщённые) разрезы.
# Обозначения выработок («скв. I», «обн. 6», «шурф 9/96») уже есть в цитатах — достаём их оттуда.
# Отдельными точками их не показать: собственных координат у скважин в тексте нет.
EXC_ID_RE = _re.compile(
    r"(скв(?:ажин\w*|\.)?\s*№?\s*[\dIVX][\d/IVX]{0,4}"
    r"|шурф\w*\s*№?\s*\d[\d/]{0,4}"
    r"|расчистк\w*\s*№?\s*\d[\d/]{0,4}"
    r"|обн(?:ажени\w*|\.)\s*№?\s*\d[\d/]{0,3}"
    r"|разрез\s*№\s*\d[\d/]{0,3}"
    r"|Site\s*\d{1,3}"
    r"|[A-Z]{2}-\d{2}[A-Z\d\-]{0,6})", _re.I)
SUMMARY_RE = _re.compile(r"(сводн\w+\s+(?:разрез|колонк)|обобщённ\w+\s+разрез|обобщенн\w+\s+разрез"
                         r"|типов\w+\s+разрез|опорн\w+\s+разрез)", _re.I)
_YEAR_RE = _re.compile(r"\b(1[89]\d{2}|20[0-3]\d)\b")

def exc_ids(phrases):
    out, seen = [], set()
    for p in phrases:
        for m in EXC_ID_RE.finditer(p):
            v = _re.sub(r"\s+", " ", m.group(0)).strip(" .,;")
            if _YEAR_RE.search(v): continue          # «скв.1974» — это год, а не номер выработки
            # ключ без падежных окончаний: «расчистка 217» и «расчистки 217» — одно и то же
            word, num = _re.match(r"([^\d]*)(.*)", v.lower()).groups()
            key = (_re.sub(r"[^a-zа-я]", "", word)[:5], num.strip())
            if key in seen: continue
            seen.add(key); out.append(v)
    return out[:12]

# «борисоглебский лёсс», «армавирская свита» — названия подразделений, образованные от топонимов
UNIT_RE = _re.compile(r"([А-ЯЁ][а-яё]+ск)(?:ий|ая|ое|ого|ой|ом|ую)\s+"
                      r"(?:лесс|лёсс|свит|горизонт|почв|толщ|слои)", _re.I)

def _stem(s):
    s = str(s or "").lower().replace("ё", "е")
    for suf in ("ский", "ская", "ское", "ского", "ской", "ском", "ий", "ая", "ое", "а", "ы", "и"):
        if s.endswith(suf): return s[:-len(suf)]
    return s

def _same_stem(title, unit):
    a, b = _stem(title), _stem(unit)
    return len(a) >= 5 and len(b) >= 5 and (a[:6] == b[:6])

def _col_norm(s):                          # для сверки названия разреза на рисунке с названием объекта
    return _re.sub(r"[^0-9a-zа-я]+", " ", str(s or "").lower().replace("ё", "е")).strip()

def with_scans(lst):                       # к привязке страниц добавляем те, для которых есть отрендеренный скан
    out = []
    for e in lst:
        d = dict(e)
        src = UPLOAD_MARK.sub("", e["src"])         # в индексе сканов имя без пометки загрузки
        have = SCANIDX.get(src, {})
        sp = [p for p in e["pages"] if str(p) in have]
        if sp:
            d["scanSlug"] = scan_slug(src); d["scanPages"] = sp
        out.append(d)
    return out


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
    evs = [e.strip() for e in str(r[C["evidence"]] or "").split("||") if e.strip()]   # v2: фразы-основания самой записи
    feat = f"{loc}, {adm}" if adm != "ND" else loc
    rec = {
        "id": str(rid), "lat": lat, "lon": lon,
        "accuracy": ACC.get((round(lat, 5), round(lon, 5)), 5000),
        "thickness": thmax if thmax is not None else thmin,
        "elevation": elmax if elmax is not None else elmin,
        "typeExcavationRaw": exc, "typeExcavationTags": exc_tags,
        "geomModern": "ND", "geomPosition": geomp, "evidence": evs,
        "rawTerms": raw, "sourceKinds": srck,
        "thStudied": th_studied, "thVisible": th_visible, "thBorehole": th_borehole, "thUnspec": th_unspec,
        "featureName": feat, "locality": loc, "admin": adm,
        "depositRaw": "; ".join(dep) if dep else "ND", "depositTags": dep or ["ND"],
        "stratRaw": "; ".join(strat) if strat else "ND", "stratTags": strat or ["ND"],
        "chronoRaw": "true" if chrono == "Yes" else "false", "chrono": chrono,
        "datingRaw": "; ".join(dat) if dat else "ND", "datingTags": dat or ["ND"],
        "nMag": 0, "nC14": 0, "nOSL": 0, "nTL": 0, "nUThHe": 0,
        "pubs": pubs,
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
                          "chronologicalData", "elevationRanges", "thicknessRanges", "accuracyRanges",
                          "objectKind")}
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
    # v2: все фразы-основания самих записей (коллеги: трёх коротких фрагментов не хватает для проверки)
    mev_all = list(dict.fromkeys([p for r in recs for p in r["evidence"]]))
    mev_all += [p for p in mev if p not in mev_all]                # добить обоснованиями по типу отложений
    mev_all = mev_all[:14]
    # Замечание геолога: многие стратиграфические подразделения названы по населённым пунктам
    # («борисоглебский лёсс»), и упоминание такого названия ещё не значит, что там описан разрез.
    # Помечаем случай, когда имя объекта совпадает с названием подразделения — решает проверяющий.
    # Колонки цепляем по ДВУМ признакам сразу: страница (объект на неё ссылается) И название разреза
    # на рисунке. Одной страницы мало — на ней часто рисунок про СОСЕДНИЙ разрез: у «Араповичей»
    # так подхватилось «Посевкино». Одного названия тоже мало — склеивало «Колотову балку» с «балкой».
    _pl = with_scans(PAGELINKS.get(f"{lat},{lon}", []))
    _cols, _seen_col = [], set()
    _locs = [_col_norm(x) for x in localities if len(_col_norm(x)) >= 4]
    for e in _pl:
        sl = e.get("scanSlug") or scan_slug(UPLOAD_MARK.sub("", e["src"]))
        for p in e["pages"]:
            c = COLS.get(f"{sl}/p{p}")
            if not c: continue
            cname = _col_norm(c.get("section_name", ""))
            if not any(l in cname or cname in l for l in _locs): continue   # рисунок про другой разрез
            key = (c.get("section_name", ""), tuple(c.get("stratigraphic_units") or []))
            if key in _seen_col: continue
            _seen_col.add(key)
            _cols.append({"page": p, "name": c.get("section_name", ""),
                          "exc": c.get("excavation_id", ""),
                          "units": (c.get("stratigraphic_units") or [])[:16]})
    _cols = _cols[:4]
    # Что именно описано: берём разбор модели по цитатам, регулярки оставляем запасным вариантом.
    # Модель различает то, чего шаблоны не видят: «Заводское» выглядит селом, а речь о толще вообще.
    _excids = exc_ids(mev_all)
    _kind = OBJKIND.get(f"{lat},{lon}") or {}
    obj_kind = _kind.get("kind", "")
    obj_why = _kind.get("why", "")
    if obj_kind:
        obj_level = obj_kind
    else:
        obj_level = "сводный разрез" if SUMMARY_RE.search(" ".join(mev_all)) else \
                    ("конкретные выработки" if _excids else "")
    _unit = UNIT_RE.search(" ".join(mev_all))
    unit_warn = bool(_unit) and _same_stem(localities[0] if localities else "", _unit.group(1))
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
        # Разбивка по источникам (просьба геолога: один разрез описан в разных статьях, и данные
        # из них смешивать нельзя). Поэтому у каждой записи храним её публикации и типы мощности.
        "records": [{"id": r["id"], "typeExcavationRaw": r["typeExcavationRaw"],
                     "depositRaw": r["depositRaw"], "stratRaw": r["stratRaw"],
                     "thickness": r["thickness"], "elevation": r["elevation"], "accuracy": r["accuracy"],
                     "pubs": r["pubs"], "datingRaw": r["datingRaw"], "geomPosition": r["geomPosition"],
                     "thStudied": r["thStudied"], "thVisible": r["thVisible"],
                     "thBorehole": r["thBorehole"], "thUnspec": r["thUnspec"]} for r in recs],
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
        "evidence": mev_all, "grounded": mgrounded,
        "locUncertain": loc_unc, "unitWarn": unit_warn,
        "objLevel": obj_level, "excIds": _excids, "objWhy": obj_why,
        "objectKind": [obj_level or "не определено"],
        "elevDem": DEM.get((round(lat, 5), round(lon, 5))),  # расчётная высота (DEM), если нет опубликованной
        "confidence": conf,
        "pageLinks": _pl, "columns": _cols,          # страницы источника + данные со стратиграфических колонок
    }
    DATA.append(m)
    for t in excT: cat["typeExcavation"].add(t)
    for t in dep or ["ND"]: cat["depositType"].add(t)
    for t in strat or ["ND"]: cat["stratigraphicPosition"].add(t)
    for t in dat or ["ND"]: cat["datingMethod"].add(t)
    cat["objectKind"].add(obj_level or "не определено")
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
    # порядок = от самого ценного (реальная точка) к самому размытому
    "objectKind": order_by(cat["objectKind"], ["конкретная выработка", "сводный разрез",
                                               "стратиграфическое подразделение", "регион в целом",
                                               "неясно", "не определено"]),
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
    # maxZoom: 8 — реальный предел этого слоя Esri; без него вблизи приходят плитки «Map data not yet available»,
    # а с ним последний доступный уровень просто растягивается (и сверху ложится детальная подложка)
    '    source: new ol.source.XYZ({ url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Physical_Map/MapServer/tile/{z}/{y}/{x}", maxZoom: 8, attributions: "Tiles &copy; Esri", crossOrigin: "anonymous" })\n  }),\n'
    '  positron: new ol.layer.Tile({\n    visible: false,')
html = patch(html, 'layers: [baseLayers.positron,', 'layers: [baseLayers.physical, baseLayers.positron,')
html = patch(html,
    '<select id="basemapSelect">\n        <option value="positron">CartoDB Positron</option>',
    '<select id="basemapSelect">\n        <option value="physical">Physical relief (no borders)</option>\n        <option value="positron">CartoDB Positron</option>')
# #5 ранжирование: честный лейбл (большой радиус = худшая точность)
html = patch(html, '<option value="maxAccuracy">Maximum accuracy radius</option>',
                   '<option value="maxAccuracy">Coordinate precision (largest radius = worst)</option>')

# --- Переименование проекта (предложение коллег, раунд 2) ---
html = patch(html, '<title>Loess database interactive map — accuracy circles</title>',
                   '<title>Цифровой атлас геологических разрезов и палеоархивов</title>')
html = patch(html, '<h1>Loess database interactive map</h1>',
                   '<h1>Цифровой атлас геологических разрезов и палеоархивов</h1>\n'
                   '      <div style="margin-top:4px"><a href="upload.html" '
                   'style="color:#9ec9ff;font-size:12px">+ Загрузить публикацию</a></div>')

# --- #1 коллег: детальная подложка при приближении (Physical пропадает вблизи -> Carto Voyager с дорогами/подписями на крупном масштабе) ---
html = patch(html, 'popupCloser.onclick = function() {',
    '''const detailLayer = new ol.layer.Tile({ visible: false, zIndex: 6,
  source: new ol.source.XYZ({ url: "https://{a-c}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png",
    attributions: "&copy; CARTO &copy; OpenStreetMap contributors", crossOrigin: "anonymous" }) });
map.addLayer(detailLayer);
// У Esri World Physical тайлы заканчиваются на 8-м уровне (дальше «Map data not yet available»),
// что соответствует разрешению ~611 м/пиксель. Считаем именно по разрешению: getZoom() при плавном
// зуме колесом бывает дробным или неопределённым, и сравнение по номеру уровня подводит.
const DETAIL_RES = 611;
function _updateDetail(){
  try {
    const res = map.getView().getResolution();
    const phys = baseLayers.physical && baseLayers.physical.getVisible();
    detailLayer.setVisible(!!phys && res < DETAIL_RES);   // подробная — только вблизи и только под физической
  } catch (e) { /* подложка не должна ломать остальную карту */ }
}
map.on("moveend", _updateDetail);                      // срабатывает после любого зума и сдвига
map.getView().on("change:resolution", _updateDetail);  // и сразу в процессе зума, без ожидания конца
const _bmSel = document.getElementById("basemapSelect");
if (_bmSel) _bmSel.addEventListener("change", _updateDetail);
_updateDetail();

popupCloser.onclick = function() {''')

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
    ${(m.columns && m.columns.length) ? `<div style="margin-top:8px"><b>Со стратиграфической колонки</b>
      <div class="subtle" style="margin-bottom:3px">прочитано с рисунка в публикации, обозначения приведены как в оригинале</div>
      ${m.columns.map(c => `<div style="border-left:3px solid #cfe0d5;padding:2px 0 2px 8px;margin-top:3px">
        <div style="font-size:12px">${escapeHtml(c.name || "разрез")}${c.exc ? " · выработка " + escapeHtml(c.exc) : ""}
          <span class="subtle">· стр. ${c.page}</span></div>
        ${c.units && c.units.length ? `<div class="subtle">${escapeHtml(c.units.join(", "))}</div>` : ""}
      </div>`).join("")}</div>` : ""}
    ${m.unitWarn ? `<div style="margin-top:8px;background:#fff8e6;border:1px solid #f0e0b8;border-radius:6px;padding:7px 9px;font-size:12px">
      <b>Требует проверки.</b> Название объекта совпадает с названием стратиграфического подразделения
      (например, «борисоглебский лёсс»). Возможно, в публикации речь о слое, а не об отдельном разрезе.</div>` : ""}
    ${(m.records && m.records.length > 1) ? `<div style="margin-top:8px"><b>Сведения по источникам</b>
      <div class="subtle" style="margin-bottom:3px">данные из разных публикаций не смешаны</div>
      ${m.records.map(r => { const pub = (r.pubs && r.pubs.length) ? r.pubs[0] : "источник не указан";
        const th = [["изученная",r.thStudied],["видимая",r.thVisible],["скважина",r.thBorehole],["без уточнения",r.thUnspec]]
          .filter(x=>x[1]&&x[1]!="ND").map(x=>`${x[0]} ${escapeHtml(x[1])} м`).join(", ");
        const bits = [r.depositRaw!="ND"?escapeHtml(r.depositRaw):"", th,
                      r.stratRaw!="ND"?escapeHtml(r.stratRaw):"",
                      r.datingRaw!="ND"?"датирование: "+escapeHtml(r.datingRaw):"",
                      r.typeExcavationRaw!="ND"?escapeHtml(r.typeExcavationRaw):""].filter(Boolean);
        return `<div style="border-left:3px solid #dfe3e8;padding:3px 0 3px 8px;margin-top:4px">
          <div style="font-size:12px">${escapeHtml(pub.replace(/\\.pdf$/i,"").slice(0,60))}</div>
          <div class="subtle">${bits.join(" · ") || "без числовых данных"}</div></div>`; }).join("")}
      </div>` : ""}
    ${(m.pageLinks && m.pageLinks.length) ? `<div style="margin-top:6px"><b>Страница в скане</b> <span class="subtle">(номер — открыть скан)</span><br>${m.pageLinks.map(pl => { const t = pl.src.replace(/\\.pdf$/i,""); const sp = pl.scanPages || []; return `<span class="subtle">• ${escapeHtml(t.slice(0,48))}: стр. ${pl.pages.map(p => sp.indexOf(p) >= 0 ? `<a href="#" onclick="showScan('${escapeHtml(pl.scanSlug)}',${p},[${sp.join(",")}],'${escapeHtml(t.slice(0,40)).replace(/'/g,"")}',event)" style="color:#2563eb;font-weight:600">${p}</a>` : p).join(", ")}${pl.approx?" ≈":""}</span>`; }).join("<br>")}</div>` : ""}
    ${(m.evidence && m.evidence.length) ? `<div style="margin-top:8px"><b>Фрагменты публикации — основания (${m.evidence.length})</b><div style="max-height:190px;overflow-y:auto;margin-top:4px">${m.evidence.map(e => `<span class="subtle">• ${escapeHtml(e)}</span>`).join("<br>")}</div></div>` : ""}''')

# v2: геоморфопозиция (после Excavation type — теперь реального)
html = patch(html,
    '''      <div class="popup-key">Excavation type</div><div>${joinVals(m.typeExcavation)}</div>''',
    '''      <div class="popup-key">Excavation type</div><div>${joinVals(m.typeExcavation)}</div>
      ${m.objLevel ? `<div class="popup-key">Что описано</div><div>${escapeHtml(m.objLevel)}${
        m.objLevel === "регион в целом" ? ' <span class="subtle">— речь о территории, а не о точке</span>' :
        m.objLevel === "сводный разрез" ? ' <span class="subtle">— обобщение по нескольким выработкам</span>' :
        m.objLevel === "стратиграфическое подразделение" ? ' <span class="subtle">— название слоя, не разрез</span>' : ""}${
        m.objWhy ? `<div class="subtle">${escapeHtml(m.objWhy)}</div>` : ""}</div>` : ""}
      ${(m.excIds && m.excIds.length) ? `<div class="popup-key">Выработки в источнике</div><div>${escapeHtml(m.excIds.join(", "))}</div>` : ""}
      <div class="popup-key">Geomorphic position</div><div>${(m.geomPositions && m.geomPositions.length) ? (function(s){return escapeHtml(s.length>220?s.slice(0,220)+"…":s)})(m.geomPositions.join("; ")) : "ND"}</div>''')
# v2: дословные термины отложений (для проверки лёсс/лёссовидный/покровный)
html = patch(html,
    '''      <div class="popup-key">Deposit type</div><div>${joinVals(m.depositType)}</div>''',
    '''      <div class="popup-key">Deposit type</div><div>${joinVals(m.depositType)}</div>
      <div class="popup-key">Deposit (source terms)</div><div>${(m.rawTerms && m.rawTerms.length) ? (function(s){return escapeHtml(s.length>220?s.slice(0,220)+"…":s)})(m.rawTerms.join("; ")) : "ND"}</div>
      <div class="popup-key">Data source</div><div>${(m.sourceKinds && m.sourceKinds.length) ? escapeHtml(m.sourceKinds.join(", ")) : "ND"}</div>''')
# v2: мощность по типам (изученная / видимая / глубина скважины)
html = patch(html,
    '''      <div class="popup-key">Thickness / depth</div><div>${thicknessText}</div>''',
    '''      <div class="popup-key">Мощность и глубина</div><div>${[["Изученная толща",m.thStudied],["Видимая в обнажении",m.thVisible],["Глубина скважины/расчистки",m.thBorehole],["Тип не указан",m.thUnspec]].filter(x=>x[1]&&x[1]!="ND").map(x=>`<span class="subtle">${x[0]}:</span> ${escapeHtml(x[1])} м`).join("<br>") || thicknessText}</div>''')

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
             'overlay.setPosition(coordinate);\n  _curMarker = m;\n'
             '  popupContent.innerHTML += vfBlock(m.markerId);\n  loadVerify(m.markerId);')

# === ПРОСМОТР ИСХОДНОГО СКАНА (запрос коллег: как «Поиск по архивам» — видеть страницу источника) ===
SCAN_JS = '''const SCAN_BASE = "https://storage.yandexcloud.net/loess-map/scans/";
// Публикации, отсканированные боком целиком — разворачиваем сразу. Отдельные страницы не трогаем:
// в журналах широкие рисунки печатают повёрнутыми, и такая страница сама по себе нормальная.
const SCAN_ROT = __SCAN_ROT__;
let scanCur = null;

/* ---- подсветка фраз-оснований на скане ----
   Координаты строк посчитаны по той же картинке, что показываем, поэтому переводим их
   в проценты — тогда подсветка держится при любом масштабе окна. */
function _nrm(s){ return String(s||"").toLowerCase().replace(/ё/g,"е").replace(/[^0-9a-zа-я]+/g," ").trim(); }

function _matchLines(lines, phrases){
  const hit = [];
  const ph = phrases.map(_nrm).filter(p => p.length >= 12);
  if (!ph.length) return hit;
  lines.forEach((L, i) => {
    const t = _nrm(L[0]);
    if (t.length < 4) return;
    const lt = t.split(" ").filter(w => w.length >= 4);
    const ts = new Set(t.split(" "));
    for (const p of ph) {
      if (t.indexOf(p) >= 0) { hit.push(i); return; }                    // цитата целиком внутри строки
      if (t.length >= 12 && p.indexOf(t) >= 0) { hit.push(i); return; }  // строка внутри длинной цитаты
      const pt = p.split(" ").filter(w => w.length >= 4);
      if (pt.length >= 2) {                                              // бо́льшая часть слов цитаты в строке
        const c = pt.filter(w => ts.has(w)).length;
        if (c >= 2 && c / pt.length >= 0.6) { hit.push(i); return; }
      }
      if (lt.length >= 2) {                                              // и наоборот — строка покрыта цитатой
        const ps = new Set(p.split(" "));
        const c = lt.filter(w => ps.has(w)).length;
        if (c >= 2 && c / lt.length >= 0.6) { hit.push(i); return; }
      }
    }
  });
  return hit;
}

async function _drawHighlights(slug, page){
  const box = document.getElementById("scan-marks");
  if (!box) return;
  box.innerHTML = "";
  const phrases = (scanCur && scanCur.phrases) || [];
  if (!phrases.length) return;
  try {
    const url = SCAN_BASE + encodeURIComponent(slug) + "/p" + page + ".lines.json";
    const d = await (await fetch(url)).json();
    if (!d || !d.w || !d.h) return;
    const idx = _matchLines(d.lines || [], phrases);
    idx.forEach(i => {
      const [, x0, y0, x1, y1] = d.lines[i];
      const m = document.createElement("div");
      m.style.cssText = "position:absolute;background:rgba(255,214,0,.35);border:1px solid rgba(214,160,0,.85);" +
        "border-radius:2px;pointer-events:none;" +
        `left:${x0 / d.w * 100}%;top:${y0 / d.h * 100}%;` +
        `width:${(x1 - x0) / d.w * 100}%;height:${(y1 - y0) / d.h * 100}%;`;
      box.appendChild(m);
    });
    const cap = document.getElementById("scan-hint");
    if (cap) cap.textContent = idx.length ? `подсвечено фрагментов: ${idx.length}`
                                          : "фрагменты на этой странице не распознались дословно";
  } catch (e) { /* нет файла с координатами — просто показываем скан без подсветки */ }
}

let _curMarker = null;                    // объект, чья карточка сейчас открыта — из него берём фразы
const BTN = "padding:4px 9px;cursor:pointer;border-radius:5px;border:1px solid #56646f;background:#2c3742;color:#e8edf2";

function showScan(slug, page, pages, title, ev) {
  if (ev) ev.preventDefault();
  const phrases = (_curMarker && _curMarker.evidence) || [];
  // Угол: сначала выбор пользователя (он сохраняется), иначе — определённый для всей публикации
  let rot = 0;
  try { const s = localStorage.getItem("scanrot:" + slug); if (s !== null) rot = parseInt(s, 10) || 0; }
  catch (e) {}
  if (!rot && SCAN_ROT[slug]) rot = SCAN_ROT[slug];
  // page — текущая страница скана; pages — страницы, где нашлись фразы объекта (по ним быстрый переход)
  scanCur = { slug: slug, pages: pages, page: page, title: title, phrases: phrases, rot: ((rot % 360) + 360) % 360 };
  let box = document.getElementById("scan-box");
  if (!box) {
    box = document.createElement("div"); box.id = "scan-box";
    box.style.cssText = "position:fixed;inset:0;background:rgba(15,18,22,.92);z-index:9999;display:flex;" +
      "flex-direction:column;align-items:center;justify-content:center;padding:76px 14px 14px;overflow:hidden";
    box.onclick = e => { if (e.target === box) closeScan(); };
    box.innerHTML =
      // панель закреплена поверх картинки: повёрнутый скан занимает место по-старому и иначе накрывает кнопки
      '<div style="position:fixed;top:8px;left:50%;transform:translateX(-50%);z-index:10001;' +
      'background:rgba(20,26,32,.94);border-radius:8px;padding:6px 10px;' +
      'color:#e8edf2;font:13px sans-serif;display:flex;gap:8px;' +
      'align-items:center;flex-wrap:wrap;justify-content:center;max-width:96vw">' +
      `<button onclick="stepPage(-1)" style="${BTN}" title="предыдущая страница">\\u2039 стр.</button>` +
      '<span id="scan-cap" style="text-align:center"></span>' +
      `<button onclick="stepPage(1)" style="${BTN}" title="следующая страница">стр. \\u203a</button>` +
      `<button onclick="rotScan()" style="${BTN}" title="повернуть">\\u21bb</button>` +
      `<button onclick="closeScan()" style="${BTN}" title="закрыть">\\u2715</button></div>` +
      '<div id="scan-chips" style="position:fixed;top:48px;left:50%;transform:translateX(-50%);z-index:10001;' +
      'display:flex;gap:6px;flex-wrap:wrap;justify-content:center;max-width:96vw"></div>' +
      '<div id="scan-wrap" style="position:relative;display:inline-block;line-height:0;transition:transform .15s">' +
      '<img id="scan-img" style="max-width:95vw;max-height:78vh;background:#fff;box-shadow:0 6px 30px rgba(0,0,0,.5)">' +
      '<div id="scan-marks" style="position:absolute;inset:0;pointer-events:none"></div></div>' +
      '<div id="scan-hint" style="position:fixed;bottom:8px;left:50%;transform:translateX(-50%);' +
      'z-index:10001;color:#9fb0c0;font:12px sans-serif;background:rgba(20,26,32,.8);' +
      'border-radius:6px;padding:3px 9px"></div>';
    document.body.appendChild(box);
    document.getElementById("scan-img").onerror = function () {
      const h = document.getElementById("scan-hint");
      if (h) h.textContent = "эта страница не оцифрована — вернитесь к страницам с находками";
    };
    document.addEventListener("keydown", e => {
      const b = document.getElementById("scan-box");
      if (!b || b.style.display === "none") return;
      if (e.key === "Escape") closeScan();
      if (e.key === "ArrowLeft") stepPage(-1);
      if (e.key === "ArrowRight") stepPage(1);
      if (e.key.toLowerCase() === "r") rotScan();
    });
  }
  box.style.display = "flex";
  renderScan();
}

function renderScan() {
  const c = scanCur; if (!c) return;
  const marks = document.getElementById("scan-marks");
  if (marks) marks.innerHTML = "";
  const hint = document.getElementById("scan-hint");
  if (hint) hint.textContent = "";
  document.getElementById("scan-img").src = SCAN_BASE + encodeURIComponent(c.slug) + "/p" + c.page + ".jpg";
  const own = c.pages.indexOf(c.page);
  document.getElementById("scan-cap").textContent =
    c.title + " \\u2014 стр. " + c.page + (own < 0 ? " (соседняя)" : "");
  // быстрый переход к страницам, где нашлись фразы объекта
  const chips = document.getElementById("scan-chips");
  if (chips) chips.innerHTML = c.pages.map(p =>
    `<button onclick="goPage(${p})" style="${BTN};` +
    (p === c.page ? "background:#3d6b52;border-color:#4e8a68" : "") + `">${p}</button>`).join("");
  // при повороте на 90/270 меняем ограничения местами, иначе картинка вылезает за экран
  const img = document.getElementById("scan-img"), side = (c.rot === 90 || c.rot === 270);
  img.style.maxWidth = side ? "72vh" : "94vw";
  img.style.maxHeight = side ? "88vw" : "72vh";
  document.getElementById("scan-wrap").style.transform = "rotate(" + c.rot + "deg)";
  _drawHighlights(c.slug, c.page);
}

function stepPage(d) { if (scanCur && scanCur.page + d >= 1) { scanCur.page += d; renderScan(); } }
function goPage(p) { if (scanCur) { scanCur.page = p; renderScan(); } }
function rotScan() {                       // шаг 90°, выбор запоминается для этой публикации
  if (!scanCur) return;
  scanCur.rot = (scanCur.rot + 90) % 360;
  try { localStorage.setItem("scanrot:" + scanCur.slug, String(scanCur.rot)); } catch (e) {}
  renderScan();
}
function closeScan() { const b = document.getElementById("scan-box"); if (b) b.style.display = "none"; }
function showPopup(m, coordinate) {'''
SCAN_JS = SCAN_JS.replace("__SCAN_ROT__", json.dumps(ROTPUB, ensure_ascii=False))
html = patch(html, 'function showPopup(m, coordinate) {', SCAN_JS)

open(os.path.join(OUT_DIR, "index.html"), "w", encoding="utf-8").write(html)
kb = len(html.encode("utf-8")) / 1024
print(f"index.html (дизайн заказчика): маркеров {len(DATA)}, записей {rid}, дублей-групп {dup}, {kb:.0f} КБ")
print("CATEGORIES:", {k: len(v) for k, v in CATEGORIES.items()})
