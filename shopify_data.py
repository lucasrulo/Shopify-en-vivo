"""
shopify_data.py
---------------
Motor de datos del dashboard de reposición. Lee EN VIVO de cada tienda Shopify:

  - Productos + variantes (modelo, color, talle, SKU, stock)  -> /products.json
  - Ventas por variante en una ventana de días                -> /orders.json

y arma una tabla por variante con: unidades vendidas, velocidad (u/día),
stock actual, días de cobertura y sugerido de reposición.

Requiere en cada token los scopes: read_products, read_orders, read_inventory.
"""

from __future__ import annotations

import re
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Callable

import pandas as pd
import requests

API_VERSION = "2025-01"


class ShopifyDataError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# SKU modelo+color (misma lógica robusta del otro repo)
# ---------------------------------------------------------------------------
_COLOR_HINTS = ("color", "colour")
_SIZE_HINTS = ("talle", "talla", "size", "numero", "número", "nro", "medida", "pie")
_SIZE_TOKEN = re.compile(
    r"^(\d+([.,]\d+)?[A-Za-z]{0,2}"
    r"|[A-Za-z]{0,3}\d+([.,]\d+)?(/[A-Za-z]{0,3}\d+([.,]\d+)?)+)$"
)


def _looks_like_size(t: str) -> bool:
    t = (t or "").strip()
    return bool(t) and bool(_SIZE_TOKEN.match(t))


def _detect_positions(options: list) -> tuple[int | None, int | None]:
    color_pos = size_pos = None
    for o in options or []:
        name = (o.get("name") or "").strip().lower()
        pos = o.get("position")
        if color_pos is None and any(h in name for h in _COLOR_HINTS):
            color_pos = pos
        if size_pos is None and any(h in name for h in _SIZE_HINTS):
            size_pos = pos
    return color_pos, size_pos


def _vopt(v: dict, pos: int | None) -> str:
    return (v.get(f"option{pos}") or "").strip() if pos else ""


def _strip_last_if_talle(sku: str, talle: str) -> str:
    s, t = (sku or "").strip(), (talle or "").strip()
    if t and s.upper().endswith(t.upper()):
        s = s[: len(s) - len(t)].rstrip(" -_/.·")
    return s


def _base_sku_color(pares: list[tuple[str, str]]) -> str:
    skus = [s.strip() for _, s in pares if s and s.strip()]
    if not skus:
        return ""
    if len(set(skus)) == 1:
        s = skus[0]
        t = next((tt for tt, ss in pares if ss and ss.strip() == s), "")
        base = _strip_last_if_talle(s, t)
        if base == s and "-" in s:
            head, last = s.rsplit("-", 1)
            if _looks_like_size(last):
                base = head.rstrip(" -_/.·")
        return base or s
    partes = [s.split("-") for s in skus]
    comunes = []
    for i in range(min(len(p) for p in partes)):
        seg = {p[i] for p in partes}
        if len(seg) == 1:
            comunes.append(next(iter(seg)))
        else:
            break
    return "-".join(comunes).rstrip(" -_/.·") or skus[0]


# ---------------------------------------------------------------------------
# Cliente REST
# ---------------------------------------------------------------------------
class ShopifyRest:
    def __init__(self, shop_url: str, token: str):
        self.shop = shop_url.replace("https://", "").replace("http://", "").strip("/")
        self.session = requests.Session()
        self.session.headers.update({"X-Shopify-Access-Token": token,
                                     "Content-Type": "application/json"})

    def _get(self, url: str, params: dict | None):
        for attempt in range(6):
            r = self.session.get(url, params=params, timeout=60)
            if r.status_code == 429:
                time.sleep(float(r.headers.get("Retry-After", 2)))
                continue
            if r.status_code in (401, 403):
                raise ShopifyDataError(
                    f"{self.shop}: acceso denegado ({r.status_code}). "
                    f"El token necesita read_products, read_orders y read_inventory.")
            if r.status_code >= 500:
                time.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code != 200:
                raise ShopifyDataError(f"{self.shop}: HTTP {r.status_code} - {r.text[:200]}")
            return r
        raise ShopifyDataError(f"{self.shop}: demasiados reintentos.")

    def paginate(self, path: str, params: dict, root_key: str):
        url = f"https://{self.shop}/admin/api/{API_VERSION}/{path}"
        while True:
            r = self._get(url, params)
            data = r.json().get(root_key, [])
            for item in data:
                yield item
            nxt = _next_link(r.headers.get("Link", ""))
            if not nxt:
                break
            url, params = nxt, None


def _next_link(link_header: str) -> str | None:
    for part in (link_header or "").split(","):
        if 'rel="next"' in part:
            i, j = part.find("<"), part.find(">")
            if i != -1 and j != -1:
                return part[i + 1:j]
    return None


# ---------------------------------------------------------------------------
# Fetch por tienda
# ---------------------------------------------------------------------------
def fetch_variants(store: str, shop_url: str, token: str) -> list[dict]:
    """Variantes con modelo, color, talle, SKU base, SKU variante, stock, precio."""
    cli = ShopifyRest(shop_url, token)
    filas = []
    for p in cli.paginate("products.json",
                          {"limit": 250,
                           "fields": "id,title,vendor,status,created_at,options,variants"},
                          "products"):
        color_pos, size_pos = _detect_positions(p.get("options") or [])
        variants = p.get("variants") or []
        # base sku por color
        por_color = defaultdict(list)
        for v in variants:
            color = _vopt(v, color_pos) or (
                "" if (v.get("title") or "").lower() == "default title" else (v.get("title") or "")
            ) or "(sin color)"
            por_color[color].append(((_vopt(v, size_pos)), (v.get("sku") or "").strip()))
        base_por_color = {c: _base_sku_color(pares) for c, pares in por_color.items()}

        for v in variants:
            color = _vopt(v, color_pos) or (
                "" if (v.get("title") or "").lower() == "default title" else (v.get("title") or "")
            ) or "(sin color)"
            filas.append({
                "Tienda": store,
                "Modelo": (p.get("title") or "").strip(),
                "Color": color,
                "Talle": _vopt(v, size_pos),
                "SKU": base_por_color.get(color, ""),
                "SKU_variante": (v.get("sku") or "").strip(),
                "Stock": int(v.get("inventory_quantity") or 0),
                "Precio": float(v.get("price") or 0),
                "Estado": p.get("status", ""),
                "Vendor": p.get("vendor", "") or "",
                "Product ID": p.get("id"),
                "Creado": p.get("created_at", ""),
            })
    return filas


def fetch_sales(store: str, shop_url: str, token: str, days: int, recent_days: int = 3):
    """
    Unidades/ingresos por SKU en la ventana, con sub-ventanas para medir el
    ritmo ACTUAL (hoy, últimos `recent_days`, últimos 7). Además serie diaria.
    """
    cli = ShopifyRest(shop_url, token)
    hoy = datetime.now(timezone.utc).date()
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    por_sku = defaultdict(lambda: {"u": 0, "u7": 0, "ur": 0, "u1": 0, "rev": 0.0})
    diario = defaultdict(int)
    for o in cli.paginate("orders.json",
                          {"status": "any", "created_at_min": since, "limit": 250,
                           "fields": "created_at,line_items,financial_status"},
                          "orders"):
        try:
            fecha = datetime.fromisoformat(o["created_at"]).date()
            dago = (hoy - fecha).days
        except Exception:
            fecha, dago = None, 999
        for li in o.get("line_items") or []:
            sku = (li.get("sku") or "").strip()
            qty = int(li.get("quantity") or 0)
            if qty <= 0:
                continue
            price = float(li.get("price") or 0)
            if sku:
                s = por_sku[sku]
                s["u"] += qty
                s["rev"] += qty * price
                if dago <= 6:
                    s["u7"] += qty
                if dago <= recent_days - 1:
                    s["ur"] += qty
                if dago <= 0:
                    s["u1"] += qty
            if fecha:
                diario[fecha] += qty
    return por_sku, diario


# ---------------------------------------------------------------------------
# Construcción de la tabla + métricas
# ---------------------------------------------------------------------------
def build_variant_table(stores: dict, seleccion: list[str], days: int, recent_days: int = 3,
                        progress_cb: Callable[[str, float], None] | None = None):
    """
    Devuelve:
      df_var  : DataFrame por variante (Vendidos, Vend_recientes, Vend_hoy, Ingresos, ...)
      serie   : DataFrame diario de unidades por tienda
      errores : list[str]
    """
    filas, serie_rows, errores = [], [], []
    total = max(1, len(seleccion))
    for i, nombre in enumerate(seleccion):
        cfg = stores[nombre]
        if progress_cb:
            progress_cb(f"Leyendo {nombre}…", (i + 0.1) / total)
        try:
            variantes = fetch_variants(nombre, cfg["url"], cfg["token"])
            if progress_cb:
                progress_cb(f"{nombre}: ventas…", (i + 0.6) / total)
            ventas, diario = fetch_sales(nombre, cfg["url"], cfg["token"], days, recent_days)
            vacio = {"u": 0, "u7": 0, "ur": 0, "u1": 0, "rev": 0.0}
            for v in variantes:
                s = ventas.get(v["SKU_variante"], vacio)
                v["Vendidos"] = s["u"]
                v["Vend_7d"] = s["u7"]
                v["Vend_recientes"] = s["ur"]
                v["Vend_hoy"] = s["u1"]
                v["Ingresos"] = round(s["rev"], 2)
                filas.append(v)
            for f, u in diario.items():
                serie_rows.append({"Tienda": nombre, "Fecha": f, "Unidades": u})
        except Exception as e:  # noqa
            errores.append(f"{nombre}: {e}")
        if progress_cb:
            progress_cb(f"{nombre} listo ({i+1}/{total})", (i + 1) / total)

    return pd.DataFrame(filas), pd.DataFrame(serie_rows), errores


def _dias_desde(iso: str) -> float:
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).days
    except Exception:
        return 9999


INF = float("inf")


def _metricas(df: pd.DataFrame, days: int, recent_days: int,
              coverage_days: int, lead_days: int, safety_pct: int,
              accel_thr: float, launch_days: int, min_units: int) -> pd.DataFrame:
    """Calcula velocidad base/actual, aceleración, cobertura, reposición y flags."""
    vel_base = df["Vendidos"] / days
    vel_act = df["Vend_recientes"] / max(1, recent_days)
    factor = 1 + safety_pct / 100.0
    # se repone según el ritmo más exigente (base vs actual) para reaccionar a picos
    vel_eff = pd.concat([vel_base, vel_act], axis=1).max(axis=1)
    demanda = vel_eff * (coverage_days + lead_days) * factor
    df["Velocidad"] = vel_base.round(3)
    df["Vel actual"] = vel_act.round(3)
    df["Aceleración"] = (vel_act / vel_base.replace(0, pd.NA)).astype(float)
    df["Reponer"] = (demanda - df["Stock"]).round().clip(lower=0).astype(int)
    df["Cobertura"] = (df["Stock"] / vel_base.replace(0, pd.NA)).astype(float).round(1)
    df["Cobertura actual"] = (df["Stock"] / vel_act.replace(0, pd.NA)).astype(float).round(1)
    if "Creado" in df.columns:
        df["Días desde alta"] = df["Creado"].map(_dias_desde)
    else:
        df["Días desde alta"] = 9999
    # flags
    acel = df["Aceleración"]
    nuevo = df["Días desde alta"] <= launch_days
    df["🔥"] = ((acel >= accel_thr) & (df["Vend_recientes"] >= min_units)) | \
               (vel_base.eq(0) & (df["Vend_recientes"] >= min_units))
    df["🚀"] = nuevo & (df["Vend_recientes"] >= min_units)
    return df


def add_reorder_metrics(df_var: pd.DataFrame, days: int, coverage_days: int, lead_days: int,
                        safety_pct: int = 0, recent_days: int = 3, accel_thr: float = 1.6,
                        launch_days: int = 30, min_units: int = 3) -> pd.DataFrame:
    if df_var.empty:
        return df_var
    return _metricas(df_var.copy(), days, recent_days, coverage_days, lead_days,
                     safety_pct, accel_thr, launch_days, min_units)


def aggregate_model_color(df_var: pd.DataFrame, days: int, recent_days: int,
                          coverage_days: int, lead_days: int, safety_pct: int,
                          accel_thr: float = 1.6, launch_days: int = 30,
                          min_units: int = 3) -> pd.DataFrame:
    """Colapsa a modelo+color y recalcula ritmo/aceleración/reposición a ese nivel."""
    if df_var.empty:
        return df_var
    g = (df_var.groupby(["Tienda", "Modelo", "Color", "SKU"], dropna=False)
         .agg(Vendidos=("Vendidos", "sum"),
              Vend_7d=("Vend_7d", "sum"),
              Vend_recientes=("Vend_recientes", "sum"),
              Vend_hoy=("Vend_hoy", "sum"),
              Ingresos=("Ingresos", "sum"),
              Stock=("Stock", "sum"),
              Precio=("Precio", "max"),
              Creado=("Creado", "max"),
              Talles=("Talle", lambda s: ", ".join(sorted({x for x in s if x},
                                                           key=lambda z: (len(z), z)))))
         .reset_index())
    g = _metricas(g, days, recent_days, coverage_days, lead_days,
                  safety_pct, accel_thr, launch_days, min_units)
    return g.sort_values("Vendidos", ascending=False).reset_index(drop=True)
