import json, traceback, time
from threading import Thread
from flask import Flask, render_template, request, redirect, url_for, flash

# Importa utilidades del bot
from grid_bot import (
    listar_simbolos_futuros,
    preview_grilla,
    crear_grilla,
    cancelar_todas_ordenes,
    estado_ordenes,
)

app = Flask(__name__)
app.secret_key = "sPOGe7uwyXkbzF48JOeLzrxPsEJT8OK1l3foP5Oiw8U7dGEHzKaHgL4TJWlWYddv"

# ────────────────────────────────────────────
# Rutas web
# ────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html", grids=estado_ordenes)

@app.route("/nuevo", methods=["GET", "POST"])
def nuevo():
    if request.method == "POST":
        form = request.form
        try:
            prev = preview_grilla(
                sym       = form["simbolo"],
                tipo      = "long" if form["tipo"] == "compra" else "short",
                pe        = float(form["precio_entrada"]),
                dist      = float(form["distancia"]),
                pasos     = int(form["pasos"]),
                coins_total = float(form["coins_total"]),
                loss_usdt   = float(form["monto_a_perder_usdt"]),
                n_tp      = int(form["cantidad_tp"]),
                dist_tp   = float(form["distancia_tp"]),
            )
            return render_template("preview.html", prev=prev, raw=json.dumps(form))
        except Exception as e:
            traceback.print_exc()
            flash(f"❌ Error: {e}", "danger")

    return render_template("crear_grilla.html", simbolos=listar_simbolos_futuros())

@app.route("/confirm", methods=["POST"])
def confirm():
    raw = json.loads(request.form["raw"])
    try:
        crear_grilla(
            sym         = raw["simbolo"],
            tipo        = "long" if raw["tipo"] == "compra" else "short",
            pe          = float(raw["precio_entrada"]),
            dist        = float(raw["distancia"]),
            pasos       = int(raw["pasos"]),
            coins_total = float(raw["coins_total"]),
            loss_usdt   = float(raw["monto_a_perder_usdt"]),
            n_tp        = int(raw["cantidad_tp"]),
            dist_tp     = float(raw["distancia_tp"]),
        )
        flash("✅ Grid colocada", "success")
        return redirect(url_for("status"))
    except Exception as e:
        traceback.print_exc()
        flash(f"❌ Error: {e}", "danger")
        return redirect(url_for("nuevo"))

@app.route("/status")
def status():
    return render_template("status.html", estado_ordenes=estado_ordenes)

@app.route("/cancelar/<path:symbol>")
def cancelar(symbol):
    cancelar_todas_ordenes(symbol)
    flash(f"Órdenes de {symbol} canceladas", "info")
    return redirect(url_for("status"))

# ────────────────────────────────────────────
# Hilo de monitorización (placeholder)
# ────────────────────────────────────────────

def monitor():
    while True:
        time.sleep(30)
        print("[MONITOR] Bot activo…")

# ────────────────────────────────────────────
if __name__ == "__main__":
    Thread(target=monitor, daemon=True).start()
    print("· WebApp arrancando en http://localhost:5000 …")
    app.run(debug=True, port=5000)
