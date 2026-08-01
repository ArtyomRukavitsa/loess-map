# Поиск повёрнутых сканов (замечание геолога: «в отдельных случаях скан открывается перевёрнутым»).
#
# Проверено опытом: Yandex Vision САМ разворачивает страницу, чтобы прочитать, — текст выходит верным
# при любой ориентации. Но координаты строк он возвращает в системе ИСХОДНОЙ картинки. Отсюда признаки:
#   • рамка строки ВЫСОКАЯ и УЗКАЯ  -> текст идёт вертикально -> страница лежит на боку (90°)
#   • рамки обычные, но порядок чтения идёт СНИЗУ ВВЕРХ -> страница перевёрнута (180°)
# Направление поворота на боку определяем по тому, куда идёт чтение по вертикали.
#
# Выход: rotated_pages.json — {"<slug>/pN": 90 | -90 | 180} (угол, на который карта повернёт при показе).
#   DRY=1 python 65_detect_rotated.py   — только отчёт, без записи
import os, sys, json, pathlib, statistics, collections, concurrent.futures
sys.stdout.reconfigure(encoding="utf-8")
HERE = pathlib.Path(__file__).parent
OUT = HERE / "rotated_pages.json"
DRY = os.environ.get("DRY", "0") == "1"
MIN_LINES = int(os.environ.get("MIN_LINES", 10))

sec = {}
for line in open(HERE / ".secrets", encoding="utf-8"):
    line = line.strip()
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1); sec[k.strip()] = v.strip()
import boto3
s3 = boto3.client("s3", endpoint_url="https://storage.yandexcloud.net", region_name="ru-central1",
                  aws_access_key_id=sec["AWS_ACCESS_KEY_ID"], aws_secret_access_key=sec["AWS_SECRET_ACCESS_KEY"])
SITE = os.environ.get("SITE_BUCKET", "loess-map")

keys, tok = [], None
while True:
    kw = {"Bucket": SITE, "Prefix": "scans/", "MaxKeys": 1000}
    if tok: kw["ContinuationToken"] = tok
    r = s3.list_objects_v2(**kw)
    keys += [o["Key"] for o in r.get("Contents", []) if o["Key"].endswith(".lines.json")]
    tok = r.get("NextContinuationToken")
    if not r.get("IsTruncated"): break
print(f"страниц с координатами строк: {len(keys)}", flush=True)

def check(key):
    try:
        d = json.loads(s3.get_object(Bucket=SITE, Key=key)["Body"].read())
    except Exception:
        return None
    L = d.get("lines") or []
    h = d.get("h") or 0
    if len(L) < MIN_LINES or not h: return None
    mw = statistics.median(l[3] - l[1] for l in L)          # ширина рамки строки
    mh = statistics.median(l[4] - l[2] for l in L)          # высота рамки строки
    n = max(3, len(L) // 5)
    y0 = statistics.median(l[2] for l in L[:n])             # где первые строки по порядку чтения
    y1 = statistics.median(l[2] for l in L[-n:])            # где последние
    name = key.replace("scans/", "").replace(".lines.json", "")
    if mh > mw * 1.5:                                       # строки вертикальные — лист на боку
        return name, (90 if y0 > y1 else -90)
    if y0 > y1 + h * 0.3:                                   # чтение снизу вверх при обычных строках
        return name, 180
    return None

res = {}
with concurrent.futures.ThreadPoolExecutor(24) as ex:
    for r in ex.map(check, keys):
        if r: res[r[0]] = r[1]

by_pub = collections.Counter(k.split("/")[0] for k in res)
tot_by_pub = collections.Counter(k.replace("scans/", "").split("/")[0] for k in keys)
print(f"повёрнутых страниц: {len(res)} из {len(keys)} ({100*len(res)/max(len(keys),1):.0f}%)")
print(f"углы: {dict(collections.Counter(res.values()))}")
print("\nпубликации, где повёрнута бо́льшая часть страниц:")
for pub, n in by_pub.most_common(12):
    t = tot_by_pub[pub]
    print(f"   {n:4}/{t:<4} ({100*n/t:3.0f}%)  {pub[:58]}")

# Применяем поворот ТОЛЬКО к публикациям, отсканированным боком целиком.
# Отдельные страницы не трогаем: в журналах широкие рисунки печатают повёрнутыми, и такая
# страница сама по себе нормальная — развернуть её значило бы испортить (проверено глазами).
SHARE = float(os.environ.get("SHARE", 0.6))
pubs = {}
for pub, n in by_pub.items():
    t = tot_by_pub[pub]
    if t >= 3 and n / t >= SHARE:
        angles = collections.Counter(v for k, v in res.items() if k.split("/")[0] == pub)
        pubs[pub] = angles.most_common(1)[0][0]
print(f"\nпубликаций, повёрнутых целиком (≥{SHARE:.0%} страниц): {len(pubs)}")
for p, a in sorted(pubs.items(), key=lambda x: -tot_by_pub[x[0]])[:10]:
    print(f"   {a:4}°  {tot_by_pub[p]:4} стр.  {p[:56]}")

if DRY:
    print("\nсухой прогон — файл не записан"); sys.exit(0)
json.dump({"pages": res, "publications": pubs}, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
print(f"\n-> {OUT.name}: страниц {len(res)}, публикаций целиком {len(pubs)}")
