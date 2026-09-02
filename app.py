"""
Dashboard de reposición en vivo — Shopify multi-marca.
Lee ventas + stock + productos en vivo y sugiere qué reponer.
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

# ---------------------------------------------------------------------------
# Estilo "futurista" (oscuro + neón)
# ---------------------------------------------------------------------------
NEON = "#00e5ff"
NEON2 = "#a855f7"
OK = "#22e0a1"
WARN = "#ffcf5c"
BAD = "#ff5c7c"
BG = "#0a0e17"
CARD = "#111827"

st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@600;800&family=Rajdhani:wght@500;600;700&display=swap');
.stApp {{ background:
   radial-gradient(1200px 600px at 15% -10%, rgba(0,229,255,.08), transparent),
   radial-gradient(1000px 500px at 110% 10%, rgba(168,85,247,.10), transparent),
   {BG}; color:#e7ecf3; font-family:'Rajdhani',system-ui,sans-serif; }}
h1,h2,h3 {{ font-family:'Orbitron',sans-serif; letter-spacing:.5px; }}
h1 {{ background:linear-gradient(90deg,{NEON},{NEON2}); -webkit-background-clip:text;
     -webkit-text-fill-color:transparent; }}
section[data-testid="stSidebar"] {{ background:#0b1220; border-right:1px solid rgba(0,229,255,.15); }}
.kpi {{ background:linear-gradient(145deg,{CARD},#0c1322); border:1px solid rgba(0,229,255,.20);
   border-radius:16px; padding:16px 18px; box-shadow:0 0 24px rgba(0,229,255,.06);}}
.kpi .lbl {{ font-size:.8rem; text-transform:uppercase; letter-spacing:1px; color:#8aa0b6; }}
.kpi .val {{ font-family:'Orbitron',sans-serif; font-size:1.7rem; font-weight:800; color:#fff; }}
.kpi .sub {{ font-size:.8rem; color:{NEON}; }}
div[data-testid="stDataFrame"] {{ border:1px solid rgba(0,229,255,.15); border-radius:12px; }}
.stButton>button {{ background:linear-gradient(90deg,{NEON},{NEON2}); color:#05121a; font-weight:700;
   border:0; border-radius:10px; }}
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Carga de datos (cacheada)
# ---------------------------------------------------------------------------
def get_stores() -> dict:
    if "stores" not in st.secrets:
        st.error("No hay tiendas en *Secrets* (`[stores.*]`). Ver README.")
        st.stop()
    return {n: {"url": c["url"], "token": c["token"]} for n, c in st.secrets["stores"].items()}


@st.cache_data(show_spinner=False, ttl=900)
def load_data(nombres: tuple, days: int):
    stores = get_stores()
    prog = st.progress(0.0, text="Conectando…")
    df_var, serie, errores = build_variant_table(
        stores, list(nombres), days,
        progress_cb=lambda m, f: prog.progress(min(1.0, f), text=m))
    prog.empty()
    return df_var, serie, errores


def kpi(col, label, value, sub=""):
    col.markdown(f'<div class="kpi"><div class="lbl">{label}</div>'
                 f'<div class="val">{value}</div><div class="sub">{sub}</div></div>',
                 unsafe_allow_html=True)


def excel_bytes(hojas: dict) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        for nombre, d in hojas.items():
            d.to_excel(w, index=False, sheet_name=nombre[:31])
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Header + controles
# ---------------------------------------------------------------------------
st.title("⚡ REPOSICIÓN EN VIVO")
st.caption("Ventas, stock y sugerido de reposición — lectura en vivo de Shopify.")

stores = get_stores()
with st.sidebar:
    st.header("⚙️ Parámetros")
    marcas = st.multiselect("Marcas", list(stores.keys()), default=list(stores.keys()))
    ventana = st.select_slider("Ventana de ventas (días)", [7, 14, 30, 60, 90], value=30)
    cobertura = st.slider("Cobertura objetivo (días)", 7, 120, 30, step=1)
    lead = st.slider("Lead time / demora (días)", 0, 90, 15, step=1)
    safety = st.slider("Colchón de seguridad (%)", 0, 100, 10, step=5)
    st.divider()
    if st.button("🔄 Refrescar datos en vivo", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.caption("Los datos se cachean 15 min. Usá *Refrescar* para forzar la lectura.")

if not marcas:
    st.warning("Elegí al menos una marca.")
    st.stop()

try:
    df_var, serie, errores = load_data(tuple(sorted(marcas)), ventana)
except ShopifyDataError as e:
    st.error(str(e))
    st.stop()

for err in errores:
    st.error(err)

if df_var is None or df_var.empty:
    st.warning("No se pudieron traer datos. Revisá tokens/scopes (read_products, read_orders, read_inventory).")
    st.stop()

# métricas de reposición (recalcula rápido al mover sliders, sin refetch)
df_var = add_reorder_metrics(df_var, ventana, cobertura, lead, safety)
mc = aggregate_model_color(df_var)

# filtro buscador
buscar = st.text_input("🔎 Buscar modelo / color / SKU", "")
if buscar:
    m = buscar.lower()
    mask = (mc["Modelo"].str.lower().str.contains(m) | mc["Color"].str.lower().str.contains(m)
            | mc["SKU"].str.lower().str.contains(m))
    mc_f = mc[mask]
else:
    mc_f = mc

# ---------------------------------------------------------------------------
# KPIs
# ---------------------------------------------------------------------------
u_vend = int(df_var["Vendidos"].sum())
ingresos = df_var["Ingresos"].sum()
u_reponer = int(df_var["Reponer"].sum())
sku_reponer = int((mc["Reponer"] > 0).sum())
quiebres = int(((df_var["Stock"] <= 0) & (df_var["Vendidos"] > 0)).sum())
vel_dia = round(df_var["Vendidos"].sum() / ventana, 1)

c1, c2, c3, c4, c5, c6 = st.columns(6)
kpi(c1, "Unidades vendidas", f"{u_vend:,}".replace(",", "."), f"últimos {ventana} días")
kpi(c2, "Ingresos", f"${ingresos:,.0f}".replace(",", "."), "en la ventana")
kpi(c3, "Ritmo", f"{vel_dia}", "u/día")
kpi(c4, "A reponer (u)", f"{u_reponer:,}".replace(",", "."), f"{sku_reponer} modelos+color")
kpi(c5, "Quiebres", f"{quiebres}", "vend. con stock 0")
kpi(c6, "Marcas", f"{len(marcas)}", ", ".join(marcas)[:24])

st.divider()

# ---------------------------------------------------------------------------
# Ritmo de venta (serie diaria)
# ---------------------------------------------------------------------------
st.subheader("📈 Ritmo de venta")
if not serie.empty:
    serie["Fecha"] = pd.to_datetime(serie["Fecha"])
    piv = serie.groupby(["Fecha", "Tienda"])["Unidades"].sum().reset_index()
    fig = px.area(piv, x="Fecha", y="Unidades", color="Tienda",
                  color_discrete_sequence=[NEON, NEON2, OK, WARN, BAD, "#6ea8fe"])
    fig.update_layout(template="plotly_dark", height=320, margin=dict(l=10, r=10, t=20, b=10),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      legend=dict(orientation="h", y=1.15))
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("Sin ventas en la ventana seleccionada.")

# ---------------------------------------------------------------------------
# Ranking más vendidos + reposición
# ---------------------------------------------------------------------------
st.subheader("🏆 Más vendidos y reposición (modelo + color)")

tabla = mc_f.copy()
tabla["Cobertura (días)"] = tabla["Días de cobertura"].replace(float("inf"), 9999)
show = tabla[["Tienda", "Modelo", "Color", "SKU", "Vendidos", "Stock",
              "Cobertura (días)", "Reponer", "Talles"]].head(300)


def _style(df):
    def color_cov(v):
        if v >= 9999: return "color:#5b6b7d"
        if v < lead: return f"color:{BAD};font-weight:700"
        if v < cobertura: return f"color:{WARN}"
        return f"color:{OK}"
    sty = (df.style
           .map(color_cov, subset=["Cobertura (días)"])
           .bar(subset=["Vendidos"], color="rgba(0,229,255,.35)")
           .bar(subset=["Reponer"], color="rgba(168,85,247,.45)")
           .format({"Cobertura (días)": lambda v: "∞" if v >= 9999 else f"{v:.0f}"}))
    return sty


st.dataframe(_style(show), use_container_width=True, height=430)

cta1, cta2 = st.columns([1, 3])
with cta1:
    xlsx = excel_bytes({"Modelo+Color": mc, "Detalle por talle": df_var})
    st.download_button("⬇️ Descargar Excel (reposición)", xlsx,
                       file_name=f"reposicion_{datetime.now():%Y%m%d}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       use_container_width=True)

st.divider()

# ---------------------------------------------------------------------------
# Reposición urgente + Curva de talles
# ---------------------------------------------------------------------------
colA, colB = st.columns([1, 1])

with colA:
    st.subheader("🚨 Reponer ya")
    urg = mc[(mc["Días de cobertura"] < lead) & (mc["Vendidos"] > 0)].copy()
    urg = urg.sort_values("Días de cobertura").head(15)
    if urg.empty:
        st.success("Nada crítico: ningún modelo+color por debajo del lead time.")
    else:
        for _, r in urg.iterrows():
            cov = "quiebre" if r["Stock"] <= 0 else f"{r['Días de cobertura']:.0f} días"
            st.markdown(
                f"<div class='kpi' style='margin-bottom:8px;border-color:rgba(255,92,124,.4)'>"
                f"<b>{r['Modelo']} · {r['Color']}</b> <span style='color:#8aa0b6'>({r['Tienda']})</span><br>"
                f"<span style='color:{BAD}'>Cobertura: {cov}</span> · "
                f"Vendidos {int(r['Vendidos'])} · Stock {int(r['Stock'])} · "
                f"<b style='color:{NEON}'>Reponer {int(r['Reponer'])}</b></div>",
                unsafe_allow_html=True)

with colB:
    st.subheader("📐 Curva de talles")
    opciones = (mc.assign(k=mc["Modelo"] + " · " + mc["Color"] + "  [" + mc["Tienda"] + "]")
                ["k"].tolist())
    sel = st.selectbox("Elegí modelo + color", opciones) if opciones else None
    if sel:
        modelo = sel.split(" · ")[0]
        resto = sel.split(" · ")[1]
        color = resto.split("  [")[0]
        tienda = resto.split("[")[1].rstrip("]")
        d = df_var[(df_var["Modelo"] == modelo) & (df_var["Color"] == color)
                   & (df_var["Tienda"] == tienda) & (df_var["Talle"] != "")]
        if d.empty:
            st.info("Este producto no tiene talles cargados como opción.")
        else:
            d = d.sort_values("Talle", key=lambda s: s.map(lambda x: (len(x), x)))
            fig2 = go.Figure()
            fig2.add_bar(x=d["Talle"], y=d["Vendidos"], name="Vendidos", marker_color=NEON)
            fig2.add_bar(x=d["Talle"], y=d["Reponer"], name="Reponer", marker_color=NEON2)
            fig2.add_scatter(x=d["Talle"], y=d["Stock"], name="Stock", mode="lines+markers",
                             line=dict(color=WARN, width=2))
            fig2.update_layout(template="plotly_dark", barmode="group", height=340,
                               margin=dict(l=10, r=10, t=20, b=10),
                               paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                               legend=dict(orientation="h", y=1.2))
            st.plotly_chart(fig2, use_container_width=True)

st.caption(f"Actualizado {datetime.now():%d/%m/%Y %H:%M} · ventana {ventana}d · "
           f"cobertura {cobertura}d · lead {lead}d · colchón {safety}%")
