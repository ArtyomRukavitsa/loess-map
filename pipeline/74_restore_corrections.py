# Возвращаем замечания проверяющих, потерявшиеся при переносе координат.
#
# Причина: файл правок называется по координате объекта (corrections/<lat><lon>.json). Как только
# координату исправляют — в том числе по самому же замечанию проверяющего, — карточка новой точки
# читает уже другой файл, а прежние вердикты остаются на старом ключе и с карты не видны никогда.
#
# Хуже того, 68_apply_corrections.py дописывал на НОВУЮ точку только автоответ «ИСПРАВЛЕНО»
# с вердиктом correct. В итоге разрез, помеченный экспертом как НЕВЕРНЫЙ с развёрнутым
# обоснованием, на карте выглядел подтверждённым, а обоснование было не найти.
#
# Скрипт переносит записи ЛЮДЕЙ со старого ключа на актуальный, дополняя файл, а не затирая его.
# Куда переносить, берём из трёх источников (в порядке надёжности):
#   1) applied_corrections.json — журнал переносов, сделанных скриптом 68;
#   2) точное совпадение названия разреза из текста замечания с объектом на карте;
#   3) ближайший маркер, если он ближе LIMIT км и такой один.
# Ничего не угадываем: если адресат не определён однозначно — оставляем как есть и сообщаем.
#
#   DRY=1 python 74_restore_corrections.py     — показать, что будет перенесено
import os, re, sys, json, math, pathlib, collections
import boto3
sys.stdout.reconfigure(encoding="utf-8")
HERE = pathlib.Path(__file__).parent
LEDGER = HERE / "applied_corrections.json"
XLSX = HERE / "records_clean_geo_v2.xlsx"
DRY = os.environ.get("DRY", "0") == "1"
LIMIT = float(os.environ.get("LIMIT", 5))          # км: ближе — считаем той же точкой

sec = {}
for line in open(HERE / ".secrets", encoding="utf-8"):
    line = line.strip()
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1); sec[k.strip()] = v.strip()
BUCKET = sec.get("BUCKET", "loess-results")
s3 = boto3.client("s3", endpoint_url="https://storage.yandexcloud.net", region_name="ru-central1",
                  aws_access_key_id=sec["AWS_ACCESS_KEY_ID"],
                  aws_secret_access_key=sec["AWS_SECRET_ACCESS_KEY"])

import openpyxl
wb = openpyxl.load_workbook(XLSX, read_only=True); ws = wb.active
rows = ws.iter_rows(values_only=True); H = list(next(rows)); C = {n: H.index(n) for n in H}
markers = {}                                        # markerId -> название
for r in rows:
    la, lo = r[C["lat"]], r[C["lon"]]
    if str(la) in ("None", "", "ND"): continue
    markers.setdefault(f"{round(float(la),6)},{round(float(lo),6)}",
                       str(r[C["nearest_locality"]] or ""))
wb.close()
print(f"маркеров на карте: {len(markers)}")


def as_key(mid):
    """Имя файла правок — это markerId без запятой: «43.316635,43.605317» -> «43.31663543.605317»."""
    return mid.replace(",", "")


def parse_key(sid):
    """Обратный разбор неоднозначен (нет разделителя) — перебираем все места разреза."""
    out = []
    for cut in range(2, len(sid) - 2):
        try: la, lo = float(sid[:cut]), float(sid[cut:])
        except ValueError: continue
        if 35 <= la <= 82 and 19 <= lo <= 180: out.append((la, lo))
    return out


def dist(a, b, c, d):
    return math.hypot((a - c) * 111.32, (b - d) * 111.32 * math.cos(math.radians(a)))


keys = [o["Key"] for p in s3.get_paginator("list_objects_v2")
        .paginate(Bucket=BUCKET, Prefix="corrections/") for o in p.get("Contents", [])]
ledger = json.load(open(LEDGER, encoding="utf-8")) if LEDGER.exists() else {}
# журнал скрипта 68: старая координата -> новая
moved = {}
for k, v in (ledger.items() if isinstance(ledger, dict) else []):
    tgt = v.get("to") or v.get("new") if isinstance(v, dict) else None
    if tgt:
        moved[as_key(str(k))] = tgt if isinstance(tgt, str) else ",".join(map(str, tgt))
    elif isinstance(v, (list, tuple)) and len(v) == 2:
        moved[as_key(str(k))] = f"{v[0]},{v[1]}"
print(f"переносов в журнале 68: {len(moved)}")

live = {as_key(m) for m in markers}
plan, unresolved = [], []
for key in keys:
    sid = key.split("/")[-1].replace(".json", "")
    if sid in live: continue                                    # адресат на месте, всё видно
    recs = json.loads(s3.get_object(Bucket=BUCKET, Key=key)["Body"].read())
    human = [r for r in recs if r.get("user") != "система"]
    if not human: continue                                      # только автоответ — переносить нечего

    dest, why = None, ""
    if sid in moved and moved[sid] in markers:
        dest, why = moved[sid], "журнал переносов 68"
    if not dest:
        # название разреза, названное в самом замечании («Разрез Мезин находится в черниговской»)
        text = " ".join((r.get("comment") or "") for r in human)
        named = [m for m, nm in markers.items()
                 if len(nm) >= 5 and re.search(r"\b" + re.escape(nm) + r"\b", text, re.I)]
        if len(named) == 1: dest, why = named[0], f"назван в замечании: {markers[named[0]]}"
    if not dest:
        near = []
        for la, lo in parse_key(sid):
            for m, nm in markers.items():
                mla, mlo = map(float, m.split(","))
                d = dist(la, lo, mla, mlo)
                if d <= LIMIT: near.append((d, m, nm))
        near.sort()
        if len(near) == 1 or (near and (len(near) == 1 or near[0][0] < 1)):
            dest, why = near[0][1], f"ближайший маркер {near[0][2]} в {near[0][0]:.1f} км"
    if dest:
        plan.append((sid, dest, human, why))
    else:
        unresolved.append((sid, human))

print(f"\nк переносу: {len(plan)} | адресат не определён: {len(unresolved)}\n")
for sid, dest, human, why in plan:
    print(f"  {sid}  ->  {dest}  «{markers.get(dest,'')}»   ({why})")
    for r in human:
        print(f"      [{r.get('verdict')}] {(r.get('comment') or '(без текста)')[:78]}")
for sid, human in unresolved:
    print(f"  ? {sid}  адресат не найден — оставляю как есть")
    for r in human:
        print(f"      [{r.get('verdict')}] {(r.get('comment') or '')[:78]}")

if DRY:
    print("\nсухой прогон — в бакет ничего не записано"); sys.exit(0)

done = 0
for sid, dest, human, why in plan:
    dkey = f"corrections/{as_key(dest)}.json"
    try:
        cur = json.loads(s3.get_object(Bucket=BUCKET, Key=dkey)["Body"].read())
    except s3.exceptions.NoSuchKey:
        cur = []
    except Exception as e:
        print(f"  ! {dest}: не смог прочитать ({str(e)[:50]}) — пропускаю, чтобы не затереть")
        continue
    # дополняем, а не заменяем; дубли по (вердикт, текст, время) не плодим
    seen = {(r.get("verdict"), r.get("comment"), r.get("ts")) for r in cur}
    add = [dict(r, movedFrom=sid) for r in human
           if (r.get("verdict"), r.get("comment"), r.get("ts")) not in seen]
    if not add:
        print(f"  = {dest}: уже перенесено"); continue
    s3.put_object(Bucket=BUCKET, Key=dkey, ContentType="application/json",
                  Body=json.dumps(cur + add, ensure_ascii=False).encode())
    done += len(add)
    print(f"  + {dest}: вернул {len(add)} замечан. (было {len(cur)})")

print(f"\nвосстановлено записей: {done}")
