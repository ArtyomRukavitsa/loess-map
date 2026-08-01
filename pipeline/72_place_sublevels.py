# Двухуровневая структура «сводный разрез ↔ входящие выработки» — прямая просьба геолога (п.3):
# «конкретные выработки, например Site 1, Site 2, OT-22V и OT-23-12, как отдельные точки с
#  собственными координатами; сводный разрез "Отказное" как обобщающий объект, связанный с ними».
#
# Почему их сейчас нет на карте: проверяющий их ПРИНЯЛ, но у них lat/lon = null, а без координат
# объект не рисуется. Проверка исходной статьи показала, что дело не в плохом извлечении:
# «Coordinates of all geological sections are provided in Supplement 1» — координат в файле нет,
# они в отдельном приложении на сайте журнала.
#
# Зато текст даёт положение ОТНОСИТЕЛЬНО известной точки:
#   «Site 1 lies near the northern outskirts of the village of Otkaznoe»
#   «Site 2 is located 4.75 km further south, near the Otkaznenskoe Reservoir»
# Этого достаточно, чтобы поставить точку честно — с большим радиусом неопределённости.
#
# Скрипт: для каждого принятого объекта без координат просит модель прочитать текст публикации и
# определить (а) частью какого объекта он является, (б) от какой точки и куда отсчитывать.
# Координаты считаются арифметикой от родителя, а не выдумываются моделью.
#
#   DRY=1 python 72_place_sublevels.py     — только показать, что получится
#   JOB=20260730-100725-9124ab python 72_place_sublevels.py   — одна загрузка
import os, re, sys, json, math, time, pathlib, collections
import urllib.request, urllib.error
import boto3, fitz
sys.stdout.reconfigure(encoding="utf-8")
HERE = pathlib.Path(__file__).parent
OUT = HERE / "sublevel_objects.json"
TMP = HERE / "_pdf_cache"
DRY = os.environ.get("DRY", "0") == "1"
ONLY = os.environ.get("JOB", "")
MODEL = os.environ.get("SUB_MODEL", "deepseek-v32")

sec = {}
for line in open(HERE / ".secrets", encoding="utf-8"):
    line = line.strip()
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1); sec[k.strip()] = v.strip()
FOLDER, KEY = sec["YANDEX_FOLDER_ID"], sec["YANDEX_API_KEY"]
BUCKET = sec.get("BUCKET", "loess-results")
HDR = {"Authorization": "Api-Key " + KEY, "Content-Type": "application/json", "x-folder-id": FOLDER}
LLM = "https://llm.api.cloud.yandex.net/v1/chat/completions"
s3 = boto3.client("s3", endpoint_url="https://storage.yandexcloud.net", region_name="ru-central1",
                  aws_access_key_id=sec["AWS_ACCESS_KEY_ID"],
                  aws_secret_access_key=sec["AWS_SECRET_ACCESS_KEY"])

REL = ["часть другого объекта", "то же самое место", "самостоятельный объект",
       "опорный разрез из другой публикации", "не определить"]
DIRS = {"север": (1, 0), "юг": (-1, 0), "восток": (0, 1), "запад": (0, -1),
        "северо-восток": (.707, .707), "северо-запад": (.707, -.707),
        "юго-восток": (-.707, .707), "юго-запад": (-.707, -.707), "нет": (0, 0)}
SCHEMA = {"type": "object", "additionalProperties": False,
          "required": ["relation", "parent", "anchor", "offset_km", "direction", "excavations", "why"],
          "properties": {
              "relation": {"type": "string", "enum": REL},
              "parent": {"type": "string"},           # к какому объекту привязан (пустая строка — нет)
              "anchor": {"type": "string"},           # от чего отсчитывать: название места из текста
              "offset_km": {"type": "number"},        # 0, если «у окраины», «в районе»
              "direction": {"type": "string", "enum": list(DIRS)},
              "excavations": {"type": "array", "items": {"type": "string"}},
              "why": {"type": "string"}}}
SYS = (
    "Ты геолог-четвертичник, разбираешь научную статью. Тебе дают объект без координат и выдержки "
    "из текста, где он упоминается, а также объекты ТОЙ ЖЕ статьи, у которых координаты уже есть.\n"
    "Определи:\n"
    "• relation — чем объект приходится остальным. «часть другого объекта» — это площадка или "
    "выработка внутри более крупного разреза. «то же самое место» — другое название того же объекта "
    "(старое имя города, транслитерация). «опорный разрез из другой публикации» — объект, который в "
    "этой статье лишь упомянут для сопоставления, а изучен в чужой работе (обычно рядом стоит ссылка "
    "вида «Автор и др., год»); такие НЕ надо ставить на карту по этой статье.\n"
    "• parent — точное название объекта-родителя из предложенного списка, иначе пустая строка.\n"
    "• anchor — от какого места отсчитывать положение, ТОЛЬКО если это прямо сказано в тексте "
    "(«у северной окраины села X», «в 4.75 км южнее»). Иначе пустая строка.\n"
    "  ВАЖНО: слова «further», «дальше», «южнее», «в стольких-то км» без указания места отсчитываются "
    "от объекта, о котором шла речь В ПРЕДЫДУЩЕМ ПРЕДЛОЖЕНИИ, а не от ближайшего населённого пункта. "
    "«Site 1 у окраины села; Site 2 расположен на 4.75 км южнее» значит, что 4.75 км — это между "
    "Site 1 и Site 2. Тогда anchor = «Site 1», даже если у Site 1 ещё нет координат.\n"
    "• offset_km и direction — расстояние и сторона света от anchor, если названы. Если сказано "
    "«у окраины», «близ», «в районе» — offset_km = 0 и direction = «нет».\n"
    "• excavations — номера конкретных выработок этого объекта (скважины, шурфы, расчистки).\n"
    "Ничего не домысливай: если в тексте нет привязки — anchor пустой, relation «не определить».")


def http(body, tries=5):
    for a in range(tries):
        req = urllib.request.Request(LLM, data=json.dumps(body).encode(), method="POST")
        for k, v in HDR.items(): req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=180) as r: return r.read().decode()
        except Exception:
            if a < tries - 1: time.sleep(2 ** a); continue
            raise


def jobs():
    """Ручные загрузки: у них есть решения проверяющих. Автопоиск (cl-*) пропускаем."""
    seen = set()
    p = s3.get_paginator("list_objects_v2")
    for pg in p.paginate(Bucket=BUCKET, Prefix="uploads/"):
        for o in pg.get("Contents", []):
            j = o["Key"].split("/")[1]
            if not j.startswith("cl-"): seen.add(j)
    return sorted(j for j in seen if not ONLY or j == ONLY)


def load(job, name):
    try:
        return json.loads(s3.get_object(Bucket=BUCKET, Key=f"uploads/{job}/{name}")["Body"].read())
    except Exception:
        return None


def pdf_text(job):
    """Текстовый слой публикации. Кэшируем: файлы по 25 МБ."""
    TMP.mkdir(exist_ok=True)
    f = TMP / f"{job}.pdf"
    if not f.exists():
        try: s3.download_file(BUCKET, f"uploads/{job}/source.pdf", str(f))
        except Exception: return ""
    try:
        d = fitz.open(f)
        return re.sub(r"\s+", " ", "\n".join(p.get_text() for p in d))
    except Exception:
        return ""


def snippets(text, name, n=4, pad=420):
    """Куски текста вокруг упоминаний объекта — модели незачем видеть всю статью."""
    if not text or not name: return []
    out = []
    for m in list(re.finditer(re.escape(name), text, re.I))[:n]:
        out.append(text[max(0, m.start() - pad):m.start() + pad])
    if not out:                                  # имя могло быть транслитерировано
        core = re.sub(r"[^A-Za-zА-Яа-яЁё]", "", name)[:6]
        if len(core) >= 4:
            for m in list(re.finditer(re.escape(core), text, re.I))[:2]:
                out.append(text[max(0, m.start() - pad):m.start() + pad])
    return out


# Внутренние обозначения статьи — не топонимы. Геокодер на «Site 1» бодро отвечает точкой
# в Таджикистане, и объект уезжает за три тысячи километров. Такие имена ищем только среди
# объектов этой же публикации.
LABEL = re.compile(r"^\s*(site|участок|площадк\w*|section|разрез|скв\w*|borehole|pit|шурф)?\s*"
                   r"[-\w]{0,3}[-\s]?\d+[-\w]*\s*$", re.I)


def geocode(name):
    """Опорная точка по названию — только если модель её назвала."""
    if LABEL.match(name or ""): return None
    try:
        u = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
            {"q": name, "format": "json", "limit": 1,
             "countrycodes": "ru,ua,kz,by,md,ge,az,am,uz,kg,tj,tm"})
        r = urllib.request.Request(u, headers={"User-Agent": "loess-sections/1.0 (research)"})
        d = json.load(urllib.request.urlopen(r, timeout=30))
        time.sleep(1.05)
        return [float(d[0]["lat"]), float(d[0]["lon"])] if d else None
    except Exception:
        return None


import urllib.parse, unicodedata


def norm(s):
    """Для сверки имён: «Отка́зное» и «Отказное» — одно и то же (в тексте знак ударения U+0301)."""
    s = unicodedata.normalize("NFD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().lower()


# Модель иногда сама признаётся, что домысливает («вероятно, курган Дуна является частью…»).
# Такие точки не ставим: цена ошибки в координатах выше, чем польза от лишнего маркера.
HEDGE = re.compile(r"вероятн|возможно|скорее всего|по-видимому|предполож|наверное|может быть", re.I)

# «у северной окраины села X» — это положение, а не просто упоминание стороны света. Фраза может
# оказаться и в anchor, и в обосновании модели. Без такого сдвига площадка садится ровно в точку
# села и на карте сливается со сводным разрезом вместо того, чтобы стать отдельной точкой.
SIDES = [(r"северн|northern", (1, 0)), (r"южн|southern", (-1, 0)),
         (r"восточн|eastern", (0, 1)), (r"западн|western", (0, -1))]
EDGE = re.compile(r"окраин|outskirts|краю|edge of", re.I)


def outskirts_dir(text):
    if not EDGE.search(text or ""): return None
    for pat, vec in SIDES:
        if re.search(pat, text, re.I): return vec
    return None


def offset_from(base, r):
    """Координаты = опорная точка + смещение, названное в тексте. Возвращает (lat, lon, радиус)."""
    dy, dx = DIRS.get(r.get("direction"), (0, 0))
    km = float(r.get("offset_km") or 0)
    # «у северной окраины села X» — это не центр села. Сдвигаем на километр в названную сторону:
    # и точнее, и объект не сливается с самим селом в один маркер на карте.
    if km == 0 and (dy or dx) == 0:
        s = outskirts_dir(" ".join([r.get("anchor") or "", r.get("why") or ""]))
        if s: dy, dx, km = s[0], s[1], 1.0
    lat = round(base[0] + dy * km / 111.32, 5)
    lon = round(base[1] + dx * km / (111.32 * math.cos(math.radians(base[0]))), 5)
    # честный радиус: у окраины села ~1.5 км, со смещением — плюс десятая часть расстояния
    return lat, lon, int(1500 + km * 100)


MAX_FAR = float(os.environ.get("MAX_FAR", 150))   # км от района работ публикации


def near_job(lat, lon, job_anchors):
    """Объект статьи не может оказаться за тысячу километров от всего, что в ней же описано.
    Последний рубеж против «Site 1», найденного геокодером в другой стране."""
    if not job_anchors: return True
    for a in job_anchors:
        d = math.hypot((lat - a["lat"]) * 111.32,
                       (lon - a["lon"]) * 111.32 * math.cos(math.radians(lat)))
        if d <= MAX_FAR: return True
    return False


res = json.load(open(OUT, encoding="utf-8")) if OUT.exists() else {}
stat = collections.Counter()
report = []
job_anchors = {}

for job in jobs():
    props = load(job, "proposals.json")
    dec = load(job, "decisions.json") or {}
    if props is None: continue
    items = props if isinstance(props, list) else props.get("proposals", [])
    accepted_no_geo, anchors = [], []
    for k, p in enumerate(items):
        d = dec.get(str(k)) or {}
        v = d.get("verdict") if isinstance(d, dict) else d
        has = p.get("lat") is not None and p.get("lon") is not None
        if has:
            anchors.append(p)
        elif v == "accept":
            accepted_no_geo.append((k, p, (d.get("comment") or "")))
    if not accepted_no_geo: continue
    print(f"\n=== {job}: без координат, но приняты — {len(accepted_no_geo)} ===", flush=True)
    text = pdf_text(job)
    print(f"    текстовый слой: {len(text)} символов" + ("" if text else "  (нет — нужен OCR)"))
    anchor_list = "; ".join(f"{a.get('locality')} ({a['lat']:.4f},{a['lon']:.4f})" for a in anchors) or "нет"

    for k, p, comment in accepted_no_geo:
        name = str(p.get("locality") or "?")
        key = f"{job}#{k}"
        if key in res: stat["уже есть"] += 1; continue
        sn = snippets(text, name)
        user = (f"Объект без координат: {name}\n"
                f"Цитаты, по которым он найден:\n" +
                "\n".join("• " + e[:200] for e in (p.get("evidence") or [])[:8]) +
                (f"\nКомментарий проверяющего-геолога: {comment}\n" if comment else "\n") +
                f"\nОбъекты этой же статьи, у которых координаты известны: {anchor_list}\n"
                f"\nВыдержки из текста статьи вокруг упоминаний объекта:\n" +
                ("\n---\n".join(sn) if sn else "(в тексте статьи упоминаний не найдено)") +
                f"\n\nЧем является «{name}» и как определить его положение?")
        body = {"model": f"gpt://{FOLDER}/{MODEL}/latest", "temperature": 0, "max_tokens": 900,
                "messages": [{"role": "system", "content": SYS}, {"role": "user", "content": user}],
                "response_format": {"type": "json_schema",
                                    "json_schema": {"name": "sub", "strict": True, "schema": SCHEMA}}}
        try:
            c = json.loads(http(body))["choices"][0]["message"]["content"]
            r = json.loads(c)
        except Exception as e:
            print(f"  ! {name}: {str(e)[:70]}"); stat["ошибка"] += 1; continue

        # --- координаты считаем САМИ от опорной точки, модель их не выдумывает ---
        lat = lon = None; acc = None; base = None; hedged = bool(HEDGE.search(r["why"]))
        # Привязку НЕ завязываем на ярлык отношения: модель от прогона к прогону называет один и тот
        # же Site 1 то «частью объекта», то «самостоятельным», и площадка из-за этого теряла место.
        # Не размещаем только опорные разрезы чужих работ — их координаты к этой статье отношения
        # не имеют.
        if r["relation"] != "опорный разрез из другой публикации" and not hedged:
            par = next((a for a in anchors if norm(a.get("locality")) == norm(r["parent"])), None)
            if par: base = [par["lat"], par["lon"]]
        # Модель бывает называет место только в обосновании, оставляя anchor пустым
        # («Site 1 находится у северной окраины села Отказное», anchor = ""). Если в тексте
        # обоснования ровно один известный объект этой статьи — берём его за опору.
        if base is None and not hedged and not r.get("anchor") and not r.get("parent"):
            hits = [a for a in anchors if norm(a.get("locality")) and norm(a["locality"]) in norm(r["why"])]
            if len(hits) == 1:
                base = [hits[0]["lat"], hits[0]["lon"]]
                r["anchor"] = str(hits[0].get("locality"))
        if base is None and r.get("anchor") and not hedged:
            # «северная окраина села Отказное» геокодеру не по зубам — сначала пробуем совпадение
            # с уже известным объектом статьи, и лишь потом идём в Nominatim
            a_n = norm(r["anchor"])
            par = next((a for a in anchors if norm(a.get("locality")) and norm(a["locality"]) in a_n), None)
            base = [par["lat"], par["lon"]] if par else geocode(r["anchor"])
        if hedged:
            stat["отклонено как догадка"] += 1
        if base:
            lat, lon, acc = offset_from(base, r)
            if not near_job(lat, lon, anchors):
                print(f"     отброшено: {lat},{lon} — за {MAX_FAR:.0f} км от района работ статьи")
                lat = lon = acc = None; stat["отброшено как далёкое"] += 1
        res[key] = {"job": job, "i": k, "name": name, "relation": r["relation"],
                    "parent": r["parent"], "anchor": r.get("anchor", ""),
                    "offset_km": r.get("offset_km", 0), "direction": r.get("direction", "нет"),
                    "excavations": r.get("excavations", []), "why": r["why"],
                    "lat": lat, "lon": lon, "accuracy": acc,
                    "thickness": p.get("thickness", []), "evidence": (p.get("evidence") or [])[:8],
                    "pages": p.get("pages", [])}
        job_anchors[job] = anchors
        stat[r["relation"]] += 1
        if lat: stat["поставлено на карту"] += 1
        report.append((name, r["relation"], r["parent"], f"{lat},{lon}" if lat else "—", r["why"][:70]))
        print(f"  {name[:22]:22} {r['relation'][:26]:26} "
              f"{(f'{lat},{lon}' if lat else 'координат нет'):22} {r['why'][:56]}", flush=True)
        if not DRY: json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

# Второй проход: объект привязан к соседу, который сам получил координаты только что
# («Site 2 в 4.75 км южнее Site 1», «скважина OT-22V на участке Site 2»). В первом проходе
# опорной точки ещё не существовало. Повторяем, пока что-то доставляется: цепочка может быть
# длиннее одного звена (село -> Site 1 -> Site 2 -> OT-22V).
for _ in range(4):
    placed = {norm(v["name"]): v for v in res.values() if v.get("lat") is not None}
    moved = 0
    for v in res.values():
        if v.get("lat") is not None: continue
        # Родитель важнее опоры: скважина внутри площадки стоит ровно там же, где площадка.
        # Иначе она считалась бы от того же ориентира отдельно и уезжала от своей же площадки.
        by_parent = placed.get(norm(v.get("parent")))
        by_anchor = None if by_parent else placed.get(norm(v.get("anchor")))
        par = by_parent or by_anchor
        if not par: continue
        # Смещение отсчитывается ОТ ОПОРЫ. Если опора — сам родитель (выработка внутри площадки),
        # его координата уже включает это смещение: «Site 2 в 4.75 км южнее» относится к Site 2,
        # а не к скважине внутри него. Повторное применение уводило точку ещё на 4.75 км.
        same = norm(v.get("anchor")) == norm(v.get("parent")) or not v.get("anchor")
        r2 = dict(v) if (by_anchor and not same) else {**v, "offset_km": 0, "direction": "нет", "anchor": ""}
        lat, lon, acc = offset_from([par["lat"], par["lon"]], r2)
        if not near_job(lat, lon, job_anchors.get(v["job"], [])):
            print(f"  2-й проход: {v['name'][:20]} отброшен — {lat},{lon} вне района работ")
            continue
        v["lat"], v["lon"] = lat, lon
        # если смещения нет, объект внутри родителя: своих координат не имеет, но заведомо рядом
        v["accuracy"] = acc if float(v.get("offset_km") or 0) else max(500, int(par.get("accuracy") or 1500))
        v["inherited_from"] = par["name"]
        moved += 1; stat["привязано ко второму проходу"] += 1
        print(f"  2-й проход: {v['name'][:20]:20} <- {par['name']} ({v['lat']},{v['lon']})")
    if not moved: break

print("\n" + "=" * 70)
for k, n in stat.most_common(): print(f"   {n:4}  {k}")
if DRY:
    print("\nсухой прогон — файл не записан")
else:
    json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n-> {OUT.name}: {len(res)} объектов")
