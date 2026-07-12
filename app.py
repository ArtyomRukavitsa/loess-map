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
XLSX = HERE / "results.csv"                                   # CSV быстрее xlsx, только геокодированные (2112)

PALETTE = [[31,119,180],[255,127,14],[44,160,44],[214,39,40],[148,103,189],[140,86,75],
           [227,119,194],[127,127,127],[188,189,34],[23,190,207],[174,199,232],[255,187,120]]

SRC_TOKENS = ["БД (архив)"]
def sources_of(comment):
    return ["БД (архив)"]   # единый источник — консолидированная БД из архива публикаций

@st.cache_data
def load_df(path):
    df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig", on_bad_lines="skip")  # быстрый устойчивый парс
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
    df["n_sources"] = pd.to_numeric(df.get("n_sources", 1), errors="coerce").fillna(1).astype(int)
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
_maxsrc = max(2, int(df["n_sources"].max()) if len(df) else 2)
f_minsrc = sb.slider("Мин. источников (надёжность)", 1, _maxsrc, 1,
                     help="Разрезы, подтверждённые несколькими публикациями, надёжнее. Подними до 2+ чтобы убрать single-source шум.")
only_geo = sb.checkbox("Только с координатами (для карты)", value=True)
color_by = sb.selectbox("Раскрасить по", ["source","region","deposits","strat"],
                        format_func={"source":"Источник","region":"Регион","deposits":"Тип отложений","strat":"Стратиграфия"}.get)

m = df["region"].isin(f_region)
m &= df["src_list"].apply(lambda lst: any(s in f_src for s in lst))
m &= df["deposits"].apply(lambda v: any(t.strip() in f_dep for t in str(v).split(";")) or str(v).strip()=="ND")
m &= (df["radius_m"].isin(f_rad) | df["radius_m"].isna())
m &= (df["n_sources"] >= f_minsrc)
fdf = df[m].copy()
cmap = color_map(fdf[color_by].fillna("ND").astype(str))
fdf["color"] = fdf[color_by].fillna("ND").astype(str).map(cmap)

tab_map, tab_tbl, tab_stat = st.tabs(["Карта", "Таблица", "Статистика"])

with tab_map:
    geo = fdf.dropna(subset=["lat","lon"]).copy()
    st.write(f"На карте: **{len(geo)}** точек (из {len(fdf)} отфильтрованных; без координат — {len(fdf)-len(geo)}). "
             "**Наведи** на точку — название; **выбери разрез** — карточка + приближение на карте.")
    if len(geo):
        geo = geo.reset_index(drop=True); geo["r"] = geo["radius_m"].fillna(2000)
        labels = [f"{r['feature']} ({r['region']})"[:55] for _, r in geo.iterrows()]
        idx = st.selectbox("🔍 Разрез — карточка + зум на карте", [None] + list(range(len(geo))),
                           format_func=lambda i: "— обзор: все точки —" if i is None else labels[i], key="pick")
        mcol, dcol = st.columns([3, 1.5])
        with mcol:
            circles = pdk.Layer("ScatterplotLayer", geo, get_position=["lon", "lat"], get_radius="r",
                                get_fill_color="[color[0],color[1],color[2],70]", get_line_color="[color[0],color[1],color[2]]",
                                line_width_min_pixels=1, stroked=True, filled=True, pickable=True, auto_highlight=True)
            layers = [circles]
            if idx is None:
                view = pdk.ViewState(latitude=float(geo["lat"].mean()), longitude=float(geo["lon"].mean()), zoom=3.3)
            else:
                s = geo.iloc[idx]
                view = pdk.ViewState(latitude=float(s["lat"]), longitude=float(s["lon"]), zoom=7)
                layers.append(pdk.Layer("ScatterplotLayer", geo.iloc[[idx]], get_position=["lon", "lat"], get_radius=7000,
                              get_fill_color="[255,20,20,200]", stroked=True, get_line_color="[120,0,0]", line_width_min_pixels=2, pickable=True))
            tooltip = {"html": "<b>{feature}</b><br/>{locality}", "style": {"backgroundColor": "#333", "color": "#fff", "font-size": "12px"}}
            st.pydeck_chart(pdk.Deck(layers=layers, initial_view_state=view, tooltip=tooltip,
                            map_provider="carto", map_style="light"), use_container_width=True)
        with dcol:
            if idx is None:
                st.info("Выбери разрез в списке выше → карточка + приближение. Наведи на точку — название.")
            else:
                o = geo.iloc[idx].to_dict()
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
    st.subheader("По регионам")                                  # таблицы вместо st.bar_chart (altair ломается на Python 3.14)
    reg = fdf["region"].value_counts().rename_axis("Регион").reset_index(name="Разрезов")
    st.dataframe(reg[reg["Регион"] != "ND"].head(30), hide_index=True, use_container_width=True)
    st.subheader("По типам отложений")
    dep = fdf["deposits"].str.split(";").explode().str.strip()
    dep = dep[~dep.isin(["ND", ""])].value_counts().rename_axis("Отложения").reset_index(name="Разрезов")
    st.dataframe(dep, hide_index=True, use_container_width=True)
