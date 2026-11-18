"""Jira Time Tracker 3 — servidor.

Diferença para a versão 2: a tela não é mais montada no servidor a cada ação.
Aqui o Flask entrega *uma* página e, a partir daí, só JSON. Lançar, excluir e
filtrar viram chamadas `fetch`; a tela se atualiza sozinha e um erro vira um
aviso na própria página, sem recarregar nada.

Toda a conversa com Jira e Tempo continua em ``tracker_core.py``.

O programa não abre janela de console: o Flask roda em uma thread e o processo
fica representado por um ícone na área de notificação (ver ``tray.py``).
"""

import json
import logging
import os
import socket
import threading
import urllib.error
import urllib.request
import webbrowser
from datetime import date, datetime, timedelta
from logging.handlers import RotatingFileHandler

from flask import Flask, jsonify, render_template, request, send_from_directory, session
from werkzeug.exceptions import HTTPException

import credentials
import feriados
import tracker_core as core
import tray

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv é conveniência, não requisito
    pass

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", os.urandom(24))
app.json.ensure_ascii = False

# Estáticos têm hash na URL (ver `asset_url`), então podem ser cacheados forte.
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 60 * 60 * 24 * 30

DEFAULT_PORT = int(os.getenv("PORT", "5000"))
PROJECT_KEY = os.getenv("JIRA_PROJECT_KEY", "PEDIDO")
PROJECT_NAME = os.getenv("JIRA_PROJECT_NAME", "Portal de Pedidos")

WEEKDAYS_PT = [
    "Segunda-feira",
    "Terça-feira",
    "Quarta-feira",
    "Quinta-feira",
    "Sexta-feira",
    "Sábado",
    "Domingo",
]

# Lista de issues do projeto é cara e muda pouco; 2 minutos já evita a espera
# repetida de quem fica alternando entre as abas.
ISSUES_CACHE = core._TTLCache(ttl_seconds=int(os.getenv("ISSUES_CACHE_TTL", "120")))


# --------------------------------------------------------------------------- #
# Configuração
# --------------------------------------------------------------------------- #

def get_config():
    """Configuração da sessão do navegador ou, na falta dela, do cofre."""
    jira_keys = ("JIRA_URL", "JIRA_EMAIL", "JIRA_API_TOKEN")
    if all(session.get(key) for key in jira_keys):
        return {
            **{key: session[key] for key in jira_keys},
            "TEMPO_API_TOKEN": session.get("TEMPO_API_TOKEN", ""),
        }
    return core.load_config()


def require_config():
    config = get_config()
    if not core.jira_configured(config):
        raise ConfigMissing()
    return config


class ConfigMissing(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# Respostas de erro — sempre JSON nas rotas /api
# --------------------------------------------------------------------------- #

def fail(message, status=400, **extra):
    return jsonify({"ok": False, "error": str(message), **extra}), status


@app.errorhandler(ConfigMissing)
def handle_config_missing(_exc):
    return fail("Configure o Jira antes de usar esta tela.", 428, needs_config=True)


@app.errorhandler(core.ApiError)
def handle_api_error(exc):
    return fail(exc, 502)


@app.errorhandler(Exception)
def handle_unexpected(exc):
    # 404, 405 e companhia já são respostas prontas. Relançá-las aqui fazia o
    # Flask tratar o relançamento como falha e devolver 500 — um GET
    # /favicon.ico virava "erro interno" no log.
    if isinstance(exc, HTTPException):
        if request.path.startswith("/api/"):
            return fail(exc.description or exc.name, exc.code or 500)
        return exc.get_response()

    if request.path.startswith("/api/"):
        app.logger.exception("Erro inesperado em %s", request.path)
        return fail(f"Erro inesperado: {exc}", 500)
    raise exc


# --------------------------------------------------------------------------- #
# Páginas (duas rotas, só a casca)
# --------------------------------------------------------------------------- #

def asset_version(*relative_paths):
    """Versão dos estáticos = maior mtime. Cache eterno que ainda assim atualiza."""
    newest = 0
    for relative in relative_paths:
        path = os.path.join(app.static_folder, *relative.split("/"))
        try:
            newest = max(newest, int(os.path.getmtime(path)))
        except OSError:
            pass
    return str(newest)


@app.route("/")
def index():
    config = get_config()
    return render_template(
        "index.html",
        configured=core.jira_configured(config),
        tempo_mode=core.uses_tempo(config),
        backend_name="Tempo" if core.uses_tempo(config) else "Jira nativo",
        project_key=PROJECT_KEY,
        project_name=PROJECT_NAME,
        prefs=credentials.load_prefs(),
        asset_v=asset_version("css/app.css", "js/app.js"),
    )


@app.route("/config")
def config_page():
    config = get_config()
    return render_template(
        "config.html",
        config={
            "JIRA_URL": config.get("JIRA_URL", ""),
            "JIRA_EMAIL": config.get("JIRA_EMAIL", ""),
            # Tokens nunca voltam para a tela: só dizemos se existem.
            "has_jira_token": bool(config.get("JIRA_API_TOKEN")),
            "has_tempo_token": bool(config.get("TEMPO_API_TOKEN")),
        },
        prefs=credentials.load_prefs(),
        asset_v=asset_version("css/app.css", "js/config.js"),
    )


# --------------------------------------------------------------------------- #
# API — sinal de vida (usado para não abrir uma segunda instância)
# --------------------------------------------------------------------------- #

@app.route("/favicon.ico")
def favicon():
    """O navegador pede isso mesmo com <link rel="icon"> declarado."""
    return send_from_directory(
        os.path.join(app.root_path, "assets"),
        "jira-time-tracker.ico",
        mimetype="image/x-icon",
    )


@app.get("/api/ping")
def api_ping():
    """Quem responde aqui diz de onde saiu.

    Sem a pasta, uma instância antiga (outra cópia do projeto) era confundida
    com esta e o atalho novo só reabria a tela da velha.
    """
    return jsonify({
        "ok": True,
        "app": "jira-time-tracker",
        "version": 3,
        "root": os.path.dirname(os.path.abspath(__file__)),
    })


# --------------------------------------------------------------------------- #
# API — estado
# --------------------------------------------------------------------------- #

@app.get("/api/status")
def api_status():
    config = get_config()
    configured = core.jira_configured(config)
    return jsonify(
        {
            "ok": True,
            "configured": configured,
            "tempo_mode": core.uses_tempo(config),
            "backend": "Tempo" if core.uses_tempo(config) else "Jira nativo",
            "jira_url": config.get("JIRA_URL", ""),
            "email": config.get("JIRA_EMAIL", ""),
            "project_key": PROJECT_KEY,
            "project_name": PROJECT_NAME,
            "prefs": credentials.load_prefs(),
        }
    )


@app.get("/api/prefs")
def api_prefs():
    return jsonify({"ok": True, "prefs": credentials.load_prefs()})


@app.post("/api/prefs")
def api_save_prefs():
    """Preferências da tela: início da semana, ciclo do mês, horas por dia."""
    body = request.get_json(silent=True) or {}
    values = {}

    if "week_start" in body:
        if body["week_start"] not in ("monday", "sunday"):
            return fail("Início da semana deve ser 'monday' ou 'sunday'.")
        values["week_start"] = body["week_start"]

    if "month_cycle_start" in body:
        try:
            day = int(body["month_cycle_start"])
        except (TypeError, ValueError):
            return fail("Dia de fechamento inválido.")
        if not 1 <= day <= 28:
            return fail("O dia de fechamento precisa estar entre 1 e 28.")
        values["month_cycle_start"] = day

    if "target_hours" in body:
        try:
            hours = float(body["target_hours"])
        except (TypeError, ValueError):
            return fail("Horas por dia inválidas.")
        if not 0 < hours <= 24:
            return fail("Horas por dia precisa estar entre 0 e 24.")
        values["target_hours"] = hours

    if not values:
        return fail("Nada para salvar.")

    try:
        prefs = credentials.save_prefs(values)
    except OSError as exc:
        return fail(f"Não foi possível gravar as preferências: {exc}", 500)
    return jsonify({"ok": True, "prefs": prefs})


@app.post("/api/config")
def api_save_config():
    body = request.get_json(silent=True) or {}
    current = core.load_config()

    jira_url = (body.get("jira_url") or "").strip().rstrip("/")
    jira_email = (body.get("jira_email") or "").strip()
    # Campo em branco = manter o token atual. Em branco de propósito usa o
    # sinalizador explícito, para dar como limpar o token do Tempo.
    jira_token = (body.get("jira_api_token") or "").strip() or current.get("JIRA_API_TOKEN", "")
    if body.get("clear_tempo"):
        tempo_token = ""
    else:
        tempo_token = (body.get("tempo_api_token") or "").strip() or current.get(
            "TEMPO_API_TOKEN", ""
        )

    if not (jira_url and jira_email and jira_token):
        return fail("Informe URL, e-mail e token do Jira.")
    if not jira_url.startswith(("http://", "https://")):
        return fail("A URL do Jira precisa começar com https://")

    values = {
        "JIRA_URL": jira_url,
        "JIRA_EMAIL": jira_email,
        "JIRA_API_TOKEN": jira_token,
        "TEMPO_API_TOKEN": tempo_token,
    }

    # Testa antes de gravar: credencial errada não entra no cofre.
    try:
        me = core.get_myself(values)
    except Exception as exc:
        return fail(f"O Jira recusou estas credenciais: {exc}", 401)

    if tempo_token:
        try:
            core.get_tempo_worklogs(
                values, me["accountId"], date.today().isoformat(), date.today().isoformat()
            )
        except Exception as exc:
            return fail(f"O token do Tempo não funcionou: {exc}", 401)

    destinations = credentials.save_config(values)
    if not destinations:
        return fail("Não foi possível gravar as credenciais no cofre do usuário.", 500)

    session.update(values)
    core.ISSUE_CACHE.clear()
    ISSUES_CACHE.clear()
    return jsonify(
        {
            "ok": True,
            "saved_to": destinations,
            "display_name": me.get("displayName", ""),
            "tempo_mode": bool(tempo_token),
        }
    )


# --------------------------------------------------------------------------- #
# API — worklogs
# --------------------------------------------------------------------------- #

def parse_date_arg(value, fallback):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return fallback


def current_week_range(today=None, week_start=None):
    """Semana corrente, começando na segunda ou no domingo, conforme a preferência.

    Só vale como padrão: a tela sempre manda start e end explícitos.
    """
    today = today or date.today()
    week_start = week_start or credentials.load_prefs()["week_start"]
    # date.weekday(): segunda = 0 ... domingo = 6.
    back = today.weekday() if week_start == "monday" else (today.weekday() + 1) % 7
    first = today - timedelta(days=back)
    return first, first + timedelta(days=6)


def serialize_log(entry, jira_url):
    return {
        "id": str(entry["id"]),
        "key": entry["key"],
        "summary": entry["summary"],
        "seconds": int(entry["seconds"] or 0),
        "hours": round(int(entry["seconds"] or 0) / 3600.0, 4),
        "comment": entry["description"] or "",
        "url": f"{jira_url}/browse/{entry['key']}",
    }


@app.get("/api/worklogs")
def api_worklogs():
    config = require_config()

    default_start, default_end = current_week_range()
    start = parse_date_arg(request.args.get("start"), default_start)
    end = parse_date_arg(request.args.get("end"), default_end)
    if end < start:
        start, end = end, start
    if (end - start).days > 366:
        return fail("Período muito longo. Use no máximo 12 meses.")

    target_hours = float(
        request.args.get("target") or credentials.load_prefs()["target_hours"]
    )

    # Feriado é só um aviso: lançar nele é permitido, e cada time faz de um
    # jeito. Por isso ele não é cobrado enquanto estiver em branco — mas,
    # havendo horas lançadas, conta como qualquer dia útil (ver workdays).
    holidays = feriados.between(start, end)

    days = {}
    cursor = start
    while cursor <= end:
        iso = cursor.isoformat()
        holiday = holidays.get(iso)
        days[iso] = {
            "date": iso,
            "weekday": WEEKDAYS_PT[cursor.weekday()],
            "weekday_short": WEEKDAYS_PT[cursor.weekday()][:3],
            "weekend": cursor.weekday() >= 5,
            "holiday": holiday["nome"] if holiday else None,
            "holiday_scope": holiday["escopo"] if holiday else None,
            "today": cursor == date.today(),
            "future": cursor > date.today(),
            "seconds": 0,
            "logs": [],
        }
        cursor += timedelta(days=1)

    jira_url = config["JIRA_URL"]
    for entry in core.worklogs_normalized(config, start.isoformat(), end.isoformat()):
        day = days.get(entry["date"])
        if day is None:
            continue
        day["seconds"] += int(entry["seconds"] or 0)
        day["logs"].append(serialize_log(entry, jira_url))

    ordered = sorted(days.values(), key=lambda day: day["date"], reverse=True)
    for day in ordered:
        day["hours"] = round(day["seconds"] / 3600.0, 2)
        day["logs"].sort(key=lambda log: log["key"])

    # Feriado em branco não entra na conta: quem não trabalhou não deve nada.
    # Feriado com horas lançadas entra, para o saldo de quem trabalhou fechar.
    workdays = [
        day
        for day in ordered
        if not day["weekend"]
        and not day["future"]
        and not (day["holiday"] and not day["seconds"])
    ]
    total_seconds = sum(day["seconds"] for day in ordered)
    expected_seconds = int(len(workdays) * target_hours * 3600)

    return jsonify(
        {
            "ok": True,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "target_hours": target_hours,
            "backend": "Tempo" if core.uses_tempo(config) else "Jira nativo",
            "days": ordered,
            "summary": {
                "total_seconds": total_seconds,
                "total_hours": round(total_seconds / 3600.0, 2),
                "expected_seconds": expected_seconds,
                "expected_hours": round(expected_seconds / 3600.0, 2),
                "balance_hours": round((total_seconds - expected_seconds) / 3600.0, 2),
                "workdays": len(workdays),
                "complete_days": sum(
                    1 for day in workdays if day["seconds"] >= target_hours * 3600
                ),
                "entries": sum(len(day["logs"]) for day in ordered),
            },
        }
    )


@app.post("/api/worklogs")
def api_add_worklogs():
    config = require_config()
    body = request.get_json(silent=True) or {}

    issue_key = (body.get("issue_key") or "").strip().upper()
    comment = (body.get("comment") or "").strip()
    entries = body.get("entries") or []

    if not issue_key:
        return fail("Informe a issue (ex.: PEDIDO-123).")
    if not entries:
        return fail("Adicione ao menos uma data com duração.")

    try:
        account_id = core.get_myself(config)["accountId"]
    except Exception as exc:
        return fail(f"Não foi possível identificar seu usuário no Jira: {exc}", 502)

    # Confere a issue uma vez só, antes de gravar: erro de digitação não deixa
    # metade dos dias lançados.
    try:
        issue = core.issue_details(config, issue_key)
    except Exception as exc:
        return fail(f"Issue {issue_key} não encontrada: {exc}", 404)

    created, failed = [], []
    jira_url = config["JIRA_URL"]

    # Em série de propósito: o Tempo empilha o startTime do dia a partir do que
    # já existe. Em paralelo, dois lançamentos do mesmo dia nasceriam sobrepostos.
    for raw in entries:
        day = (raw.get("date") or "").strip()
        duration = (raw.get("duration") or "").strip()
        if not day or core.parse_jira_time(duration) <= 0:
            failed.append({"date": day, "duration": duration, "error": "Duração inválida."})
            continue
        try:
            result = core.add_worklog(
                config, issue["key"], day, duration, comment, account_id=account_id
            )
            created.append(
                {
                    "id": str(result["worklog_id"]),
                    "date": day,
                    "key": result["issue_key"],
                    "summary": result["issue_summary"],
                    "seconds": result["seconds"],
                    "hours": round(result["seconds"] / 3600.0, 4),
                    "comment": comment,
                    "url": f"{jira_url}/browse/{result['issue_key']}",
                }
            )
        except Exception as exc:
            failed.append({"date": day, "duration": duration, "error": str(exc)})

    status = 200 if created else 502
    return (
        jsonify(
            {
                "ok": bool(created),
                "created": created,
                "failed": failed,
                "issue": {"key": issue["key"], "summary": issue["summary"]},
            }
        ),
        status,
    )


@app.delete("/api/worklogs/<worklog_id>")
def api_delete_worklog(worklog_id):
    config = require_config()
    issue_key = request.args.get("issue_key")
    core.delete_worklog(config, worklog_id, issue_key)
    return jsonify({"ok": True, "deleted": worklog_id})


# --------------------------------------------------------------------------- #
# API — issues
# --------------------------------------------------------------------------- #

@app.get("/api/issues")
def api_issues():
    config = require_config()
    refresh = request.args.get("refresh") == "1"
    cache_key = (config["JIRA_URL"], PROJECT_KEY)

    issues = None if refresh else ISSUES_CACHE.get(cache_key)
    if issues is None:
        # Somente leitura: esta rota nunca escreve no Jira nem no Tempo.
        issues = core.get_all_project_issues(config, PROJECT_KEY)
        ISSUES_CACHE.set(cache_key, issues)

    return jsonify(
        {
            "ok": True,
            "project_key": PROJECT_KEY,
            "project_name": PROJECT_NAME,
            "cached": not refresh,
            "issues": issues,
        }
    )


@app.get("/api/issues/suggest")
def api_issue_suggest():
    """Sugestões para o campo de issue do formulário de lançamento."""
    config = require_config()
    term = (request.args.get("q") or "").strip()
    if len(term) < 2:
        return jsonify({"ok": True, "results": []})

    def shrink(issue):
        return {
            "key": issue["key"],
            "summary": issue["summary"],
            "status": issue["status"],
            "status_category": issue["status_category"],
            "pedido": issue["pedido"],
        }

    # Caminho rápido: a aba de issues já carregou a lista, então filtra em memória.
    cached = ISSUES_CACHE.get((config["JIRA_URL"], PROJECT_KEY))
    if cached:
        lowered = term.lower()
        results = [
            shrink(issue)
            for issue in cached
            if lowered in f"{issue['key']} {issue['summary']} {issue.get('pedido') or ''}".lower()
        ]
        if results:
            return jsonify({"ok": True, "results": results[:8]})

    # Cache frio: uma busca dirigida no Jira, em vez de baixar o projeto inteiro.
    # O `~` do JQL casa palavra inteira, e aqui o termo é sempre um começo de
    # palavra ("tribut", "6922"), então o curinga é obrigatório.
    safe = core.escape_jql(term)
    clauses = [f'summary ~ "{safe}*"', f'description ~ "{safe}*"']
    if "-" in term:
        clauses.append(f'key = "{safe}"')
    jql = (
        f'project = "{core.escape_jql(PROJECT_KEY)}" '
        f"AND ({' OR '.join(clauses)}) ORDER BY updated DESC"
    )
    try:
        found = core.search_jql(config, jql, core.ISSUE_FIELDS, max_results=8)
        results = [shrink(core.normalize_issue(config, issue)) for issue in found[:8]]
    except Exception:
        results = []

    return jsonify({"ok": True, "results": results})


# --------------------------------------------------------------------------- #
# Inicialização
# --------------------------------------------------------------------------- #

def free_port(preferred):
    """Usa a porta preferida; se estiver ocupada, pega a próxima livre.

    Sem ``SO_REUSEADDR`` de propósito: no Windows ele permite ligar numa porta
    que já tem dono, e aí o teste daria "livre" para uma porta ocupada — dois
    servidores no mesmo número, um roubando as conexões do outro.
    """
    # Vizinhas antes de uma porta qualquer: com a 5000 ocupada (a versão 2, por
    # exemplo), o endereço ainda fica previsível — 5001, 5002 — em vez de mudar
    # a cada abertura e invalidar o favorito do navegador.
    candidates = [preferred] + [preferred + step for step in range(1, 10)] + [0]
    for candidate in candidates:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", candidate))
                return probe.getsockname()[1]
            except OSError:
                continue
    return preferred


def port_file():
    """Onde a instância viva anota em que porta ficou."""
    return os.path.join(credentials.home_dir(), "port")


def remember_port(port):
    try:
        os.makedirs(credentials.home_dir(), exist_ok=True)
        with open(port_file(), "w", encoding="utf-8") as file:
            file.write(str(port))
    except OSError:
        pass


def remembered_port():
    try:
        with open(port_file(), "r", encoding="utf-8") as file:
            return int(file.read().strip())
    except (OSError, ValueError):
        return None


def already_running(port):
    """Já existe um tracker *desta mesma pasta* vivo nesta porta?

    Rodando sem console, clicar duas vezes no atalho não dá nenhum sinal de que
    o programa já está aberto. Em vez de subir um segundo processo (e um segundo
    ícone na bandeja), a nova instância só traz a tela para frente e sai.

    A pasta importa: se quem está no ar é uma cópia antiga do projeto, ceder a
    vez a ela significaria nunca ver a versão nova. Nesse caso esta instância
    sobe do mesmo jeito, numa porta livre.
    """
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/ping", timeout=1.5
        ) as response:
            data = json.loads(response.read(2000).decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return False
    if data.get("app") != "jira-time-tracker":
        return False
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normcase(data.get("root") or "") == os.path.normcase(here)


def setup_logging():
    """Sem console, erro precisa ir para arquivo ou se perde."""
    log_path = os.path.join(credentials.home_dir(), "tracker.log")
    try:
        os.makedirs(credentials.home_dir(), exist_ok=True)
        handler = RotatingFileHandler(
            log_path, maxBytes=512 * 1024, backupCount=2, encoding="utf-8"
        )
    except OSError:
        return None
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)
    app.logger.setLevel(logging.INFO)
    return log_path


def main():
    setup_logging()

    # Segunda instância: só abre a tela da que já está rodando. A porta anotada
    # vem primeiro porque a 5000 pode estar com outro programa (a versão 2, por
    # exemplo) — e aí a instância viva está em outro número.
    for candidate in (remembered_port(), DEFAULT_PORT):
        if candidate and already_running(candidate):
            webbrowser.open(f"http://127.0.0.1:{candidate}")
            return

    port = free_port(DEFAULT_PORT)
    remember_port(port)
    url = f"http://127.0.0.1:{port}"
    open_browser = os.getenv("NO_BROWSER") != "1"
    app.logger.info("Jira Time Tracker 3 em %s", url)

    # Caminho normal no Windows: nenhuma janela, só o ícone na bandeja.
    if os.getenv("JTT_NO_TRAY") != "1" and tray.available():
        tray.run(
            app,
            port,
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets"),
            open_browser=open_browser,
        )
        return

    # Reserva (pystray ausente, ou execução pelo terminal de propósito).
    print("  Jira Time Tracker 3")
    print(f"  {url}")
    print("  Feche esta janela para encerrar.\n")
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
