# Cloud Function: fn_crawl — автопополнение из открытых источников (КиберЛенинка).
# Работает по таймеру, шагами, с курсором в бакете: перечисляет статьи раздела «Науки о Земле»,
# отсеивает по заголовку и аннотации (они есть прямо в списке — качать статью для этого не нужно),
# затем забирает текст подходящих.
#
# ГЛАВНОЕ: свой конвейер не пишем. Текст статьи кладётся туда, куда обычно кладёт распознавание
# (ocrtext/upload/<job>.json, complete=true), и создаётся обычная задача — дальше её подхватывает
# уже развёрнутая fn-process: извлечение, привязка страниц, геокод, интерфейс проверки.
# Распознавание при этом не запускается: текст на КиберЛенинке уже готов.
#
# Вызов: POST {} — сделать шаг; {"reset": true} — начать обход заново; {"status": true} — только отчёт.
# ENV: BUCKET, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
#      LIST_PER_RUN (400), FETCH_PER_RUN (40), DELAY (1.0), TIME_BUDGET (780)
import os, re, json, time, base64, hashlib, urllib.request, urllib.error
import boto3

BUCKET = os.environ.get("BUCKET", "loess-results")
STATE_KEY = "crawl/state.json"
CAND_KEY = "crawl/candidates.json"
UPLOADS = "uploads/"
LIST_PER_RUN = int(os.environ.get("LIST_PER_RUN", 400))
FETCH_PER_RUN = int(os.environ.get("FETCH_PER_RUN", 40))
DELAY = float(os.environ.get("DELAY", 1.0))          # вежливая пауза между запросами к источнику
TIME_BUDGET = int(os.environ.get("TIME_BUDGET", 780))
LAST_PAGE = int(os.environ.get("LAST_PAGE", 3531))
RUBRIC = "https://cyberleninka.ru/article/c/earth-and-related-environmental-sciences"
ART = "https://cyberleninka.ru/article/n/"
UA = "loess-atlas/1.0 (research project, Institute of Geography RAS; danik.khromov@icloud.com)"  # только латиница: заголовки HTTP не принимают кириллицу
CHUNK = 5000                                          # текст режем на куски — извлекатель работает по фрагментам

_t0 = time.time()
def left(): return TIME_BUDGET - (time.time() - _t0)

s3 = boto3.client("s3", endpoint_url="https://storage.yandexcloud.net", region_name="ru-central1",
                  aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
                  aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"])

def s3_get(key, default=None):
    try: return json.loads(s3.get_object(Bucket=BUCKET, Key=key)["Body"].read())
    except Exception: return default

def s3_put(key, obj):
    s3.put_object(Bucket=BUCKET, Key=key, Body=json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                  ContentType="application/json")

def log(*a): print(*a, flush=True)

def http(url, tries=4):
    for a in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and a < tries - 1:
                time.sleep(5 * (a + 1)); continue
            raise
        except Exception:
            if a < tries - 1: time.sleep(3 * (a + 1)); continue
            raise

# ---------------- отсев ----------------
# Двухуровневый, иначе в выборку лезет вся геология: слова «стратиграфия», «литология», «датирование»
# одинаково употребимы и про конодонтов девона, и про лёссы. Наш домен — ЧЕТВЕРТИЧКА.

# СИЛЬНЫЕ признаки — одного достаточно. Покрывают все 15 категорий отложений из нашей схемы.
# Элемент может быть строкой (подстрока) или кортежем — тогда нужны ВСЕ его части где угодно в тексте.
# Кортежи нужны из-за склонений: «погребенные почвы» не содержит подстроки «погребенн почв».
STRONG = [
    "лёсс", "лесс", "лёссов", "лессов", "лёссовидн", "лессовидн",                          # лёссовые
    ("покровн", "суглин"),                                                                 # покровные суглинки
    "палеопочв", "педокомплекс",                                                           # почвы
    ("погребен", "почв"), ("погребён", "почв"), ("ископаем", "почв"), ("древн", "почв"),
    "четвертичн", "плейстоцен", "неоплейстоцен", "эоплейстоцен", "голоцен",                # возраст
    # «антропоген» намеренно НЕ берём: в текстах это почти всегда «антропогенный» (влияние человека),
    # а не четвертичный период — та же ловушка, что «лес» против «лёсс»
    "межледников", "оледенен", "морен", "флювиогляциальн", "водно-ледников", "зандр",      # ледниковые
    ("ледников", "отложен"),
    # названия четвертичных горизонтов — очень точный признак нашей темы
    "лихвинск", "микулинск", "валдайск", "осташковск", "калининск", "мгинск", "одинцовск",
    "роменск", "мезинск", "каргинск", "сартанск", "зырянск", "самаровск", "ширтинск",
    "хвалынск", "хазарск", "бакинск", "апшеронск",                                         # каспийские горизонты
    "криогенн", "мерзлотн", "солифлюкц", "термокарст",                                     # криогенные
    "радиоуглеродн", "люминесцентн", "спорово-пыльцев", "палинологическ",                  # методы четвертички
    ("вулканическ", "пепл"), "тефра",                                                      # вулканические
    ("надпойменн", "террас"), "пролювий", "делювий", ("конус", "выноса"),                  # формы и склоновые
]
# СЛАБЫЕ — сами по себе ничего не значат, но три и более вместе указывают на описание разреза
WEAK = [
    "разрез", "обнажен", "скважин", "шурф", "расчистк", "стратиграф", "литолог",
    "мощност", "датирован", "террас", "аллювий", "аллювиальн", "эолов", "элювий",
    "пойменн", "озерн отложен", "морск отложен", "палеогеограф", "суглин", "супес",
]
# Древние периоды: если они есть, а четвертичных признаков нет — это не наша статья
OLD = ["девон", "силур", "ордовик", "кембр", "юрск", "юры", "мелов", "триас", "пермск",
       "каменноугольн", "карбон", "палеозой", "мезозой", "докембр", "архей", "протерозой",
       "неоген", "миоцен", "олигоцен", "эоцен", "палеоцен"]
NEG = [   # омонимы и заведомо чужие темы
    "loess regression", "локально взвешен", "сглаживани данных",
    "месторожден руд", "нефтегазон", "буровой раствор", "гидроразрыв", "боксит",
    "урожайност", "агротехник", "внесение удобрен", "конодонт",
]

def norm(s):
    return re.sub(r"[^0-9a-zа-яё]+", " ", str(s or "").lower().replace("ё", "е")).strip()

def _prep(items):
    out = []
    for p in items:
        out.append(tuple(norm(x) for x in p) if isinstance(p, tuple) else (norm(p),))
    return out

_STRONG = _prep(STRONG)
_WEAK = _prep(WEAK)
_OLD = _prep(OLD)
_NEG = _prep(NEG)

def _has(pat, t):
    return all(part in t for part in pat)      # для кортежа — все части, для строки — она сама

def relevant(title, abstract):
    t = norm(title + " " + abstract)
    if any(_has(p, t) for p in _NEG): return False
    strong = sum(1 for p in _STRONG if _has(p, t))
    if any(_has(o, t) for o in _OLD) and not strong: return False   # чужой период без признаков четвертички
    if strong: return True
    return sum(1 for p in _WEAK if _has(p, t)) >= 3

# ---------------- разбор страниц ----------------
RE_ITEM = re.compile(
    r'<a href="/article/n/([a-z0-9\-]+)">\s*<div class="title">(.*?)</div>\s*<p>(.*?)</p>\s*<span>(.*?)</span>',
    re.S)

def strip_tags(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()

def parse_list(html):
    out = []
    for slug, title, abstract, meta in RE_ITEM.findall(html):
        out.append({"slug": slug, "title": strip_tags(title),
                    "abstract": strip_tags(abstract), "meta": strip_tags(meta)})
    return out

def meta_tag(html, name):
    m = re.findall(r'<meta name="' + name + r'" content="([^"]*)"', html)
    return m

def parse_article(html):
    body = ""
    parts = re.split(r'itemprop="articleBody"', html, maxsplit=1)
    if len(parts) > 1:
        body = strip_tags(parts[1][:600000])
    return {
        "title": (meta_tag(html, "citation_title") or [""])[0],
        "authors": meta_tag(html, "citation_author"),
        "year": (meta_tag(html, "citation_publication_date") or [""])[0],
        "journal": (meta_tag(html, "citation_journal_title") or [""])[0],
        "issn": (meta_tag(html, "citation_issn") or [""])[0],
        "pdf": (meta_tag(html, "citation_pdf_url") or [""])[0],
        "text": body,
    }

# ---------------- создание задачи для существующего конвейера ----------------
def make_job(cand, art):
    jid = "cl-" + hashlib.md5(cand["slug"].encode("utf-8")).hexdigest()[:12]
    if s3_get(f"{UPLOADS}{jid}/status.json"):        # уже заводили — не дублируем
        return None
    title = art["title"] or cand["title"]
    fname = re.sub(r'[\\/:*?"<>|]', "_", title)[:120] + ".pdf"
    text = art["text"]
    pages = [{"idx": i, "text": text[p:p + CHUNK]}
             for i, p in enumerate(range(0, len(text), CHUNK))]
    pages = [p for p in pages if len(p["text"].strip()) > 200]
    if not pages: return None
    # текст кладём туда, куда обычно пишет распознавание -> fn-process не станет запускать OCR
    s3_put(f"ocrtext/upload/{jid}.json",
           {"path": f"upload/{jid}/{fname}", "done_upto": len(pages), "complete": True,
            "n_pages": len(pages), "n_kept": len(pages), "pages": pages})
    s3_put(f"{UPLOADS}{jid}/status.json",
           {"job_id": jid, "filename": fname, "status": "queued", "created": int(time.time()),
            "msg": "найдено автоматически, ожидает обработки", "n_proposals": 0,
            "user": "автопоиск", "source": "cyberleninka",
            "url": "https://cyberleninka.ru/article/n/" + cand["slug"],
            "journal": art["journal"], "year": art["year"], "authors": "; ".join(art["authors"])[:200]})
    return jid

# ---------------- шаг обхода ----------------
def step(state, cand):
    stats = state.setdefault("stats", {"listed": 0, "matched": 0, "fetched": 0, "jobs": 0, "empty": 0})
    page = state.get("page", 1)

    # Фаза 1 — перечисление: заголовок и аннотация есть прямо в списке, поэтому отсев бесплатный
    listed = 0
    while page <= LAST_PAGE and listed < LIST_PER_RUN and left() > 90:
        try:
            items = parse_list(http(f"{RUBRIC}/{page}"))
        except Exception as e:
            log(f"страница списка {page}: {str(e)[:80]}"); break
        if not items:
            page = LAST_PAGE + 1; break
        stats["listed"] += len(items)
        for it in items:
            if relevant(it["title"], it["abstract"]):
                cand.append({"slug": it["slug"], "title": it["title"]})
                stats["matched"] += 1
        page += 1; listed += 1
        time.sleep(DELAY)
    state["page"] = page

    # Фаза 2 — забор текста только у отобранных
    done = state.get("fetched_idx", 0)
    n = 0
    while done < len(cand) and n < FETCH_PER_RUN and left() > 60:
        c = cand[done]
        try:
            art = parse_article(http(ART + c["slug"]))
            if art["text"] and len(art["text"]) > 800:
                jid = make_job(c, art)
                if jid: stats["jobs"] += 1
            else:
                stats["empty"] += 1
            stats["fetched"] += 1
        except Exception as e:
            log(f"статья {c['slug']}: {str(e)[:80]}")
        done += 1; n += 1
        time.sleep(DELAY)
    state["fetched_idx"] = done
    return state, cand

def handler(event, context):
    body = {}
    raw = (event or {}).get("body")
    if raw:
        try: body = json.loads(base64.b64decode(raw).decode() if event.get("isBase64Encoded") else raw)
        except Exception: body = {}

    state = s3_get(STATE_KEY, {}) or {}
    cand = s3_get(CAND_KEY, []) or []

    if body.get("reset"):
        state, cand = {}, []
        s3_put(STATE_KEY, state); s3_put(CAND_KEY, cand)

    if body.get("status"):
        return {"statusCode": 200, "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"page": state.get("page", 1), "last_page": LAST_PAGE,
                                    "candidates": len(cand), "fetched": state.get("fetched_idx", 0),
                                    "stats": state.get("stats", {})}, ensure_ascii=False)}

    state, cand = step(state, cand)
    state["updated"] = int(time.time())
    s3_put(STATE_KEY, state); s3_put(CAND_KEY, cand)

    st = state.get("stats", {})
    done = state.get("page", 1) > LAST_PAGE and state.get("fetched_idx", 0) >= len(cand)
    log(f"страниц просмотрено до {state.get('page')} | кандидатов {len(cand)} | "
        f"забрано {state.get('fetched_idx', 0)} | задач {st.get('jobs', 0)}")
    return {"statusCode": 200, "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"page": state.get("page"), "last_page": LAST_PAGE,
                                "candidates": len(cand), "fetched": state.get("fetched_idx", 0),
                                "stats": st, "done": done}, ensure_ascii=False)}
