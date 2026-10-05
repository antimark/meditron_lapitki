from __future__ import annotations

import hmac
import io
import json
import os
import secrets
import threading
import zipfile
from dataclasses import asdict
from functools import wraps
from pathlib import Path

from flask import (
    Flask,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    send_file,
    url_for,
)
from werkzeug.utils import secure_filename

from auth_store import UserStore
from history_formatter import build_history
from processor import (
    finalize_batch_scores,
    get_batch_orchestrator,
    get_pipeline,
    prepare_batch,
    process_batch_item,
    process_md,
)
from src.anonymizer import MedicalTextAnonymizer

BASE_DIR = Path(__file__).resolve().parent
RUNTIME_DIR = Path(os.getenv("APP_RUNTIME_DIR", BASE_DIR / "runtime")).resolve()
DATA_DIR = RUNTIME_DIR / "data"
MD_DIR = RUNTIME_DIR / "md"
REPORT_DIR = RUNTIME_DIR / "anonymization_reports"
BATCH_DIR = RUNTIME_DIR / "batches"
SESSIONS_FILE = RUNTIME_DIR / "sessions.json"
USERS_DB = RUNTIME_DIR / "users.db"

for directory in (RUNTIME_DIR, DATA_DIR, MD_DIR, REPORT_DIR, BATCH_DIR):
    directory.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.getenv("APP_SECRET_KEY") or secrets.token_hex(32)
app.config.update(
    MAX_CONTENT_LENGTH=int(os.getenv("MAX_UPLOAD_MB", "64")) * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "0") == "1",
)

user_store = UserStore(USERS_DB)
_sessions_lock = threading.Lock()


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _new_session_id() -> str:
    return secrets.token_urlsafe(18).replace("-", "_")


def load_sessions():
    """Load sessions and lazily migrate legacy groups to stable session IDs."""
    with _sessions_lock:
        data = load_json(SESSIONS_FILE, default=[])
        groups = data if isinstance(data, list) else []
        changed = False
        for group in groups:
            if isinstance(group, dict) and not group.get("session_id"):
                group["session_id"] = str(group.get("batch_id") or _new_session_id())
                changed = True
        if changed:
            save_json(SESSIONS_FILE, groups)
        return groups


def save_sessions(groups):
    with _sessions_lock:
        save_json(SESSIONS_FILE, groups)


def append_session_group(group):
    with _sessions_lock:
        data = load_json(SESSIONS_FILE, default=[])
        groups = data if isinstance(data, list) else []
        group = dict(group)
        group.setdefault("session_id", str(group.get("batch_id") or _new_session_id()))
        groups.append(group)
        save_json(SESSIONS_FILE, groups)
        return group["session_id"]


def upsert_session_group(username: str, batch_id: str, filenames: list[str], anonymized: bool):
    """Append files to one browser batch while keeping sessions isolated per user."""
    with _sessions_lock:
        data = load_json(SESSIONS_FILE, default=[])
        groups = data if isinstance(data, list) else []
        target = None
        for group in groups:
            if group.get("user") == username and group.get("batch_id") == batch_id:
                target = group
                break
        if target is None:
            target = {
                "user": username,
                "batch_id": batch_id,
                "session_id": batch_id,
                "files": [],
                "anonymized": bool(anonymized),
            }
            groups.append(target)
        target.setdefault("session_id", batch_id)
        known = set(target.get("files", []))
        for filename in filenames:
            if filename not in known:
                target.setdefault("files", []).append(filename)
                known.add(filename)
        target["anonymized"] = bool(anonymized)
        save_json(SESSIONS_FILE, groups)
        return target["session_id"]


def find_user_session(username: str, session_id: str):
    for group in user_session_groups(username):
        if str(group.get("session_id")) == session_id or str(group.get("batch_id")) == session_id:
            return group
    return None


def _batch_manifest_path(batch_id: str) -> Path:
    if not __import__("re").fullmatch(r"[A-Za-z0-9_-]{8,96}", batch_id or ""):
        raise ValueError("Недопустимый идентификатор пакетной обработки")
    return BATCH_DIR / f"{batch_id}.json"


def _load_batch_manifest(batch_id: str, username: str):
    path = _batch_manifest_path(batch_id)
    manifest = load_json(path, default=None)
    if not isinstance(manifest, dict) or manifest.get("user") != username:
        return None
    return manifest


def csrf_token() -> str:
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


@app.context_processor
def inject_template_context():
    return {"csrf_token": csrf_token, "current_username": session.get("username")}


def csrf_valid() -> bool:
    expected = session.get("csrf_token", "")
    supplied = request.form.get("csrf_token", "") or request.headers.get("X-CSRF-Token", "")
    return bool(expected and supplied and hmac.compare_digest(str(expected), str(supplied)))


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("username"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "Требуется авторизация"}), 401
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def user_session_groups(username: str):
    return [
        g for g in load_sessions()
        if isinstance(g, dict) and g.get("user") == username and isinstance(g.get("files"), list)
    ]


def user_can_access(filename: str, username: str) -> bool:
    safe = Path(filename).name
    return any(safe in group.get("files", []) for group in user_session_groups(username))


def get_unique_case_stem(base_name: str) -> str:
    counter = 1
    while True:
        stem = base_name if counter == 1 else f"{base_name}_{counter}"
        paths = [
            MD_DIR / f"{stem}.md",
            MD_DIR / f"{stem}.txt",
            DATA_DIR / f"{stem}.json",
            DATA_DIR / f"{stem}_context.json",
            DATA_DIR / f"{stem}_score.json",
            REPORT_DIR / f"{stem}_report.json",
        ]
        if not any(path.exists() for path in paths):
            return stem
        counter += 1


def _safe_upload_basename(filename: str) -> tuple[str, str]:
    cleaned = secure_filename(filename or "")
    suffix = Path(cleaned).suffix.lower()
    if suffix not in {".md", ".txt"}:
        raise ValueError("Поддерживаются только .md и .txt файлы")
    stem = Path(cleaned).stem or "document"
    return stem, suffix


def _anonymize_upload(storage, stem: str, suffix: str) -> tuple[Path, dict]:
    raw = storage.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("Файл должен быть в UTF-8") from exc

    use_ner = os.getenv("ANONYMIZER_USE_NER", "1") == "1"
    mode = os.getenv("ANONYMIZER_MODE", "clinical")
    anonymizer = MedicalTextAnonymizer(mode=mode, use_ner=use_ner)
    anon_text, report = anonymizer.anonymize(text)

    anon_stem = get_unique_case_stem(f"{stem}_anon")
    dst = MD_DIR / f"{anon_stem}{suffix}"
    dst.write_text(anon_text, encoding="utf-8")
    save_json(REPORT_DIR / f"{anon_stem}_report.json", asdict(report))
    return dst, asdict(report)


@app.after_request
def security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


@app.get("/health")
def health():
    return jsonify({"status": "ok", "users": user_store.count_users()})


@app.route("/")
def home():
    return redirect(url_for("cases" if session.get("username") else "login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        if session.get("username"):
            return redirect(url_for("cases"))
        return render_template("login.html", error=None, mode="login", entered_login="")

    if not csrf_valid():
        abort(400, description="Invalid CSRF token")

    mode = request.form.get("mode", "login")
    username = user_store.normalize_username(request.form.get("login", ""))
    password = request.form.get("password", "")
    error = None

    if mode == "register":
        configured_secret = os.getenv("REGISTRATION_SECRET_KEY", "")
        supplied_secret = request.form.get("registration_secret", "")
        if not configured_secret:
            error = "Регистрация не настроена: задайте REGISTRATION_SECRET_KEY на сервере."
        elif not hmac.compare_digest(configured_secret, supplied_secret):
            error = "Неверный секретный ключ регистрации."
        else:
            ok, error = user_store.create_user(username, password)
            if ok:
                session.clear()
                session["username"] = username
                csrf_token()
                return redirect(url_for("cases"))
    else:
        if user_store.verify_user(username, password):
            session.clear()
            session["username"] = username
            csrf_token()
            return redirect(url_for("cases"))
        error = "Неверный логин или пароль."

    return render_template(
        "login.html",
        error=error,
        mode="register" if mode == "register" else "login",
        entered_login=username,
    ), 400


@app.post("/logout")
@login_required
def logout():
    if not csrf_valid():
        abort(400, description="Invalid CSRF token")
    session.clear()
    return redirect(url_for("login"))


@app.get("/cases")
@login_required
def cases():
    username = session["username"]
    display_sessions = []
    for group in user_session_groups(username):
        files = [f for f in group.get("files", []) if (DATA_DIR / Path(f).name).exists()]
        if files:
            display_sessions.append({
                "files": files,
                "anonymized": bool(group.get("anonymized")),
                "session_id": str(group.get("session_id") or group.get("batch_id") or ""),
                "batch_id": group.get("batch_id"),
            })
    display_sessions.reverse()
    return render_template("cases.html", sessions=display_sessions)


@app.get("/api/cases/<filename>")
@login_required
def get_case(filename):
    safe_filename = Path(filename).name
    if not safe_filename.lower().endswith(".json"):
        return jsonify({"error": "Недопустимый файл"}), 400
    if not user_can_access(safe_filename, session["username"]):
        return jsonify({"error": "Кейс не найден"}), 404

    stem = Path(safe_filename).stem
    json_path = DATA_DIR / f"{stem}.json"
    if not json_path.exists():
        return jsonify({"error": "JSON-файл не найден"}), 404
    json_data = load_json(json_path)
    if json_data is None:
        return jsonify({"error": "Не удалось прочитать JSON-файл"}), 500

    context_data = load_json(DATA_DIR / f"{stem}_context.json", default={}) or {}
    score_data = load_json(DATA_DIR / f"{stem}_score.json", default={}) or {}

    md_path = None
    for suffix in (".md", ".txt"):
        candidate = MD_DIR / f"{stem}{suffix}"
        if candidate.exists():
            md_path = candidate
            break
    md_content = None
    md_filename = None
    if md_path:
        md_filename = md_path.name
        try:
            md_content = md_path.read_text(encoding="utf-8")
        except OSError:
            pass

    return jsonify({
        "history": build_history(json_data),
        "context": context_data.get("fields", {}),
        "score": score_data,
        "md": md_content,
        "md_filename": md_filename,
    })



# The UI splits batch work into prepare/process/finalize so progress is visible
# and completed JSON files survive a later per-file failure.
@app.post("/api/batch/prepare")
@login_required
def prepare_batch_route():
    if not csrf_valid():
        return jsonify({"error": "Недействительный CSRF-токен"}), 400

    uploaded_files = request.files.getlist("files")
    if not uploaded_files:
        return jsonify({"error": "Файлы не переданы"}), 400

    anonymize = request.form.get("anonymize", "0").lower() in {"1", "true", "on", "yes"}
    batch_id = request.form.get("batch_id", "").strip() or secrets.token_urlsafe(18).replace("-", "_")
    try:
        _batch_manifest_path(batch_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    items = []
    md_paths = []
    try:
        for index, uploaded_file in enumerate(uploaded_files):
            if not uploaded_file.filename:
                continue
            base_name, suffix = _safe_upload_basename(uploaded_file.filename)
            if anonymize:
                md_path, anon_report = _anonymize_upload(uploaded_file, base_name, suffix)
            else:
                case_stem = get_unique_case_stem(base_name)
                md_path = MD_DIR / f"{case_stem}{suffix}"
                uploaded_file.save(md_path)
                anon_report = None
            md_paths.append(md_path)
            items.append({
                "index": len(items),
                "md_filename": md_path.name,
                "result_filename": f"{md_path.stem}.json",
                "anonymized": anonymize,
                "anonymization_safe": anon_report.get("safe") if anon_report else None,
            })
    except Exception as error:
        return jsonify({"error": f"Ошибка подготовки пакета: {error}"}), 500

    if not items:
        return jsonify({"error": "Нет поддерживаемых файлов"}), 400

    try:
        prepared = prepare_batch(md_paths)
    except Exception as error:
        # Batch analysis is optional; extraction must still be possible.
        probe_files = int(get_batch_orchestrator().ocfg.get("probe_files", get_batch_orchestrator().ocfg.get("warmup_files", 5)))
        prepared = {
            "probe_files": probe_files,
            "warmup_files": probe_files,
            "probe_document_ids": [x["md_filename"] for x in items[:probe_files]],
            "probe_indices": list(range(min(probe_files, len(items)))),
            "processing_order": list(range(len(items))),
            "similarity": {"enabled": False, "reason": "prepare_error", "error": repr(error)},
            "calibration": {"enabled": True, "probe_results": {}, "ready": False, "completed": 0, "expected": min(probe_files, len(items)), "adaptive_policy": {}},
            "previews": {},
        }

    manifest = {
        "version": 3,
        "user": session["username"],
        "batch_id": batch_id,
        "anonymized": anonymize,
        "items": items,
        "prepared": prepared,
    }
    save_json(_batch_manifest_path(batch_id), manifest)
    upsert_session_group(
        session["username"],
        batch_id,
        [item["result_filename"] for item in items],
        anonymize,
    )

    similarity = prepared.get("similarity", {})
    return jsonify({
        "status": "prepared",
        "batch_id": batch_id,
        "files": items,
        "probe_files": prepared.get("probe_files", prepared.get("warmup_files", 0)),
        "warmup_files": prepared.get("probe_files", prepared.get("warmup_files", 0)),
        "probe_document_ids": prepared.get("probe_document_ids", []),
        "probe_selection": prepared.get("probe_selection", {}),
        "processing_order": prepared.get("processing_order", list(range(len(items)))),
        "batch_regex_shift": prepared.get("batch_regex_shift", {}),
        "batch_similarity": {
            "enabled": bool(similarity.get("enabled")),
            "method": similarity.get("method"),
            "cluster_count": len(similarity.get("clusters", {})),
        },
    })


@app.post("/api/batch/process")
@login_required
def process_batch_route():
    if not csrf_valid():
        return jsonify({"error": "Недействительный CSRF-токен"}), 400
    batch_id = request.form.get("batch_id", "").strip()
    manifest = _load_batch_manifest(batch_id, session["username"])
    if manifest is None:
        return jsonify({"error": "Пакет не найден"}), 404
    try:
        index = int(request.form.get("index", "-1"))
    except ValueError:
        return jsonify({"error": "Недопустимый индекс файла"}), 400
    items = manifest.get("items", [])
    if index < 0 or index >= len(items):
        return jsonify({"error": "Файл пакета не найден"}), 404

    item = items[index]
    md_path = MD_DIR / Path(item["md_filename"]).name
    if not md_path.exists():
        return jsonify({"error": "Исходный файл пакета не найден"}), 404
    try:
        pack = process_batch_item(md_path, DATA_DIR, index, manifest.get("prepared"))
        prepared_state = manifest.get("prepared") or {}
        calibration = get_batch_orchestrator().record_probe_result(prepared_state, md_path.name, pack)
        manifest["prepared"] = prepared_state
        save_json(_batch_manifest_path(batch_id), manifest)
    except Exception as error:
        return jsonify({"error": f"Ошибка обработки {md_path.name}: {error}"}), 500

    return jsonify({
        "status": "ready",
        "batch_id": batch_id,
        "index": index,
        "filename": item["result_filename"],
        "md_filename": md_path.name,
        "quality_score": pack["score"].get("quality_score"),
        "quality_label": pack["score"].get("quality_label"),
        "routing": pack["score"].get("routing", {}),
        "calibration": {
            "ready": bool(calibration.get("ready")),
            "completed": int(calibration.get("completed", 0)),
            "expected": int(calibration.get("expected", 0)),
            "adaptive_policy_summary": (calibration.get("adaptive_policy", {}) or {}).get("summary", {}),
        },
    })


@app.post("/api/batch/finalize")
@login_required
def finalize_batch_route():
    if not csrf_valid():
        return jsonify({"error": "Недействительный CSRF-токен"}), 400
    batch_id = request.form.get("batch_id", "").strip()
    manifest = _load_batch_manifest(batch_id, session["username"])
    if manifest is None:
        return jsonify({"error": "Пакет не найден"}), 404
    md_paths = [MD_DIR / Path(x["md_filename"]).name for x in manifest.get("items", [])]
    try:
        summary = finalize_batch_scores(md_paths, DATA_DIR, manifest.get("prepared"))
    except Exception as error:
        return jsonify({"error": f"Ошибка финализации batch-score: {error}"}), 500
    manifest["finalize_summary"] = summary
    save_json(_batch_manifest_path(batch_id), manifest)
    return jsonify({"status": "finalized", "batch_id": batch_id, "summary": summary})


@app.get("/api/sessions/<session_id>/download")
@login_required
def download_session_jsons(session_id):
    group = find_user_session(session["username"], session_id)
    if group is None:
        return jsonify({"error": "Сессия не найдена"}), 404

    buffer = io.BytesIO()
    added = 0
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for filename in group.get("files", []):
            safe = Path(filename).name
            # Only final submission JSONs; context/score are intentionally excluded.
            if not safe.lower().endswith(".json") or safe.endswith("_context.json") or safe.endswith("_score.json"):
                continue
            path = DATA_DIR / safe
            if path.exists():
                archive.write(path, arcname=safe)
                added += 1
    if not added:
        return jsonify({"error": "В сессии пока нет готовых JSON"}), 404
    buffer.seek(0)
    return send_file(
        buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"session-{session_id}.zip",
        max_age=0,
    )


@app.post("/api/process")
@login_required
def process_files():
    if not csrf_valid():
        return jsonify({"error": "Недействительный CSRF-токен"}), 400

    uploaded_files = request.files.getlist("files")
    if not uploaded_files:
        return jsonify({"error": "Файлы не переданы"}), 400

    anonymize = request.form.get("anonymize", "0").lower() in {"1", "true", "on", "yes"}
    batch_id = request.form.get("batch_id", "").strip()
    if batch_id and not __import__("re").fullmatch(r"[A-Za-z0-9_-]{8,96}", batch_id):
        return jsonify({"error": "Недопустимый идентификатор пакетной обработки"}), 400
    results = []

    for uploaded_file in uploaded_files:
        if not uploaded_file.filename:
            continue
        try:
            base_name, suffix = _safe_upload_basename(uploaded_file.filename)

            if anonymize:
                md_path, anon_report = _anonymize_upload(uploaded_file, base_name, suffix)
                case_stem = md_path.stem
            else:
                case_stem = get_unique_case_stem(base_name)
                md_path = MD_DIR / f"{case_stem}{suffix}"
                uploaded_file.save(md_path)
                anon_report = None

            pack = process_md(md_path, DATA_DIR)
            results.append({
                "filename": f"{case_stem}.json",
                "md_filename": md_path.name,
                "anonymized": anonymize,
                "anonymization_safe": anon_report.get("safe") if anon_report else None,
                "quality_score": pack["score"].get("quality_score"),
                "quality_label": pack["score"].get("quality_label"),
            })
        except Exception as error:
            return jsonify({
                "error": f"Ошибка обработки {uploaded_file.filename}: {error}"
            }), 500

    if results:
        filenames = [item["filename"] for item in results]
        if batch_id:
            upsert_session_group(session["username"], batch_id, filenames, anonymize)
        else:
            append_session_group({
                "user": session["username"],
                "files": filenames,
                "anonymized": anonymize,
            })

    return jsonify({"status": "ready", "files": results, "anonymized": anonymize, "batch_id": batch_id or None})


@app.get("/api/system/status")
@login_required
def system_status():
    pipe = get_pipeline()
    orchestrator = get_batch_orchestrator()
    return jsonify({
        "status": "ok",
        "pipeline_config": os.getenv("CARDIO_PIPELINE_CONFIG", "config/default.toml"),
        **pipe.provider_status(),
        "startup_errors": pipe.startup_errors,
        "orchestration": {
            "enabled": bool(orchestrator.ocfg.get("enabled", True)),
            "probe_files": int(orchestrator.ocfg.get("probe_files", orchestrator.ocfg.get("warmup_files", 5))),
            "sampling": "representative+template_shift+diversity",
            "batch_similarity_enabled": orchestrator.similarity.enabled,
        },
    })


if __name__ == "__main__":
    app.run(
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "5000")),
        debug=os.getenv("FLASK_DEBUG", "0") == "1",
    )
