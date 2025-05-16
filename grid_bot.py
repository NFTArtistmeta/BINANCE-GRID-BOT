import os, json, math, configparser, atexit, ccxt

"""
Grid-bot v2  –  Binance USD-M Futures
-------------------------------------
• Define la malla por NÚMERO TOTAL DE MONEDAS (`coins_total`) y pasos.
• Cada orden DCA = coins_total / pasos (redondeada al LOT_SIZE step).
• Garantiza notional ≥ 5 USDT después de todos los redondeos.
• Stop-loss en USDT (`loss_usdt`).  TP configurables.
• SL sin reduceOnly.  TP reduceOnly con fallback.
"""

# ───────────────────────────── 1) API keys ───────────────────────────
BASE_DIR = os.path.dirname(__file__)
cfg = configparser.ConfigParser()
cfg.read(os.path.join(BASE_DIR, "config.cfg"))

API_KEY    = cfg.get("binance", "api_key")
API_SECRET = cfg.get("binance", "api_secret", fallback=cfg.get("binance", "secret_key"))
if not (API_KEY and API_SECRET):
    raise SystemExit("Faltan api_key o api_secret en config.cfg")

# ───────────────────────────── 2) Exchange ───────────────────────────
exchange = ccxt.binanceusdm({"apiKey": API_KEY,
                             "secret": API_SECRET,
                             "enableRateLimit": True})
exchange.load_markets()

# ───────────────────────────── 3) Persistencia ───────────────────────
estado_ordenes: dict[str, dict] = {}
DB = os.path.join(BASE_DIR, "estados_de_ordenes.json")
if os.path.isfile(DB):
    estado_ordenes.update(json.load(open(DB)))

atexit.register(
    lambda: json.dump(estado_ordenes, open(DB, "w", encoding="utf-8"),
                      indent=2) if estado_ordenes else None
)

# ───────────────────────────── 4) Helpers ────────────────────────────
def listar_simbolos_futuros() -> list[str]:
    """Devuelve sólo contratos PERPETUAL (swap)."""
    return sorted(
        s for s, m in exchange.markets.items()
        if m.get("info", {}).get("contractType") == "PERPETUAL" or m.get("swap")
    )

ceil_to_step = lambda x, s: math.ceil(x / s) * s
prices = lambda pe, d, n, t: [pe - d*i for i in range(1, n+1)] if t=="long" \
                             else [pe + d*i for i in range(1, n+1)]

def step_size(m):
    for f in m["info"].get("filters", []):
        if f.get("filterType") == "LOT_SIZE" and "stepSize" in f:
            return float(f["stepSize"])
    prec = m.get("precision", {}).get("amount", 0)
    return 1 / (10**prec) if prec else 1

def _qty(sym: str, target_qty: float, step: float,
         price: float, min_not: float) -> float:
    """Ajusta qty hacia arriba hasta cumplir notional ≥ min_not tras redondeos."""
    qty = ceil_to_step(target_qty, step)
    while True:
        qty_p   = float(exchange.amount_to_precision(sym, qty))
        price_p = float(exchange.price_to_precision(sym, price))
        if qty_p * price_p >= min_not - 1e-8:
            return qty_p
        qty += step

# ───────────────────────────── 5) Preview ────────────────────────────
def preview_grilla(sym: str, tipo: str,
                   pe: float, dist: float, pasos: int,
                   coins_total: float, loss_usdt: float,
                   n_tp: int, dist_tp: float, lev: int = 20):
    m = exchange.markets[sym]
    pmin, pmax = float(m["limits"]["price"]["min"]), float(m["limits"]["price"]["max"])
    min_not    = max(float(m["limits"]["cost"]["min"] or 0), 5)   # regla Binance
    step       = step_size(m)

    qty_per_order = coins_total / pasos
    grid = []
    for price in prices(pe, dist, pasos, tipo):
        if not (pmin <= price <= pmax):
            continue
        qty = _qty(sym, qty_per_order, step, price, min_not)
        grid.append({"price": price, "qty": qty,
                     "side": "buy" if tipo == "long" else "sell"})

    if not grid:
        raise RuntimeError("Ninguna orden cumple el notional mínimo de 5 USDT.")

    qty_total = sum(o["qty"] for o in grid)
    sl_price  = pe - loss_usdt / qty_total if tipo == "long" \
                else pe + loss_usdt / qty_total
    sl_price  = max(pmin, min(sl_price, pmax))

    side_tp = "sell" if tipo == "long" else "buy"
    qty_tp  = qty_total / n_tp
    tp = []
    for i in range(1, n_tp+1):
        tp_p = pe + dist_tp*i if tipo == "long" else pe - dist_tp*i
        if pmin <= tp_p <= pmax:
            tp.append({"price": tp_p, "qty": qty_tp, "side": side_tp})

    return {"grid": grid,
            "sl":   {"price": sl_price, "qty": qty_total, "side": side_tp},
            "tp":   tp,
            "leverage": lev}

# ───────────────────────────── 6) Colocar grid ───────────────────────
def crear_grilla(sym: str, tipo: str,
                 pe: float, dist: float, pasos: int,
                 coins_total: float, loss_usdt: float,
                 n_tp: int, dist_tp: float, lev: int = 20):

    prev = preview_grilla(sym, tipo, pe, dist, pasos,
                          coins_total, loss_usdt, n_tp, dist_tp, lev)

    # leverage
    try:
        exchange.set_leverage(lev, sym)
    except AttributeError:
        exchange.fapiPrivate_post_leverage({"symbol": exchange.market_id(sym),
                                            "leverage": lev})

    grid_ids, tp_ids = [], []
    for o in prev["grid"]:
        qty_str   = exchange.amount_to_precision(sym, o["qty"])
        price_str = exchange.price_to_precision(sym, o["price"])
        maker = exchange.create_limit_buy_order if o["side"] == "buy" \
                else exchange.create_limit_sell_order
        grid_ids.append(
            maker(sym, qty_str, price_str, {"timeInForce": "GTC"})["id"]
        )

    # SL (sin reduceOnly)
    sl_id = exchange.create_order(
        symbol=sym, type="STOP_MARKET", side=prev["sl"]["side"],
        amount=exchange.amount_to_precision(sym, prev["sl"]["qty"]),
        price=None,
        params={"stopPrice": exchange.price_to_precision(sym, prev["sl"]["price"])}
    )["id"]

    # TP reduceOnly con fallback
    for o in prev["tp"]:
        qty_str   = exchange.amount_to_precision(sym, o["qty"])
        price_str = exchange.price_to_precision(sym, o["price"])
        try:
            tp_ids.append(exchange.create_order(
                symbol=sym, type="LIMIT", side=o["side"],
                amount=qty_str, price=price_str,
                params={"timeInForce": "GTC", "reduceOnly": True}
            )["id"])
        except ccxt.InvalidOrder:
            tp_ids.append(exchange.create_order(
                symbol=sym, type="LIMIT", side=o["side"],
                amount=qty_str, price=price_str,
                params={"timeInForce": "GTC"}
            )["id"])

    estado_ordenes[sym] = {"grid": grid_ids, "sl": sl_id, "tp": tp_ids}

# ───────────────────────────── 7) Cancelar ───────────────────────────
def cancelar_todas_ordenes(sym: str):
    rec = estado_ordenes.get(sym, {})
    for oid in rec.get("grid", []) + ([rec.get("sl")] if rec.get("sl") else []) + rec.get("tp", []):
        try: exchange.cancel_order(oid, sym)
        except Exception: pass
    estado_ordenes.pop(sym, None)

# ───────────────────────────── 8) Ejecución directa ──────────────────
if __name__ == "__main__":
    print("Símbolos PERPETUAL disponibles:")
    for s in listar_simbolos_futuros():
        print("-", s)
