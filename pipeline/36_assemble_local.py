# Собирает БД из arch_local/*.json -> замер (токены/стр, yield) + плоская таблица записей (с чисткой).
# Дедуп по документу (если есть и полный, и старый кусочный — берём с бо́льшим числом записей).
import os, sys, json, glob, collections
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "arch_local")

# --- собрать, дедуп по path ---
best = {}
for fp in glob.glob(os.path.join(OUT, "*.json")):
    try: d = json.load(open(fp, encoding="utf-8"))
    except Exception: continue
    p = d.get("path", fp)
    if p not in best or d.get("n_records", 0) > best[p].get("n_records", 0):
        best[p] = d
docs = list(best.values())
ok = [d for d in docs if "error" not in d]

tp = sum(d.get("n_pages", 0) for d in ok); tr = sum(d.get("n_records", 0) for d in ok); tt = sum(d.get("tokens", 0) for d in ok)
lines = [f"документов: {len(docs)}  ok: {len(ok)}",
         f"страниц: {tp}  записей: {tr}  токенов: {tt}",
         f"токенов/стр: {tt/tp:.0f}" if tp else "н/д",
         f"записей/стр: {tr/tp:.2f}" if tp else "н/д"]
for d in sorted(ok, key=lambda x: -x.get("n_records", 0))[:40]:
    lines.append(f"  {d.get('n_pages',0):>3}стр {d.get('n_records',0):>3}зап {d.get('tokens',0):>7}ток  {d.get('path','?').rsplit('/',1)[-1][:56]}")
open(os.path.join(HERE, "measure_local.txt"), "w", encoding="utf-8").write("\n".join(lines))
print("\n".join(lines[:4]))

# --- плоская таблица (чистка: дедуп мультиполей + стрип math::) ---
def clean(v): return v[6:] if isinstance(v, str) and v.startswith("math::") else v
def dedup(xs):
    out = []
    for x in xs or []:
        if x not in out: out.append(x)
    return out
def cell(f):
    v = (f or {}).get("value")
    if isinstance(v, list): return "; ".join(dedup([clean(x) for x in v]))
    return clean(v) if v not in (None, "") else "ND"

rows = []
for d in ok:
    src = d.get("path", "?").rsplit("/", 1)[-1]
    for r in d.get("records", []):
        rows.append([src, cell(r.get("nearest_locality")), cell(r.get("administrative_unit")), cell(r.get("thickness_m")),
                     cell(r.get("absolute_elevation_m")), cell(r.get("type_of_deposits")), cell(r.get("stratigraphic_position")),
                     cell(r.get("dating_methods")), (r.get("nearest_locality") or {}).get("evidence", "")[:200]])
import openpyxl, re
ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
def xl(v): return ILLEGAL.sub("", v) if isinstance(v, str) else v
wb = openpyxl.Workbook(); ws = wb.active; ws.title = "records"
ws.append(["source_file", "nearest_locality", "administrative_unit", "thickness_m", "absolute_elevation_m",
           "type_of_deposits", "stratigraphic_position", "dating_methods", "evidence_locality"])
for row in rows: ws.append([xl(c) for c in row])
wb.save(os.path.join(HERE, "sample_records.xlsx"))
print(f"строк: {len(rows)} -> sample_records.xlsx ; замер -> measure_local.txt")
