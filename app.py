"""
Dashboard de reposición en vivo — Shopify multi-marca.
Alarmas de productos que se venden rápido (incl. lanzamientos), Top 10,
bloques por marca y sugerido de reposición.
"""

import io
from datetime import datetime

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from shopify_data import (build_variant_table, add_reorder_metrics,
                          aggregate_model_color, ShopifyDataError)

st.set_page_config(page_title="Reposición en vivo", page_icon="⚡", layout="wide")

NEON, NEON2 = "#00e5ff", "#a855f7"
OK, WARN, BAD, HOT = "#22e0a1", "#ffcf5c", "#ff5c7c", "#ff8a3d"
BG, CARD = "#0a0e17", "#111827"

st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@600;800&family=Rajdhani:wght@500;600;700&display=swap');
.stApp {{ background:
   radial-gradient(1200px 600px at 15% -10%, rgba(0,229,255,.08), transparent),
   radial-gradient(1000px 500px at 110% 10%, rgba(168,85,247,.10), transparent), {BG};
   color:#e7ecf3; font-family:'Rajdhani',system-ui,sans-serif; }}
h1,h2,h3 {{ font-family:'Orbitron',sans-serif; letter-spacing:.5px; }}
h1 {{ background:linear-gradient(90deg,{NEON},{NEON2}); -webkit-background-clip:text; -webkit-text-fill-color:transparent; }}
section[data-testid="stSidebar"] {{ background:#0b1220; border-right:1px solid rgba(0,229,255,.15); }}
.kpi {{ background:linear-gradient(145deg,{CARD},#0c1322); border:1px solid rgba(0,229,255,.20);
   border-radius:16px; padding:14px 16px; box-shadow:0 0 24px rgba(0,229,255,.06); }}
.kpi .lbl {{ font-size:.72rem; text-transform:uppercase; letter-spacing:1px; color:#8aa0b6; }}
.kpi .val {{ font-family:'Orbitron',sans-serif; font-size:1.5rem; font-weight:800; color:#fff; }}
.kpi .sub {{ font-size:.75rem; color:{NEON}; }}
.card {{ background:{CARD}; border:1px solid rgba(255,255,255,.08); border-radius:12px;
   padding:10px 12px; margin-bottom:8px; }}
.brand {{ background:linear-gradient(160deg,#0e1626,#0b1120); border:1px solid rgba(0,229,255,.18);
   border-radius:14px; padding:12px 14px; height:100%; }}
.brand h4 {{ margin:0 0 6px 0; font-family:'Orbitron'; color:{NEON}; }}
.pill {{ display:inline-block; padding:1px 8px; border-radius:999px; font-size:.72rem; font-weight:700; }}
.stButton>button {{ background:linear-gradient(90deg,{NEON},{NEON2}); color:#05121a; font-weight:700; border:0; border-radius:10px; }}
</style>
""", unsafe_allow_html=True)


def get_stores() -> dict:
    if "stores" not in st.secrets:
        st.error("No hay tiendas en *Secrets* (`[stores.*]`). Ver README.")
        st.stop()
    return {n: {"url": c["url"], "token": c["token"]} for n, c in st.secrets["stores"].items()}


@st.cache_data(show_spinner=False, ttl=900)
def load_data(nombres: tuple, days: int, recent_days: int):
    stores = get_stores()
    prog = st.progress(0.0, text="Conectando…")
    df_var, serie, errores = build_variant_table(
        stores, list(nombres), days, recent_days,
        progress_cb=lambda m, f: prog.progress(min(1.0, f), text=m))
    prog.empty()
    return df_var, serie, errores


def kpi(col, label, value, sub=""):
    col.markdown(f'<div class="kpi"><div class="lbl">{label}</div>'
                 f'<div class="val">{value}</div><div class="sub">{sub}</div></div>',
                 unsafe_allow_html=True)


def miles(n):
    return f"{int(n):,}".replace(",", ".")


def excel_bytes(hojas: dict) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        for nombre, d in hojas.items():
            d.to_excel(w, index=False, sheet_name=nombre[:31])
    return buf.getvalue()


# ---------------------------------------------------------------------------
st.title("⚡ REPOSICIÓN EN VIVO")
st.caption("Qué se está vendiendo rápido, qué reponer y qué lanzamiento despegó — en vivo.")

stores = get_stores()
with st.sidebar:
    st.header("⚙️ Parámetros")
    marcas = st.multiselect("Marcas", list(stores.keys()), default=list(stores.keys()))
    ventana = st.select_slider("Ventana base (días)", [7, 14, 30, 60, 90], value=30)
    reciente = st.select_slider("Ritmo actual: últimos (días)", [1, 2, 3, 7], value=3)
    st.divider()
    cobertura = st.slider("Cobertura objetivo (días)", 7, 120, 30)
    lead = st.slider("Lead time / demora (días)", 0, 90, 15)
    safety = st.slider("Colchón de seguridad (%)", 0, 100, 10, step=5)
    st.divider()
    accel = st.slider("Umbral de aceleración (🔥)", 1.2, 4.0, 1.6, step=0.1,
                      help="Ritmo actual ÷ ritmo base. 1.6 = vendiendo 60% más rápido que el promedio.")
    min_u = st.slider("Mínimo de unidades recientes", 1, 20, 3)
    launch = st.slider("Días para considerar 'lanzamiento'", 3, 90, 30)
    st.divider()
    if st.button("🔄 Refrescar datos en vivo", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.caption("Datos cacheados 15 min. *Refrescar* fuerza la lectura.")

if not marcas:
    st.warning("Elegí al menos una marca.")
    st.stop()

try:
    df_var, serie, errores = load_data(tuple(sorted(marcas)), ventana, reciente)
except ShopifyDataError as e:
    st.error(str(e)); st.stop()
for err in errores:
    st.error(err)
if df_var is None or df_var.empty:
    st.warning("Sin datos. Revisá tokens/scopes (read_products, read_orders, read_inventory).")
    st.stop()

kw = dict(recent_days=reciente, accel_thr=accel, launch_days=launch, min_units=min_u)
df_var = add_reorder_metrics(df_var, ventana, cobertura, lead, safety, **kw)
mc = aggregate_model_color(df_var, ventana, reciente, cobertura, lead, safety,
                           accel_thr=accel, launch_days=launch, min_units=min_u)

# ---------------------------------------------------------------------------
# KPIs
# ---------------------------------------------------------------------------
c = st.columns(6)
kpi(c[0], "Vendido (ventana)", miles(df_var["Vendidos"].sum()), f"últimos {ventana}d")
kpi(c[1], "Vendido hoy", miles(df_var["Vend_hoy"].sum()), "unidades")
kpi(c[2], "🔥 Acelerando", f"{int(mc['🔥'].sum())}", "reponer con urgencia")
kpi(c[3], "🚀 Lanzamientos", f"{int(mc['🚀'].sum())}", f"altas < {launch}d con tracción")
kpi(c[4], "A reponer (u)", miles(df_var["Reponer"].sum()), f"{int((mc['Reponer']>0).sum())} modelos+color")
kpi(c[5], "Quiebres", f"{int(((df_var['Stock']<=0)&(df_var['Vendidos']>0)).sum())}", "vend. con stock 0")

st.divider()

# ---------------------------------------------------------------------------
# ALARMAS
# ---------------------------------------------------------------------------
st.subheader("🔔 Alarmas — actuar ahora")


def card(r, tono, extra=""):
    ac = r["Aceleración"]
    ac_txt = "nuevo" if pd.isna(ac) else f"x{ac:.1f}"
    cov = r["Cobertura actual"]
    cov_txt = "quiebre" if r["Stock"] <= 0 else ("∞" if pd.isna(cov) else f"{cov:.0f}d")
    return (f"<div class='card' style='border-left:3px solid {tono}'>"
            f"<b>{r['Modelo']} · {r['Color']}</b> "
            f"<span style='color:#8aa0b6'>({r['Tienda']})</span><br>"
            f"<span style='color:{tono}'>{extra}</span> "
            f"hoy {int(r['Vend_hoy'])} · {reciente}d {int(r['Vend_recientes'])} · "
            f"ritmo {ac_txt} · cob {cov_txt} · "
            f"<b style='color:{NEON}'>reponer {int(r['Reponer'])}</b></div>")


a1, a2, a3 = st.columns(3)
with a1:
    st.markdown(f"<h4 style='color:{HOT}'>🔥 Vendiendo rápido</h4>", unsafe_allow_html=True)
    hot = mc[mc["🔥"]].sort_values(["Vend_recientes", "Aceleración"], ascending=False).head(10)
    if hot.empty:
        st.caption("Nada acelerando ahora mismo.")
    for _, r in hot.iterrows():
        st.markdown(card(r, HOT, "ACELERANDO ·"), unsafe_allow_html=True)
with a2:
    st.markdown(f"<h4 style='color:{NEON2}'>🚀 Lanzamientos con tracción</h4>", unsafe_allow_html=True)
    lz = mc[mc["🚀"]].sort_values("Vend_recientes", ascending=False).head(10)
    if lz.empty:
        st.caption(f"Sin lanzamientos (< {launch}d) traccionando.")
    for _, r in lz.iterrows():
        st.markdown(card(r, NEON2, f"NUEVO {int(r['Días desde alta'])}d ·"), unsafe_allow_html=True)
with a3:
    st.markdown(f"<h4 style='color:{BAD}'>🚨 Cobertura crítica</h4>", unsafe_allow_html=True)
    crit = mc[(mc["Vendidos"] > 0) & (mc["Cobertura actual"].fillna(0) < lead)]
    crit = crit.sort_values("Cobertura actual").head(10)
    if crit.empty:
        st.caption("Nada por debajo del lead time.")
    for _, r in crit.iterrows():
        st.markdown(card(r, BAD, "SE ACABA ·"), unsafe_allow_html=True)

st.divider()

# ---------------------------------------------------------------------------
# TOP 10 + RITMO
# ---------------------------------------------------------------------------
t1, t2 = st.columns([1, 1])
with t1:
    st.subheader("🏆 Top 10 más vendidos")
    top = mc.head(10).copy()
    top["etq"] = top["Modelo"].str.slice(0, 22) + " · " + top["Color"] + " (" + top["Tienda"] + ")"
    fig = px.bar(top[::-1], x="Vendidos", y="etq", orientation="h", color="Tienda",
                 color_discrete_sequence=[NEON, NEON2, OK, WARN, BAD, "#6ea8fe"], text="Vendidos")
    fig.update_layout(template="plotly_dark", height=380, margin=dict(l=10, r=10, t=10, b=10),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      yaxis_title="", xaxis_title="", legend=dict(orientation="h", y=1.12))
    st.plotly_chart(fig, use_container_width=True)
with t2:
    st.subheader("📈 Ritmo de venta")
    if not serie.empty:
        serie["Fecha"] = pd.to_datetime(serie["Fecha"])
        piv = serie.groupby(["Fecha", "Tienda"])["Unidades"].sum().reset_index()
        fig2 = px.area(piv, x="Fecha", y="Unidades", color="Tienda",
                       color_discrete_sequence=[NEON, NEON2, OK, WARN, BAD, "#6ea8fe"])
        fig2.update_layout(template="plotly_dark", height=380, margin=dict(l=10, r=10, t=10, b=10),
                           paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                           legend=dict(orientation="h", y=1.12))
        st.plotly_chart(fig2, use_container_width=True)
    else:
        st.info("Sin ventas en la ventana.")

st.divider()

# ---------------------------------------------------------------------------
# BLOQUES POR MARCA
# ---------------------------------------------------------------------------
st.subheader("🧱 Por marca")
cols = st.columns(min(3, len(marcas)))
for idx, marca in enumerate(sorted(marcas)):
    d = mc[mc["Tienda"] == marca]
    with cols[idx % len(cols)]:
        vend = int(d["Vendidos"].sum())
        hoy = int(d["Vend_hoy"].sum())
        rep = int((d["Reponer"] > 0).sum())
        nhot = int(d["🔥"].sum())
        top5 = d.head(5)
        filas = "".join(
            f"<div style='display:flex;justify-content:space-between;font-size:.85rem;"
            f"padding:2px 0;border-bottom:1px dashed rgba(255,255,255,.06)'>"
            f"<span>{'🔥 ' if r['🔥'] else ''}{r['Modelo'][:20]} · {r['Color'][:12]}</span>"
            f"<b style='color:{NEON}'>{int(r['Vendidos'])}</b></div>"
            for _, r in top5.iterrows())
        st.markdown(
            f"<div class='brand'><h4>{marca}</h4>"
            f"<div style='font-size:.8rem;color:#8aa0b6;margin-bottom:6px'>"
            f"vendido {miles(vend)} · hoy {hoy} · "
            f"<span class='pill' style='background:rgba(255,138,61,.18);color:{HOT}'>🔥 {nhot}</span> "
            f"<span class='pill' style='background:rgba(0,229,255,.15);color:{NEON}'>reponer {rep}</span>"
            f"</div>{filas}</div>", unsafe_allow_html=True)

st.divider()

# ---------------------------------------------------------------------------
# TABLA + CURVA
# ---------------------------------------------------------------------------
st.subheader("📋 Detalle (modelo + color)")
buscar = st.text_input("🔎 Buscar modelo / color / SKU", "")
tab = mc.copy()
if buscar:
    m = buscar.lower()
    tab = tab[tab["Modelo"].str.lower().str.contains(m) | tab["Color"].str.lower().str.contains(m)
              | tab["SKU"].str.lower().str.contains(m)]
tab["Cob"] = tab["Cobertura actual"].fillna(9999)
tab["Acel"] = tab["Aceleración"].fillna(0)
show = tab[["Tienda", "Modelo", "Color", "SKU", "Vendidos", "Vend_hoy", "Acel",
            "Stock", "Cob", "Reponer", "Talles"]].rename(
    columns={"Vend_hoy": "Hoy", "Acel": "Ritmo x", "Cob": "Cobertura d"}).head(400)


def _sty(df):
    def cov(v):
        if v >= 9999: return "color:#5b6b7d"
        if v < lead: return f"color:{BAD};font-weight:700"
        if v < cobertura: return f"color:{WARN}"
        return f"color:{OK}"
    return (df.style.map(cov, subset=["Cobertura d"])
            .bar(subset=["Vendidos"], color="rgba(0,229,255,.35)")
            .bar(subset=["Reponer"], color="rgba(168,85,247,.45)")
            .format({"Cobertura d": lambda v: "∞" if v >= 9999 else f"{v:.0f}",
                     "Ritmo x": lambda v: "—" if v == 0 else f"x{v:.1f}"}))


st.dataframe(_sty(show), use_container_width=True, height=430)
st.download_button("⬇️ Descargar Excel (reposición)",
                   excel_bytes({"Modelo+Color": mc, "Detalle por talle": df_var}),
                   file_name=f"reposicion_{datetime.now():%Y%m%d_%H%M}.xlsx",
                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

st.subheader("📐 Curva de talles")
opciones = (mc.assign(k=mc["Modelo"] + " · " + mc["Color"] + "  [" + mc["Tienda"] + "]")["k"].tolist())
sel = st.selectbox("Modelo + color", opciones) if opciones else None
if sel:
    modelo = sel.split(" · ")[0]
    color = sel.split(" · ")[1].split("  [")[0]
    tienda = sel.split("[")[1].rstrip("]")
    d = df_var[(df_var["Modelo"] == modelo) & (df_var["Color"] == color)
               & (df_var["Tienda"] == tienda) & (df_var["Talle"] != "")]
    if d.empty:
        st.info("Este producto no tiene talles como opción.")
    else:
        d = d.sort_values("Talle", key=lambda s: s.map(lambda x: (len(x), x)))
        fig3 = go.Figure()
        fig3.add_bar(x=d["Talle"], y=d["Vendidos"], name="Vendidos", marker_color=NEON)
        fig3.add_bar(x=d["Talle"], y=d["Reponer"], name="Reponer", marker_color=NEON2)
        fig3.add_scatter(x=d["Talle"], y=d["Stock"], name="Stock", mode="lines+markers",
                         line=dict(color=WARN, width=2))
        fig3.update_layout(template="plotly_dark", barmode="group", height=340,
                           margin=dict(l=10, r=10, t=20, b=10), paper_bgcolor="rgba(0,0,0,0)",
                           plot_bgcolor="rgba(0,0,0,0)", legend=dict(orientation="h", y=1.2))
        st.plotly_chart(fig3, use_container_width=True)

st.caption(f"Actualizado {datetime.now():%d/%m/%Y %H:%M} · base {ventana}d · actual {reciente}d · "
           f"cobertura {cobertura}d · lead {lead}d")
