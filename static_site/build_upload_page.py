# Сборка страницы загрузки публикаций (upload.html) — рядом с картой в том же бакете.
# URL функции подставляется при сборке:  INGEST_API="https://functions.yandexcloud.net/<id>" python build_upload_page.py
import os

HERE = os.path.dirname(os.path.abspath(__file__))
INGEST_API = os.environ.get("INGEST_API", "")
PUBLISH_API = os.environ.get("PUBLISH_API", "")     # функция публикации карты (кнопка ниже)
PROCESS_API = os.environ.get("PROCESS_API", "")     # функция распознавания и извлечения

HTML = r"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Загрузка публикаций — Цифровой атлас геологических разрезов и палеоархивов</title>
<style>
  :root { --bg:#f5f6f8; --card:#fff; --line:#e2e5ea; --ink:#1d2530; --sub:#6b7688;
          --accent:#2f6f4f; --warn:#a8611a; --bad:#a33; --ok:#2f6f4f; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--ink);
         font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif; }
  header { background:#233; color:#fff; padding:14px 20px; }
  header h1 { margin:0; font-size:17px; font-weight:600; }
  header .sub { color:#b9c4cf; font-size:12px; margin-top:3px; }
  header a { color:#9ec9ff; }
  main { max-width:1000px; margin:0 auto; padding:18px 16px 60px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:16px; margin-bottom:16px; }
  h2 { font-size:15px; margin:0 0 10px; }
  .drop { border:2px dashed #c3cbd6; border-radius:8px; padding:26px; text-align:center; color:var(--sub);
          cursor:pointer; transition:.15s; }
  .drop.hot { border-color:var(--accent); background:#f0f7f3; color:var(--ink); }
  button { font:inherit; border:1px solid var(--line); background:#fff; border-radius:6px;
           padding:6px 12px; cursor:pointer; }
  button:hover { background:#f2f4f7; }
  button.primary { background:var(--accent); color:#fff; border-color:var(--accent); }
  button.primary:disabled { opacity:.5; cursor:default; }
  button.ok { border-color:var(--ok); color:var(--ok); }
  button.bad { border-color:var(--bad); color:var(--bad); }
  input[type=text] { font:inherit; padding:6px 9px; border:1px solid var(--line); border-radius:6px; width:100%; }
  table { width:100%; border-collapse:collapse; }
  th, td { text-align:left; padding:7px 8px; border-bottom:1px solid var(--line); vertical-align:top; }
  th { color:var(--sub); font-weight:600; font-size:12px; text-transform:uppercase; letter-spacing:.03em; }
  .badge { display:inline-block; padding:2px 8px; border-radius:99px; font-size:12px; }
  .b-queued { background:#eef1f5; color:#57606f; }
  .b-processing { background:#fff3e0; color:var(--warn); }
  .b-ready { background:#e8f4ec; color:var(--ok); }
  .b-error { background:#fdecec; color:var(--bad); }
  .sub { color:var(--sub); font-size:12px; }
  .prop { border:1px solid var(--line); border-radius:8px; padding:12px; margin-bottom:10px; }
  .prop.accepted { border-color:var(--ok); background:#f6fbf8; }
  .prop.rejected { border-color:var(--bad); background:#fdf6f6; opacity:.75; }
  .prop h3 { margin:0 0 3px; font-size:15px; }
  .grid { display:grid; grid-template-columns:150px 1fr; gap:3px 12px; margin:8px 0; font-size:13px; }
  .grid .k { color:var(--sub); }
  .ev { background:#fafbfc; border-left:3px solid #dfe3e8; padding:6px 10px; margin-top:8px;
        font-size:12px; color:#4a5462; }
  .row { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
  .hide { display:none; }
  .note { background:#fff8e6; border:1px solid #f0e0b8; border-radius:6px; padding:10px; font-size:13px; }
</style>
</head>
<body>
<header>
  <h1>Загрузка публикаций</h1>
  <div class="sub">Цифровой атлас геологических разрезов и палеоархивов · <a href="index.html">← к карте</a></div>
</header>
<main>

  <div class="card">
    <h2>1. Загрузить публикацию</h2>
    <div class="row" style="margin-bottom:10px">
      <span class="sub">Проверяющий:</span>
      <input type="text" id="user" placeholder="Фамилия" style="max-width:220px">
    </div>
    <div class="drop" id="drop">
      <div style="font-size:17px;font-weight:600;color:var(--ink);margin-bottom:6px">
        Перетащите PDF или изображение сюда
      </div>
      <button class="primary" style="font-size:15px;padding:10px 22px;margin:4px 0 8px"
              onclick="event.stopPropagation();document.getElementById('file').click()">Выбрать файл</button>
      <div class="sub">Система распознает текст, найдёт разрезы и предложит их для проверки</div>
    </div>
    <input type="file" id="file" class="hide" accept=".pdf,.jpg,.jpeg,.png,.tif,.tiff">
    <div id="upmsg" class="sub" style="margin-top:10px"></div>
  </div>

  <div class="card">
    <h2>2. Обработка</h2>
    <div class="row" style="margin-bottom:10px">
      <button id="procBtn" onclick="runProcess()">Запустить обработку</button>
      <span class="sub" id="procMsg">Файлы из очереди распознаются и разбираются автоматически;
        кнопка запускает это немедленно.</span>
    </div>
    <div id="jobs"><span class="sub">Загрузка…</span></div>
  </div>

  <div class="card hide" id="review">
    <h2>3. Проверка найденных объектов</h2>
    <div class="sub" id="revhead"></div>
    <div class="note" style="margin:10px 0">
      Отметьте, какие объекты корректны. Принятые попадут на карту при следующем обновлении,
      отклонённые сохранятся как обучающие примеры ошибок.
    </div>
    <div id="props"></div>
  </div>

  <div class="card">
    <h2>4. Публикация на карту</h2>
    <div class="sub" style="margin-bottom:10px">
      Принятые объекты попадают на карту только после публикации. Карта пересобирается в облаке
      и обновляется для всех — поэтому шаг сделан отдельным и осознанным.
    </div>
    <div class="row">
      <button class="primary" id="pubBtn" onclick="publish(false)">Опубликовать принятые</button>
      <button onclick="publish(true)">Проверить без публикации</button>
      <span class="sub" id="pubMsg"></span>
    </div>
  </div>

</main>
<script>
const API = "__INGEST_API__";
const $ = s => document.querySelector(s);
const esc = s => String(s == null ? "" : s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
let openJob = null, timer = null;

$("#user").value = localStorage.getItem("atlas_user") || "";
$("#user").onchange = () => localStorage.setItem("atlas_user", $("#user").value);

/* ---------- загрузка файла: ссылка от функции -> заливка прямо в хранилище ---------- */
$("#drop").onclick = () => $("#file").click();
$("#drop").ondragover = e => { e.preventDefault(); $("#drop").classList.add("hot"); };
$("#drop").ondragleave = () => $("#drop").classList.remove("hot");
$("#drop").ondrop = e => { e.preventDefault(); $("#drop").classList.remove("hot");
                           if (e.dataTransfer.files[0]) upload(e.dataTransfer.files[0]); };
$("#file").onchange = e => { if (e.target.files[0]) upload(e.target.files[0]); };

let uploading = false;

/* Отправляем через XMLHttpRequest, а не fetch: он умеет сообщать прогресс.
   Для файла в десятки мегабайт это принципиально — иначе страница выглядит зависшей. */
function putWithProgress(url, file, ctype, onPct) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", url);
    xhr.setRequestHeader("Content-Type", ctype);
    xhr.upload.onprogress = e => { if (e.lengthComputable) onPct(e.loaded / e.total); };
    xhr.onload = () => (xhr.status >= 200 && xhr.status < 300)
      ? resolve() : reject(new Error("хранилище отклонило файл (" + xhr.status + ")"));
    xhr.onerror = () => reject(new Error("обрыв соединения при отправке"));
    xhr.ontimeout = () => reject(new Error("превышено время отправки"));
    xhr.timeout = 30 * 60 * 1000;
    xhr.send(file);
  });
}

async function upload(file) {
  if (!API) { $("#upmsg").textContent = "Сервис загрузки ещё не подключён (не задан адрес функции)."; return; }
  const msg = $("#upmsg");
  if (uploading) { msg.textContent = "Дождитесь окончания текущей отправки."; return; }
  const mb = (file.size / 1048576).toFixed(1);
  uploading = true; $("#drop").style.opacity = .5;
  try {
    msg.textContent = `Готовлю загрузку «${file.name}» (${mb} МБ)…`;
    const r = await fetch(API, { method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({ action:"create", filename:file.name, user:$("#user").value || "anon" }) });
    const d = await r.json();
    if (d.error) throw new Error(d.error);
    await putWithProgress(d.upload_url, file, d.content_type || "application/octet-stream",
      p => { msg.textContent = `Отправка: ${Math.round(p * 100)} % из ${mb} МБ` +
                               (p >= 1 ? " — обрабатываю…" : ""); });
    await fetch(API, { method:"POST", headers:{"Content-Type":"application/json"},
                       body: JSON.stringify({ action:"enqueue", job_id:d.job_id }) });
    msg.textContent = "Файл принят и поставлен в очередь на обработку.";
    loadJobs();
  } catch (e) {
    msg.textContent = "Не удалось загрузить: " + e.message;
  } finally {
    uploading = false; $("#drop").style.opacity = 1;
  }
}

/* ---------- список задач ---------- */
async function loadJobs() {
  if (!API) { $("#jobs").innerHTML = '<span class="sub">Сервис загрузки ещё не подключён.</span>'; return; }
  try {
    const d = await (await fetch(API + "?action=jobs")).json();
    // статус created = заготовка, файл до хранилища не доехал; в списке это только мусор
    const jobs = (d.jobs || []).filter(j => j.status !== "created");
    if (!jobs.length) { $("#jobs").innerHTML = '<span class="sub">Пока ничего не загружено.</span>'; return; }
    $("#jobs").innerHTML = `<table><tr><th>Публикация</th><th>Состояние</th><th>Найдено</th><th></th></tr>` +
      jobs.map(j => `<tr>
        <td>${esc(j.filename)}<div class="sub">${esc(j.user||"")}</div></td>
        <td><span class="badge b-${esc(j.status)}">${({queued:"в очереди",processing:"обработка",ready:"готово",error:"ошибка",created:"ожидает файл"})[j.status]||esc(j.status)}</span>
            <div class="sub">${esc(j.msg||"")}</div></td>
        <td>${j.n_proposals || 0}</td>
        <td>${j.status === "ready" && j.n_proposals ? `<button onclick="openReview('${esc(j.job_id)}')">Проверить</button>` : ""}</td>
      </tr>`).join("") + `</table>`;
    const busy = jobs.some(j => j.status === "processing" || j.status === "queued");
    clearTimeout(timer);
    if (busy) timer = setTimeout(loadJobs, 10000);           // пока что-то считается — обновляем сами
  } catch (e) {
    $("#jobs").innerHTML = '<span class="sub">Не удалось получить список: ' + esc(e.message) + '</span>';
  }
}

/* ---------- проверка предложений ---------- */
async function openReview(jid) {
  openJob = jid;
  const d = await (await fetch(API + "?action=job&job_id=" + encodeURIComponent(jid))).json();
  const props = d.proposals || [], dec = d.decisions || {};
  $("#review").classList.remove("hide");
  $("#revhead").textContent = `${d.status?.filename || jid} — найдено объектов: ${props.length}`;
  $("#props").innerHTML = props.map((p, i) => {
    const v = (dec[String(i)] || {}).verdict;
    const line = (k, val) => val && val.length ? `<div class="k">${k}</div><div>${esc(Array.isArray(val)?val.join("; "):val)}</div>` : "";
    return `<div class="prop ${v === "accept" ? "accepted" : v === "reject" ? "rejected" : ""}" id="p${i}">
      <h3>${esc(p.locality)}</h3>
      <div class="sub">${esc(p.admin)} ${p.lat ? `· ${p.lat.toFixed(4)}, ${p.lon.toFixed(4)}` : "· координаты не определены"}
           · упоминаний: ${p.n_records}${p.pages && p.pages.length ? " · стр. " + p.pages.join(", ") : ""}
           ${p.lat ? `· <a href="#" onclick="toggleMap(${i},${p.lat},${p.lon},event)">показать на карте</a>` : ""}</div>
      <div id="map${i}" class="hide" style="margin:8px 0"></div>
      <div class="grid">
        ${line("Тип вскрытия", p.excavation)}
        ${line("Геоморфология", p.geomorph)}
        ${line("Отложения", p.deposits)}
        ${line("Термины источника", p.raw_terms)}
        ${line("Мощность", p.thickness)}
        ${line("Высота, м", p.elevation !== "ND" ? p.elevation : null)}
        ${line("Стратиграфия", p.strat)}
        ${line("Датирование", p.dating)}
      </div>
      ${(p.evidence||[]).length ? `<div class="ev">${p.evidence.map(e => "• " + esc(e)).join("<br>")}</div>` : ""}
      <div class="row" style="margin-top:10px">
        <button class="ok" onclick="decide(${i},'accept')">✓ Принять</button>
        <button class="bad" onclick="decide(${i},'reject')">✗ Отклонить</button>
        <input type="text" id="c${i}" placeholder="комментарий (необязательно)" style="flex:1;min-width:180px">
        <span class="sub" id="s${i}">${v ? (v === "accept" ? "принято" : "отклонено") : ""}</span>
      </div>
    </div>`;
  }).join("");
  $("#review").scrollIntoView({ behavior:"smooth" });
}

async function decide(i, verdict) {
  const el = document.getElementById("p" + i);
  try {
    await fetch(API, { method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({ action:"decide", job_id:openJob, i, verdict,
                             comment: document.getElementById("c" + i).value,
                             user: $("#user").value || "anon" }) });
    el.classList.remove("accepted", "rejected");
    el.classList.add(verdict === "accept" ? "accepted" : "rejected");
    document.getElementById("s" + i).textContent = verdict === "accept" ? "принято" : "отклонено";
  } catch (e) {
    document.getElementById("s" + i).textContent = "не сохранилось";
  }
}

/* ---------- мини-карта: глазами проверить, туда ли встала точка ---------- */
function toggleMap(i, lat, lon, ev) {
  if (ev) ev.preventDefault();
  const el = document.getElementById("map" + i);
  if (!el.classList.contains("hide")) { el.classList.add("hide"); el.innerHTML = ""; return; }
  const dx = 0.09, dy = 0.05;
  const bbox = [lon - dx, lat - dy, lon + dx, lat + dy].join(",");
  el.innerHTML =
    `<iframe width="100%" height="240" frameborder="0" loading="lazy"
       style="border:1px solid var(--line);border-radius:6px"
       src="https://www.openstreetmap.org/export/embed.html?bbox=${bbox}&layer=mapnik&marker=${lat},${lon}"></iframe>
     <div class="sub" style="margin-top:3px">
       <a href="https://www.openstreetmap.org/?mlat=${lat}&mlon=${lon}#map=10/${lat}/${lon}"
          target="_blank" rel="noopener">открыть карту в новой вкладке</a></div>`;
  el.classList.remove("hide");
}

/* ---------- запуск распознавания и извлечения ---------- */
const PROCESS_API = "__PROCESS_API__";
function runProcess() {
  const msg = $("#procMsg"), btn = $("#procBtn");
  if (!PROCESS_API) { msg.textContent = "Сервис обработки ещё не подключён."; return; }
  btn.disabled = true;
  msg.textContent = "Обработка запущена. Занимает от полуминуты до нескольких минут — состояние обновляется само, страницу можно не держать открытой.";
  // ответа НЕ ждём: он придёт лишь по завершении, а статусы меняются уже по ходу
  fetch(PROCESS_API, { method:"POST", headers:{"Content-Type":"application/json"}, body:"{}" })
    .then(r => r.json())
    .then(d => {
      const n = (d.processed || []).length;
      msg.textContent = n ? `Обработано публикаций: ${n}. Ниже можно нажать «Проверить».`
                          : "В очереди нечего обрабатывать.";
      loadJobs();
    })
    .catch(e => { msg.textContent = "Не удалось запустить: " + e.message; })
    .finally(() => { btn.disabled = false; });
  setTimeout(loadJobs, 4000);
}

/* ---------- публикация принятых объектов на карту ---------- */
const PUBLISH_API = "__PUBLISH_API__";
async function publish(dry) {
  const msg = $("#pubMsg"), btn = $("#pubBtn");
  if (!PUBLISH_API) { msg.textContent = "Сервис публикации ещё не подключён."; return; }
  btn.disabled = true;
  msg.textContent = dry ? "Считаю, что изменится…" : "Публикую, это занимает около минуты…";
  try {
    const r = await fetch(PUBLISH_API, { method:"POST", headers:{"Content-Type":"application/json"},
                                         body: JSON.stringify(dry ? { dry:true } : {}) });
    const d = await r.json();
    if (d.msg) { msg.textContent = d.msg; }
    else if (dry) {
      msg.textContent = `К публикации: ${d.added} объектов, на карте станет ${d.markers}. Карта не изменена.`;
    } else if (d.published) {
      msg.innerHTML = `Опубликовано: добавлено ${d.added}, объектов на карте ${d.markers} (${d.sec} с). ` +
                      `<a href="index.html">открыть карту</a>`;
    } else {
      msg.textContent = "Публикация остановлена проверкой целостности — данные не изменены.";
    }
  } catch (e) {
    msg.textContent = "Не удалось опубликовать: " + e.message;
  }
  btn.disabled = false;
}

loadJobs();
</script>
</body>
</html>
"""

html = (HTML.replace("__INGEST_API__", INGEST_API)
            .replace("__PUBLISH_API__", PUBLISH_API)
            .replace("__PROCESS_API__", PROCESS_API))
out = os.path.join(HERE, "upload.html")
open(out, "w", encoding="utf-8").write(html)
print(f"upload.html собран ({len(html)/1024:.0f} КБ) | загрузка: {'подключена' if INGEST_API else 'НЕ задана'}"
      f" | обработка: {'подключена' if PROCESS_API else 'НЕ задана'}"
      f" | публикация: {'подключена' if PUBLISH_API else 'НЕ задана'}")
