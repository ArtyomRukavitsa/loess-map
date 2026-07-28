# Собирает самодостаточный index.html (Leaflet + встроенные данные) из results.csv.
# Клиентская карта: точки, клик->карточка, слайдер надёжности, фильтр по региону. Выход: index.html
import csv, json, os, sys

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "loess_map_app", "results.csv")


def num(v):
    try:
        return float(v)
    except Exception:
        return None


def nd(v):
    s = "" if v is None else str(v).strip()
    return "ND" if s.lower() in ("", "none", "nan", "nd") else s


rows = []
with open(SRC, encoding="utf-8-sig") as f:
    for r in csv.DictReader(f):
        lat, lon = num(r.get("N")), num(r.get("E"))
        if lat is None or lon is None:
            continue
        rows.append({
            "id": r.get("ID", ""),
            "lat": round(lat, 5), "lon": round(lon, 5),
            "f": nd(r.get("Name of geographic feature")),
            "l": nd(r.get("Nearest locality")),
            "r": nd(r.get("Administrative unit")),
            "th": nd(r.get("Thickness, m")),
            "el": nd(r.get("Absolute elevation, m a.s.l.")),
            "dp": nd(r.get("Type of deposits")),
            "st": nd(r.get("Stratigraphic position")),
            "ch": nd(r.get("Chronological data available")),
            "dm": nd(r.get("Dating method")),
            "pb": nd(r.get("Publication 1")),
            "ns": int(num(r.get("n_sources")) or 1),
        })

data_json = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
maxsrc = max((d["ns"] for d in rows), default=2)

HTML = r"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>База данных лёссовых и четвертичных разрезов</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  * { box-sizing: border-box; }
  html, body { margin: 0; height: 100%; font-family: -apple-system, "Segoe UI", Roboto, sans-serif; color: #1a2233; }
  #app { display: flex; flex-direction: column; height: 100%; }
  header { background: #1f3864; color: #fff; padding: 10px 16px; }
  header h1 { margin: 0; font-size: 17px; font-weight: 600; }
  header p { margin: 2px 0 0; font-size: 12px; opacity: .8; }
  #controls { display: flex; flex-wrap: wrap; gap: 16px; align-items: center;
              padding: 8px 16px; background: #eef1f6; border-bottom: 1px solid #d7dce6; font-size: 13px; }
  #controls label { font-weight: 600; margin-right: 6px; }
  #controls select, #controls input { vertical-align: middle; }
  #stat { margin-left: auto; color: #55617a; }
  #main { flex: 1; display: flex; min-height: 0; }
  #map { flex: 1; }
  #panel { width: 340px; background: #fff; border-left: 1px solid #d7dce6; padding: 14px 16px; overflow-y: auto; }
  #panel h2 { margin: 0 0 10px; font-size: 16px; color: #1f3864; }
  #panel table { border-collapse: collapse; font-size: 13px; width: 100%; }
  #panel td { padding: 3px 8px 3px 0; vertical-align: top; }
  #panel td.k { color: #6b7690; white-space: nowrap; }
  #panel td.v { font-weight: 600; }
  .hint { color: #8790a3; font-size: 13px; }
  .pub { margin-top: 12px; font-size: 12px; color: #444; }
  @media (max-width: 720px) { #main { flex-direction: column; } #panel { width: auto; max-height: 45%; } }
</style>
</head>
<body>
<div id="app">
  <header>
    <h1>База данных лёссовых и четвертичных разрезов</h1>
    <p>Институт географии РАН · автоматическое извлечение из научных публикаций</p>
  </header>
  <div id="controls">
    <span><label>Мин. источников</label><input type="range" id="src" min="1" max="__MAXSRC__" value="1">
      <b id="srcv">1</b></span>
    <span><label>Регион</label><select id="reg"><option value="all">все</option></select></span>
    <span id="stat"></span>
  </div>
  <div id="main">
    <div id="map"></div>
    <div id="panel"><p class="hint">Кликните точку на карте — здесь появится карточка разреза.</p></div>
  </div>
</div>
<script>
const DATA = __DATA__;
const PALETTE = [[31,119,180],[255,127,14],[44,160,44],[214,39,40],[148,103,189],[140,86,75],
  [227,119,194],[127,127,127],[188,189,34],[23,190,207],[174,199,232],[255,187,120]];
const regions = [...new Set(DATA.map(d=>d.r).filter(r=>r && r!=="ND"))].sort();
const regColor = {}; regions.forEach((r,i)=>regColor[r]="rgb("+PALETTE[i%PALETTE.length].join(",")+")");
const regSel = document.getElementById("reg");
regions.forEach(r=>{ const o=document.createElement("option"); o.value=r; o.textContent=r; regSel.appendChild(o); });

const map = L.map("map",{preferCanvas:true}).setView([55,55],3);
L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
  {attribution:'&copy; OpenStreetMap, &copy; CARTO', maxZoom:19, subdomains:"abcd"}).addTo(map);
const layer = L.layerGroup().addTo(map);
const srcInp = document.getElementById("src"), srcVal = document.getElementById("srcv"),
      stat = document.getElementById("stat"), panel = document.getElementById("panel");

function esc(s){ return String(s).replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c])); }
function showCard(d){
  const rows = [["Координаты", d.lat.toFixed(5)+", "+d.lon.toFixed(5)],
    ["Источников", d.ns+" · ID "+d.id], ["Мощность, м", d.th], ["Высота, м", d.el],
    ["Нас. пункт", d.l], ["Регион", d.r], ["Тип отложений", d.dp],
    ["Стратиграфия", d.st], ["Датирование", d.dm], ["Хронология", d.ch]];
  let h = "<h2>"+esc(d.f!=="ND"?d.f:d.l)+"</h2><table>";
  rows.forEach(([k,v])=>{ h+="<tr><td class='k'>"+k+"</td><td class='v'>"+esc(v)+"</td></tr>"; });
  h += "</table><div class='pub'><b>Публикация</b><br>"+esc(d.pb)+"</div>";
  panel.innerHTML = h;
}
function render(){
  layer.clearLayers();
  const minSrc = +srcInp.value, reg = regSel.value; srcVal.textContent = minSrc;
  let shown = 0;
  DATA.forEach(d=>{
    if (d.ns < minSrc) return;
    if (reg !== "all" && d.r !== reg) return;
    shown++;
    const m = L.circleMarker([d.lat,d.lon],
      {radius:5, color:regColor[d.r]||"#888", weight:1, fillColor:regColor[d.r]||"#888", fillOpacity:.55});
    m.bindTooltip((d.f!=="ND"?d.f:d.l)+(d.r!=="ND"?" ("+d.r+")":""));
    m.on("click",()=>showCard(d));
    layer.addLayer(m);
  });
  stat.textContent = "Показано разрезов: "+shown+" из "+DATA.length;
}
srcInp.oninput = render; regSel.onchange = render;
render();
</script>
</body>
</html>"""

out = HTML.replace("__DATA__", data_json).replace("__MAXSRC__", str(maxsrc))
with open(os.path.join(HERE, "index.html"), "w", encoding="utf-8") as f:
    f.write(out)
kb = len(out.encode("utf-8")) / 1024
nreg = len({d["r"] for d in rows if d["r"] != "ND"})
print(f"index.html собран: {len(rows)} точек, {kb:.0f} КБ, регионов {nreg}, maxsrc {maxsrc}")
