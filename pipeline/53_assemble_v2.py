# Сборка v2: arch_local_v2/*.json -> плоская таблица sample_records_v2.xlsx.
# Разворачивает новые поля: типы мощности в отдельные колонки, сырые термины, excavation, geomorphic, source_kind.
# Дедуп по документу (берём вариант с бо́льшим n_records).
import os, sys, json, glob, collections, re
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "arch_local_v2")

best = {}
for fp in glob.glob(os.path.join(SRC, "*.json")):
    try: d = json.load(open(fp, encoding="utf-8"))
    except Exception: continue
    p = d.get("path", fp)
    if p not in best or d.get("n_records", 0) > best[p].get("n_records", 0):
        best[p] = d
docs = [d for d in best.values() if "error" not in d]

def dedup(xs):
    out = []
    for x in xs or []:
        if x not in out and str(x).strip() not in ("", "ND", "None"): out.append(x)
    return out

def cell(f):                                   # значение из обёртки {value, evidence}
    v = (f or {}).get("value")
    if isinstance(v, list): return "; ".join(str(x) for x in dedup(v))
    return str(v) if v not in (None, "") else "ND"

def ev(f):                                     # цитата-обоснование
    return str((f or {}).get("evidence", "") or "")[:250]

def thick(thlist, kind):                       # значения мощности заданного типа
    vals = [str(t.get("value_m")) for t in (thlist or [])
            if t.get("kind") == kind and t.get("value_m") not in (None, "", "ND")]
    return "; ".join(dedup(vals)) if vals else "ND"

COLS = ["source_file", "nearest_locality", "administrative_unit", "excavation_type", "geomorphic_position",
        "type_of_deposits", "deposit_raw_terms",
        "thickness_studied", "thickness_visible", "thickness_borehole_depth", "thickness_unspecified",
        "absolute_elevation_m", "stratigraphic_position", "dating_methods", "source_kind",
        "evidence_locality", "evidence_deposit", "evidence_geomorph"]

rows = []
for d in docs:
    src = d.get("path", "?").rsplit("/", 1)[-1]
    for r in d.get("records", []):
        th = r.get("thickness", [])
        rows.append([
            src, cell(r.get("nearest_locality")), cell(r.get("administrative_unit")),
            cell(r.get("excavation_type")), cell(r.get("geomorphic_position")),
            cell(r.get("type_of_deposits")), cell(r.get("deposit_raw_terms")),
            thick(th, "studied"), thick(th, "visible"), thick(th, "borehole_depth"), thick(th, "unspecified"),
            cell(r.get("absolute_elevation_m")), cell(r.get("stratigraphic_position")),
            cell(r.get("dating_methods")), (r.get("source_kind") or "ND"),
            ev(r.get("nearest_locality")), ev(r.get("type_of_deposits")), ev(r.get("geomorphic_position")),
        ])

import openpyxl
ILL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
def xl(v): return ILL.sub("", v) if isinstance(v, str) else v
wb = openpyxl.Workbook(); ws = wb.active; ws.title = "records"; ws.append(COLS)
for row in rows: ws.append([xl(c) for c in row])
wb.save(os.path.join(HERE, "sample_records_v2.xlsx"))

tp = sum(d.get("n_pages", 0) for d in docs); tr = sum(d.get("n_records", 0) for d in docs)
print(f"документов: {len(docs)} | страниц: {tp} | записей(сырых): {tr} | строк в таблице: {len(rows)}")
print(f"-> sample_records_v2.xlsx ({len(COLS)} колонок)")
