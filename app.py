"""secretmgmt - a tiny per-team env-file vault for Jenkins pipelines.

UI:   http://<host>:8097/<team>
API:  http://<host>:8097/<team>/api/...   (Authorization: Bearer <team token>)
"""
import hashlib
import hmac
import io
import os
import re
import secrets
import tarfile
import time
from datetime import datetime, timezone
from functools import wraps

from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv
from flask import (Flask, Response, abort, jsonify, redirect, render_template,
                   request, session, url_for)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.exc import IntegrityError

TEAM_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
PATH_RE = re.compile(r"^[A-Za-z0-9._/-]{1,255}$")
RESERVED_TEAMS = {"api", "static", "healthz"}

# For `python app.py` runs; real environment variables (e.g. from docker compose) take precedence.
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))


def _require_env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} must be set (see secretmgmt/.env.example)")
    return value


MASTER_KEY = _require_env("VAULT_MASTER_KEY")
try:
    FERNET = Fernet(MASTER_KEY.encode())
except ValueError as exc:
    raise RuntimeError("VAULT_MASTER_KEY is not a valid Fernet key") from exc

ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "").strip()

app = Flask(__name__)
app.config.update(
    SQLALCHEMY_DATABASE_URI=_require_env("DATABASE_URL"),
    SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True},
    SECRET_KEY=os.environ.get("SECRET_KEY")
    or hashlib.sha256(b"secretmgmt-session:" + MASTER_KEY.encode()).hexdigest(),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "false").lower() == "true",
    MAX_CONTENT_LENGTH=1024 * 1024,
)
db = SQLAlchemy(app)


def utcnow():
    return datetime.now(timezone.utc)


class Team(db.Model):
    __tablename__ = "vault_teams"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(63), unique=True, nullable=False)
    token_hash = db.Column(db.String(64), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)


class EnvFile(db.Model):
    __tablename__ = "vault_env_files"
    __table_args__ = (db.UniqueConstraint("team_id", "path"),)
    id = db.Column(db.Integer, primary_key=True)
    team_id = db.Column(db.Integer, db.ForeignKey("vault_teams.id", ondelete="CASCADE"), nullable=False)
    path = db.Column(db.String(255), nullable=False)
    content_encrypted = db.Column(db.Text, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    @property
    def content(self):
        try:
            return FERNET.decrypt(self.content_encrypted.encode()).decode()
        except InvalidToken:
            abort(500, "Stored file cannot be decrypted with the current VAULT_MASTER_KEY")

    @content.setter
    def content(self, value):
        self.content_encrypted = FERNET.encrypt(value.encode()).decode()


with app.app_context():
    db.create_all()


# ---------- helpers ----------

def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def new_token():
    return "vlt_" + secrets.token_urlsafe(32)


def valid_team_name(name):
    return bool(TEAM_RE.fullmatch(name)) and name not in RESERVED_TEAMS


def clean_path(raw):
    path = (raw or "").strip()
    if not PATH_RE.fullmatch(path) or path.startswith("/"):
        return None
    parts = path.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return None
    return path


def get_team(name):
    if not valid_team_name(name):
        abort(404)
    return Team.query.filter_by(name=name).first()


def session_authed(team):
    stored = session.get("teams", {}).get(team.name)
    return stored is not None and hmac.compare_digest(stored, team.token_hash)


def remember_login(team):
    teams = dict(session.get("teams", {}))
    teams[team.name] = team.token_hash
    session["teams"] = teams
    session.setdefault("csrf", secrets.token_urlsafe(24))


def check_csrf():
    sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf", "")
    if not hmac.compare_digest(sent, session.get("csrf", "")):
        abort(403, "Bad CSRF token")


def api_auth(view):
    """Accept a Bearer team token (Jenkins/curl) or a logged-in UI session (+CSRF for writes)."""
    @wraps(view)
    def wrapper(team, *args, **kwargs):
        t = Team.query.filter_by(name=team).first() if valid_team_name(team) else None
        header = request.headers.get("Authorization", "")
        if t is not None and header.startswith("Bearer "):
            if hmac.compare_digest(hash_token(header[7:].strip()), t.token_hash):
                return view(t, *args, **kwargs)
        elif t is not None and session_authed(t):
            if request.method not in ("GET", "HEAD"):
                check_csrf()
            return view(t, *args, **kwargs)
        return jsonify(error="unauthorized"), 401
    return wrapper


def path_arg():
    path = clean_path(request.args.get("path"))
    if path is None:
        abort(400, "Invalid path: use a relative path like k8s/base/secrets.env")
    return path


@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(413)
def api_errors(err):
    if "/api/" in request.path:
        return jsonify(error=err.description), err.code
    return err


# ---------- UI ----------

@app.get("/healthz")
def healthz():
    db.session.execute(db.text("SELECT 1"))
    return {"status": "ok"}


@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        name = request.form.get("team", "").strip().lower()
        if valid_team_name(name):
            return redirect(url_for("team_page", team=name))
        return render_template("index.html", error="Team names use a-z, 0-9 and '-'.")
    return render_template("index.html")


@app.get("/<team>")
def team_page(team):
    t = get_team(team)
    if t is None:
        return render_template("team.html", team=team, mode="create", admin_required=bool(ADMIN_TOKEN))
    if not session_authed(t):
        return render_template("team.html", team=team, mode="login")
    return render_template("team.html", team=team, mode="files", csrf=session["csrf"],
                           new_token=session.pop("new_token", None))


@app.post("/<team>/create")
def team_create(team):
    if get_team(team) is not None:
        return redirect(url_for("team_page", team=team))
    if ADMIN_TOKEN and not hmac.compare_digest(request.form.get("admin_token", ""), ADMIN_TOKEN):
        return render_template("team.html", team=team, mode="create", admin_required=True,
                               error="Wrong admin token."), 403
    token = new_token()
    t = Team(name=team, token_hash=hash_token(token))
    db.session.add(t)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return redirect(url_for("team_page", team=team))
    remember_login(t)
    session["new_token"] = token
    return redirect(url_for("team_page", team=team))


@app.post("/<team>/login")
def team_login(team):
    t = get_team(team) or abort(404)
    if not hmac.compare_digest(hash_token(request.form.get("token", "").strip()), t.token_hash):
        time.sleep(0.5)
        return render_template("team.html", team=team, mode="login", error="Invalid token."), 401
    remember_login(t)
    return redirect(url_for("team_page", team=team))


@app.post("/<team>/logout")
def team_logout(team):
    teams = dict(session.get("teams", {}))
    teams.pop(team, None)
    session["teams"] = teams
    return redirect(url_for("team_page", team=team))


@app.post("/<team>/rotate")
def team_rotate(team):
    t = get_team(team) or abort(404)
    if not session_authed(t):
        abort(403)
    check_csrf()
    token = new_token()
    t.token_hash = hash_token(token)
    db.session.commit()
    remember_login(t)
    session["new_token"] = token
    return redirect(url_for("team_page", team=team))


# ---------- API ----------

@app.get("/<team>/api/files")
@api_auth
def api_list(t):
    files = EnvFile.query.filter_by(team_id=t.id).order_by(EnvFile.path).all()
    return jsonify(files=[{"path": f.path, "updated_at": f.updated_at.isoformat()} for f in files])


@app.get("/<team>/api/file")
@api_auth
def api_get(t):
    f = EnvFile.query.filter_by(team_id=t.id, path=path_arg()).first() or abort(404, "No such file")
    return Response(f.content, mimetype="text/plain")


@app.put("/<team>/api/file")
@api_auth
def api_put(t):
    path = path_arg()
    try:
        content = request.get_data().decode("utf-8")
    except UnicodeDecodeError:
        abort(400, "File content must be UTF-8 text")
    f = EnvFile.query.filter_by(team_id=t.id, path=path).first()
    if f is None:
        f = EnvFile(team_id=t.id, path=path)
        db.session.add(f)
    f.content = content
    db.session.commit()
    return jsonify(path=path, updated_at=f.updated_at.isoformat())


@app.delete("/<team>/api/file")
@api_auth
def api_delete(t):
    f = EnvFile.query.filter_by(team_id=t.id, path=path_arg()).first() or abort(404, "No such file")
    db.session.delete(f)
    db.session.commit()
    return jsonify(deleted=f.path)


@app.get("/<team>/api/bundle.tar.gz")
@api_auth
def api_bundle(t):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for f in EnvFile.query.filter_by(team_id=t.id).order_by(EnvFile.path):
            data = f.content.encode()
            info = tarfile.TarInfo(name=f.path)
            info.size = len(data)
            info.mode = 0o600
            info.mtime = int(f.updated_at.timestamp())
            tar.addfile(info, io.BytesIO(data))
    return Response(buf.getvalue(), mimetype="application/gzip",
                    headers={"Content-Disposition": f'attachment; filename="{t.name}-env.tar.gz"'})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8097")))
