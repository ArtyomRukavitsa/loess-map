#!/usr/bin/env python3
"""
Streamlit-карта по единому датасету (results.xlsx).
Запуск:  streamlit run loess_pipeline/app.py
"""
import re, pathlib
import pandas as pd
import streamlit as st
import pydeck as pdk

HERE = pathlib.Path(__file__).parent
XLSX = HERE / "results.xlsx"

PALETTE = [[31,119,180],[255,127,14],[44,160,44],[214,39,40],[148,103,189],[140,86,75],
           [227,119,194],[127,127,127],[188,189,34],[23,190,207],[174,199,232],[255,187,120]]

SRC_TOKENS = ["Галай","Величко 1997","рис.5","рис.13","рис.14","рис.6"]
def sources_of(comment):
    c = str(comment); out = []
    if "Галай" in c: out.append("Галай")
    if "Величко" in c: out.append("Величко 1997")
    for r in ("5","6","13","14"):
        if f"рис.{r}" in c or f"рис. {r}" in c: out.append(f"рис.{r}")
    return out or ["—"]

@st.cache_data
def load_df(path):
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True); ws = wb["base"]
    rows = list(ws.iter_rows(values_only=True))
    df = pd.DataFrame(rows[1:], columns=list(rows[0]))
    ren = {}
    for c in df.columns:
        cl = str(c).lower()
        if cl == "n": ren[c] = "lat"
        elif cl == "e": ren[c] = "lon"
        elif "accuracy" in cl: ren[c] = "radius_m"
        elif "name of geographic" in cl: ren[c] = "feature"
        elif "nearest locality" in cl: ren[c] = "locality"
        elif "administrative unit" in cl: ren[c] = "region"
        elif "thickness" in cl: ren[c] = "thickness"
        elif "absolute elevation" in cl: ren[c] = "elevation"
        elif "type of excavation" in cl: ren[c] = "excavation"
        elif "type of deposits" in cl: ren[c] = "deposits"
        elif "stratigraphic position" in cl: ren[c] = "strat"
        elif "chro" in cl and "available" in cl: ren[c] = "chrono"
        elif cl == "dating method": ren[c] = "dating"
        elif "publication 1" in cl: ren[c] = "pub"
        elif "doi / link 1" in cl: ren[c] = "doi"
        elif cl == "comments": ren[c] = "comments"
    df = df.rename(columns=ren)
    for col in ["lat","lon","radius_m"]:
        df[col] = pd.to_numeric(df[col].replace("ND", None), errors="coerce")
    for col in ["feature","locality","region","thickness","elevation","excavation","deposits",
                "strat","dating","chrono","pub","doi","comments"]:
        if col not in df: df[col] = "ND"
        df[col] = df[col].fillna("ND").astype(str)
    df["src_list"] = df["comments"].map(sources_of)
    df["source"] = df["src_list"].map(lambda s: "; ".join(s))
    df["nsrc"] = df["src_list"].map(len)
    df["chrono_disp"] = df["chrono"].map(lambda v: "Yes" if str(v).lower() in ("true","yes","1") else ("No" if str(v).lower() in ("false","no","0") else "ND"))
    df["coord"] = df.apply(lambda r: f"{r['lat']:.5f}, {r['lon']:.5f}" if pd.notna(r["lat"]) else "ND (геокодинг)", axis=1)
    df["rad_disp"] = df["radius_m"].map(lambda v: f"{int(v)} м" if pd.notna(v) else "ND")
    return df

def color_map(values):
    cats = sorted(set(values)); return {c: PALETTE[i % len(PALETTE)] for i, c in enumerate(cats)}

# ============================ UI ============================
st.set_page_config(page_title="Извлечение научных данных из публикаций", layout="wide")
df = load_df(XLSX)

st.title("Извлечение научных данных из публикаций и формирование баз данных")

sb = st.sidebar
sb.header("Фильтры")
regions = sorted(df["region"].unique())
dep_tokens = sorted({t.strip() for v in df["deposits"] for t in str(v).split(";") if t.strip() and t.strip()!="ND"})
radii = sorted([int(x) for x in df["radius_m"].dropna().unique()])

f_region = sb.multiselect("Регион", regions, default=regions)
f_dep    = sb.multiselect("Тип отложений", dep_tokens, default=dep_tokens)
f_src    = sb.multiselect("Источник", SRC_TOKENS, default=SRC_TOKENS)
f_rad    = sb.multiselect("Радиус точности, м", radii, default=radii)
only_geo = sb.checkbox("Только с координатами (для карты)", value=True)
color_by = sb.selectbox("Раскрасить по", ["source","region","deposits","strat"],
                        format_func={"source":"Источник","region":"Регион","deposits":"Тип отложений","strat":"Стратиграфия"}.get)

m = df["region"].isin(f_region)
m &= df["src_list"].apply(lambda lst: any(s in f_src for s in lst))
m &= df["deposits"].apply(lambda v: any(t.strip() in f_dep for t in str(v).split(";")) or str(v).strip()=="ND")
m &= (df["radius_m"].isin(f_rad) | df["radius_m"].isna())
fdf = df[m].copy()
cmap = color_map(fdf[color_by].fillna("ND").astype(str))
fdf["color"] = fdf[color_by].fillna("ND").astype(str).map(cmap)

tab_map, tab_tbl, tab_stat = st.tabs(["Карта", "Таблица", "Статистика"])

with tab_map:
    geo = fdf.dropna(subset=["lat","lon"]).copy()
    st.write(f"На карте: **{len(geo)}** точек (из {len(fdf)} отфильтрованных; без координат — {len(fdf)-len(geo)}). "
             "**Кликни точку** — справа появится карточка записи.")
    if len(geo):
        geo["r"] = geo["radius_m"].fillna(2000)
        mcol, dcol = st.columns([3, 1.5])
        with mcol:
            circles = pdk.Layer("ScatterplotLayer", geo, id="circles", get_position=["lon","lat"], get_radius="r",
                                get_fill_color="[color[0],color[1],color[2],55]", get_line_color="[color[0],color[1],color[2]]",
                                line_width_min_pixels=1, stroked=True, filled=True, pickable=True, auto_highlight=True)
            dots = pdk.Layer("ScatterplotLayer", geo, id="points", get_position=["lon","lat"], get_radius=3500,
                             radius_min_pixels=9, radius_max_pixels=16, get_fill_color="[color[0],color[1],color[2]]",
                             pickable=True, auto_highlight=True)
            view = pdk.ViewState(latitude=float(geo["lat"].mean()), longitude=float(geo["lon"].mean()), zoom=4)
            tooltip = {"html": "<b>{feature}</b><br/>{locality}",
                       "style": {"backgroundColor": "#333", "color": "white", "font-size": "12px"}}
            event = st.pydeck_chart(
                pdk.Deck(layers=[circles, dots], initial_view_state=view, tooltip=tooltip,
                         map_provider="carto", map_style="light"),
                on_select="rerun", selection_mode="single-object", key="map", use_container_width=True)
        with dcol:
            objs = {}
            try: objs = dict(event.selection["objects"])
            except Exception:
                try: objs = dict(event["selection"]["objects"])
                except Exception: objs = {}
            sel = next((v for v in objs.values() if v), [])
            if sel:
                o = sel[0]
                def g(k):
                    v = o.get(k, "ND")
                    return "ND" if v in (None, "", "nan") else v
                fields = [("Coordinates", g("coord")), ("Source records", f"{g('nsrc')} · ID: {g('ID')}"),
                          ("Accuracy radius", g("rad_disp")), ("Thickness / depth", g("thickness")),
                          ("Absolute elevation", g("elevation")), ("Locality", g("locality")),
                          ("Administrative unit", g("region")), ("Feature", g("feature")),
                          ("Excavation type", g("excavation")), ("Deposit type", g("deposits")),
                          ("Stratigraphy", g("strat")), ("Dating method", g("dating")),
                          ("Chronological data", g("chrono_disp"))]
                html = f"<div style='font-family:sans-serif'><h3 style='margin:0 0 10px'>{g('feature')}</h3><table style='border-collapse:collapse;font-size:13px'>"
                for k, v in fields:
                    html += f"<tr><td style='color:#666;padding:3px 12px 3px 0;vertical-align:top'>{k}</td><td style='font-weight:600'>{v}</td></tr>"
                html += "</table>"
                html += f"<div style='margin-top:10px;font-size:13px'><b>Publication</b><br/>{g('pub')}</div>"
                html += f"<div style='margin-top:6px;font-size:13px'><b>DOI / link</b><br/>{g('doi')}</div></div>"
                st.markdown(html, unsafe_allow_html=True)
                same = geo[(geo["lat"] == o.get("lat")) & (geo["lon"] == o.get("lon"))]
                st.markdown("**Records at this coordinate**")
                st.dataframe(same[["ID","excavation","deposits","strat","thickness","elevation","radius_m"]]
                             .rename(columns={"excavation":"Excavation","deposits":"Deposit","strat":"Stratigraphy",
                                              "thickness":"Thick.","elevation":"Elev.","radius_m":"Acc."}),
                             hide_index=True, use_container_width=True)
            else:
                st.info("Кликни точку на карте — здесь появится карточка записи.")
    else:
        st.info("Нет точек с координатами под текущие фильтры.")

with tab_tbl:
    cols = ["ID","feature","region","thickness","elevation","deposits","strat","dating","chrono_disp","source","lat","lon","radius_m"]
    cols = [c for c in cols if c in fdf.columns]
    st.dataframe(fdf[cols], use_container_width=True, hide_index=True)
    csv = fdf[cols].to_csv(index=False).encode("utf-8-sig")
    st.download_button("⬇️ Скачать отфильтрованное (CSV)", csv, "filtered.csv", "text/csv")

with tab_stat:
    c1,c2,c3 = st.columns(3)
    c1.metric("Разрезов (фильтр)", len(fdf))
    c2.metric("С координатами", int(fdf["lat"].notna().sum()))
    c3.metric("С датированием", int((fdf["chrono_disp"]=="Yes").sum()))
    st.subheader("По регионам"); st.bar_chart(fdf["region"].value_counts())
    st.subheader("По источникам"); st.bar_chart(fdf.explode("src_list")["src_list"].value_counts())
