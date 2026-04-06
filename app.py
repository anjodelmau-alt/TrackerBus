"""
Sistema de Rastreio Colaborativo de Ônibus
Backend Flask - app.py
"""

import os
import time
import secrets
from functools import wraps

from flask import (
    Flask, render_template, request, jsonify,
    session, redirect, url_for, send_from_directory
)

import firebase_admin
from firebase_admin import credentials, db

# ---------------------------------------------------------------------------
# Inicialização
# ---------------------------------------------------------------------------

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))

# -- Firebase Admin SDK --
# Coloque o arquivo serviceAccountKey.json na raiz do projeto
# Obtenha em: Firebase Console → Configurações → Contas de Serviço → Gerar chave privada
_cred = credentials.Certificate("serviceAccountKey.json")
firebase_admin.initialize_app(_cred, {
    "databaseURL": os.environ.get(
        "FIREBASE_DATABASE_URL",
        "https://SEU-PROJETO-default-rtdb.firebaseio.com"   # ← altere aqui
    )
})

# ---------------------------------------------------------------------------
# Configurações de acesso
# ---------------------------------------------------------------------------

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "troque-esta-senha")

# Tokens válidos que serão embutidos nos QR Codes de cada ônibus.
# Formato: "linha_sentido", ex: "L101_IDA", "L101_VOLTA"
# Em produção leia de um banco de dados ou variável de ambiente.
VALID_TOKENS: set[str] = set(
    os.environ.get("VALID_TOKENS", "L101_IDA,L101_VOLTA,DEMO").split(",")
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return decorated


def is_banned(token: str) -> bool:
    try:
        return bool(db.reference(f"/banned/{token}").get())
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Rotas públicas
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    """Página principal – passageiro e rastreador."""
    token = request.args.get("token", "").strip()
    return render_template("index.html", token=token)


@app.route("/sw.js")
def service_worker():
    """Service Worker precisa ser servido da raiz."""
    return send_from_directory(app.root_path, "sw.js",
                               mimetype="application/javascript")


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

@app.route("/api/validate", methods=["POST"])
def validate_token():
    """
    Valida o token vindo do QR Code.
    Retorna o papel (role) do usuário: 'tracker' ou 'passenger'.
    """
    data = request.get_json(silent=True) or {}
    token = data.get("token", "").strip()
    user_id = data.get("userId", "").strip()

    if not token:
        return jsonify({"valid": False, "reason": "Token ausente"}), 400

    if token not in VALID_TOKENS:
        return jsonify({"valid": False, "reason": "QR Code inválido"}), 403

    if is_banned(user_id) or is_banned(token):
        return jsonify({"valid": False, "reason": "Acesso bloqueado pelo administrador"}), 403

    # Registra o usuário no Firebase (sem sobrescrever se já existir)
    user_ref = db.reference(f"/sessions/{token}/users/{user_id}")
    existing = user_ref.get()
    if not existing:
        user_ref.set({
            "connectedAt": int(time.time() * 1000),
            "lastSeen": int(time.time() * 1000),
            "role": "passenger",
            "stopId": None,
        })

    return jsonify({"valid": True, "token": token})


@app.route("/api/heartbeat", methods=["POST"])
def heartbeat():
    """Atualiza o lastSeen do usuário para detectar desconexões."""
    data = request.get_json(silent=True) or {}
    token = data.get("token", "").strip()
    user_id = data.get("userId", "").strip()

    if not token or not user_id:
        return jsonify({"ok": False}), 400

    if is_banned(user_id):
        return jsonify({"ok": False, "reason": "banned"}), 403

    try:
        db.reference(f"/sessions/{token}/users/{user_id}/lastSeen").set(
            int(time.time() * 1000)
        )
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# Painel de Administração
# ---------------------------------------------------------------------------

@app.route("/admin", methods=["GET"])
@admin_required
def admin_panel():
    return render_template("admin.html")


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    error = None
    if request.method == "POST":
        password = request.form.get("password", "")
        if password == ADMIN_PASSWORD:
            session["admin_logged_in"] = True
            session.permanent = False
            return redirect(url_for("admin_panel"))
        error = "Senha incorreta. Tente novamente."
    return render_template("admin_login.html", error=error)


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_logged_in", None)
    return redirect(url_for("admin_login"))


# ---------------------------------------------------------------------------
# API de Administração (protegida)
# ---------------------------------------------------------------------------

@app.route("/api/admin/sessions", methods=["GET"])
@admin_required
def admin_get_sessions():
    """Lista todas as sessões ativas com seus usuários."""
    try:
        sessions_data = db.reference("/sessions").get() or {}
        banned_data = db.reference("/banned").get() or {}

        result = []
        for token, session_obj in sessions_data.items():
            users = session_obj.get("users", {})
            tracker_info = session_obj.get("tracker", {})
            location = session_obj.get("location", {})

            user_list = []
            for uid, udata in users.items():
                user_list.append({
                    "id": uid,
                    "role": udata.get("role", "passenger"),
                    "connectedAt": udata.get("connectedAt"),
                    "lastSeen": udata.get("lastSeen"),
                    "stopId": udata.get("stopId"),
                    "banned": uid in banned_data,
                })

            result.append({
                "token": token,
                "trackerActive": bool(tracker_info.get("active")),
                "lastLocation": location,
                "userCount": len(user_list),
                "users": user_list,
            })

        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/admin/ban", methods=["POST"])
@admin_required
def admin_ban():
    """Bane um userId ou token (bloqueia acesso ao banco)."""
    data = request.get_json(silent=True) or {}
    target = data.get("target", "").strip()

    if not target:
        return jsonify({"error": "target (userId ou token) obrigatório"}), 400

    try:
        db.reference(f"/banned/{target}").set(True)
        # Remove o usuário da sessão ativa imediatamente
        # (as regras do Firebase impedirão novas escritas)
        return jsonify({"ok": True, "message": f"'{target}' banido com sucesso."})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/admin/unban", methods=["POST"])
@admin_required
def admin_unban():
    """Remove o banimento de um target."""
    data = request.get_json(silent=True) or {}
    target = data.get("target", "").strip()

    if not target:
        return jsonify({"error": "target obrigatório"}), 400

    try:
        db.reference(f"/banned/{target}").delete()
        return jsonify({"ok": True, "message": f"'{target}' desbanido."})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/admin/notify", methods=["POST"])
@admin_required
def admin_notify():
    """Envia uma notificação global para todos os usuários conectados."""
    data = request.get_json(silent=True) or {}
    message = data.get("message", "").strip()
    token = data.get("token")  # None = broadcast para todas as sessões

    if not message:
        return jsonify({"error": "message obrigatório"}), 400

    try:
        payload = {
            "message": message,
            "timestamp": int(time.time() * 1000),
            "active": True,
        }
        if token:
            db.reference(f"/sessions/{token}/notifications").push(payload)
        else:
            # Broadcast: escreve em todas as sessões ativas
            sessions_data = db.reference("/sessions").get() or {}
            for sess_token in sessions_data:
                db.reference(f"/sessions/{sess_token}/notifications").push(payload)

        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/admin/clear-session", methods=["POST"])
@admin_required
def admin_clear_session():
    """Encerra uma sessão de rastreio."""
    data = request.get_json(silent=True) or {}
    token = data.get("token", "").strip()

    if not token:
        return jsonify({"error": "token obrigatório"}), 400

    try:
        db.reference(f"/sessions/{token}/tracker").delete()
        db.reference(f"/sessions/{token}/location").delete()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# Ponto de entrada
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_ENV", "production") != "production"
    app.run(host="0.0.0.0", port=port, debug=debug)
