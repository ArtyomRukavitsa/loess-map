# Публикация проверенных объектов: слияние принятых -> досчёт -> пересборка карты (-> при желании деплой).
# Одна команда вместо шести, с защитой от одновременных запусков и проверкой результата перед публикацией.
# Кнопка «Принять» в интерфейсе проверки И ЕСТЬ человеческий контроль, поэтому дальше всё автоматизируемо.
#
#   python 61_publish.py            # слить, досчитать, пересобрать карту (БЕЗ публикации)
#   DEPLOY=1 python 61_publish.py   # то же + выложить сайт
#   FORCE=1 python 61_publish.py    # пересобрать, даже если новых принятых нет
import os, re, sys, json, time, subprocess, pathlib
sys.stdout.reconfigure(encoding="utf-8")
HERE = pathlib.Path(__file__).parent
SITE = HERE.parent / "loess_static_site"
INDEX = SITE / "index.html"
LOCK = HERE / ".publish.lock"
DEPLOY = os.environ.get("DEPLOY", "0") == "1"
FORCE = os.environ.get("FORCE", "0") == "1"
VERIFY_API = os.environ.get("VERIFY_API", "https://functions.yandexcloud.net/d4evhi01uh5faspidt74")
MIN_KEEP = 0.9            # если маркеров вдруг стало меньше 90% от прежнего — что-то сломалось, не публикуем

def markers_in_index():
    if not INDEX.exists(): return None
    try:
        h = INDEX.read_text(encoding="utf-8")
        m = re.search(r"const DATA = (\[.*?\]);\n", h, re.S)
        return len(json.loads(m.group(1))) if m else None
    except Exception:
        return None

def run(title, script, cwd=HERE, env=None, tail=3):
    e = dict(os.environ); e["PYTHONIOENCODING"] = "utf-8"
    if env: e.update(env)
    print(f"\n=== {title} ===", flush=True)
    t0 = time.time()
    p = subprocess.run([sys.executable, "-u", script], cwd=str(cwd), env=e,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = [l for l in (p.stdout or "").splitlines() if l.strip()]
    for l in out[-tail:]: print("   ", l, flush=True)
    if p.returncode != 0:
        print("    ОШИБКА:", (p.stderr or "")[-400:], flush=True)
        raise RuntimeError(f"{script} завершился с кодом {p.returncode}")
    print(f"    ({time.time() - t0:.0f} с)", flush=True)
    return "\n".join(out)

# --- защита от одновременных запусков (ВМ + ноутбук не должны писать таблицу вдвоём) ---
if LOCK.exists():
    age = time.time() - LOCK.stat().st_mtime
    if age < 3600:
        print(f"Публикация уже идёт (блокировка {age/60:.0f} мин назад). Если это ошибка — удали {LOCK.name}")
        sys.exit(1)
    print("Старая блокировка снята (>1 ч)")
LOCK.write_text(str(os.getpid()), encoding="utf-8")

try:
    before = markers_in_index()
    print(f"маркеров на карте сейчас: {before}")

    # 1) принятые проверяющим объекты -> в таблицу карты
    merged = run("слияние принятых объектов", "58_merge_approved.py")
    added = 0
    m = re.search(r"дописано строк:\s*(\d+)", merged)
    if m: added = int(m.group(1))
    if "новых принятых объектов нет" in merged and not FORCE:
        print("\nНовых принятых объектов нет — публиковать нечего.")
        sys.exit(0)
    print(f"    добавлено строк: {added}")

    # 2) досчёт по новым координатам (кэши: считается только то, чего ещё нет)
    env_src = {"SRC": "records_clean_geo_v2.xlsx"}
    run("высоты по рельефу", "50_dem_elevation.py", env=env_src)
    run("радиусы точности", "51_accuracy_radius.py", env=env_src)

    # 3) привязка фраз к страницам и рендер недостающих сканов (включая загруженные публикации)
    run("привязка страниц", "56_page_link_v2.py")
    run("рендер сканов", "60_render_scans.py")

    # 4) пересборка карты
    run("сборка карты", "build_customer_map_v2.py", cwd=SITE, env={"VERIFY_API": VERIFY_API})

    # 5) проверка: карта не должна внезапно похудеть
    after = markers_in_index()
    print(f"\nмаркеров стало: {after} (было {before})")
    if before and after and after < before * MIN_KEEP:
        print(f"СТОП: маркеров стало меньше {MIN_KEEP:.0%} от прежнего — публикацию не делаю, проверь данные.")
        sys.exit(2)

    # 6) публикация — только по явному разрешению
    if DEPLOY:
        run("публикация сайта", "deploy_site.py", cwd=SITE, tail=6)
        print("\nГОТОВО: карта обновлена и опубликована.")
    else:
        print("\nГОТОВО: карта пересобрана ЛОКАЛЬНО (не опубликована).")
        print("Публикация: DEPLOY=1 python 61_publish.py")
finally:
    LOCK.unlink(missing_ok=True)
