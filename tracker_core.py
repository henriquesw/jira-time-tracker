"""Núcleo de acesso a Jira e Tempo.

Toda conversa com as duas APIs passa por aqui — inclusive o empilhamento de
``startTime``, que evita worklogs sobrepostos no mesmo dia.
"""

import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from urllib.parse import urlparse, parse_qs

import requests

import credentials

TEMPO_API_URL = "https://api.tempo.io/4"
REQUEST_TIMEOUT = 30
WORKDAY_SECONDS = 8 * 3600
DEFAULT_DAY_START = 9 * 3600

# Alguns lançadores locais injetam um proxy bloqueado (por exemplo 127.0.0.1:9).
# Jira e Tempo são HTTPS públicos, então a conexão é direta por padrão.
TRUST_ENV = os.getenv("USE_SYSTEM_PROXY", "").lower() in {"1", "true", "yes"}

# Quantas chamadas simultaneas o tracker faz ao Jira/Tempo. O gargalo da tela
# sempre foi latencia de rede, nao processamento: resolver 40 issues em serie
# custa 40 round-trips. Oito de cada vez derruba isso sem irritar o rate limit.
MAX_PARALLEL_REQUESTS = int(os.getenv("JIRA_TRACKER_PARALLEL", "8"))

_local = threading.local()


def _session():
    """Uma requests.Session por thread.

    A Session reaproveita a conexao TCP/TLS (ganho grande por chamada), mas nao
    e thread-safe. Uma por thread da os dois beneficios.
    """
    session = getattr(_local, "session", None)
    if session is None:
        session = requests.Session()
        session.trust_env = TRUST_ENV
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=MAX_PARALLEL_REQUESTS,
            pool_maxsize=MAX_PARALLEL_REQUESTS * 2,
        )
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        _local.session = session
    return session


# Compatibilidade com quem importava API_SESSION.
API_SESSION = _session()


class _TTLCache:
    """Cache com validade, para dados de issue que quase nao mudam."""

    def __init__(self, ttl_seconds):
        self.ttl = ttl_seconds
        self._data = {}
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            value, saved_at = item
            if time.time() - saved_at > self.ttl:
                self._data.pop(key, None)
                return None
            return value

    def set(self, key, value):
        with self._lock:
            self._data[key] = (value, time.time())

    def clear(self):
        with self._lock:
            self._data.clear()


ISSUE_CACHE = _TTLCache(ttl_seconds=int(os.getenv("JIRA_TRACKER_CACHE_TTL", "600")))


def parallel_map(function, items):
    """Aplica function a items em paralelo, preservando a ordem."""
    items = list(items)
    if not items:
        return []
    if len(items) == 1:
        return [function(items[0])]
    workers = min(MAX_PARALLEL_REQUESTS, len(items))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(function, items))


class ApiError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# Configuração
# --------------------------------------------------------------------------- #

def load_config():
    return credentials.load_config(os.path.dirname(os.path.abspath(__file__)))


def jira_configured(config):
    return credentials.is_configured(config)


def uses_tempo(config):
    return credentials.uses_tempo(config)


# --------------------------------------------------------------------------- #
# Conversões
# --------------------------------------------------------------------------- #

UNIT_SECONDS = {"w": 5 * 8 * 3600, "d": 8 * 3600, "h": 3600, "m": 60}

# Um número, e a unidade logo depois quando houver. A unidade é opcional de
# propósito: '1h30' é como se escreve uma hora e meia no dia a dia, e exigir o
# 'm' fazia os 30 minutos sumirem sem aviso nenhum.
TIME_TOKEN = re.compile(r"(\d+(?:\.\d+)?)\s*([wdhm])?")


def parse_jira_time(time_str):
    """Converte '2h30m', '1h30', '1.5h', '90m' ou '2' (horas) em segundos.

    Número sem unidade vale minutos quando vem depois de outro valor ('1h30'),
    e horas quando está sozinho ('8'). É a leitura que as duas formas pedem.
    """
    time_str = str(time_str).strip().lower().replace(",", ".")
    if not time_str:
        return 0

    matches = [
        (value, unit)
        for value, unit in TIME_TOKEN.findall(time_str)
        if value
    ]
    if not matches:
        return 0

    seconds = 0.0
    for index, (value, unit) in enumerate(matches):
        value = float(value)
        if not unit:
            unit = "m" if index else "h"
        seconds += value * UNIT_SECONDS[unit]
    return int(seconds)


def start_time_to_seconds(value):
    if isinstance(value, (int, float)):
        return int(value)
    try:
        parts = str(value or "09:00:00").split(":")
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except (ValueError, IndexError):
        return DEFAULT_DAY_START


def seconds_to_start_time(seconds):
    # Tempo aceita HH:mm:ss. Mantém o horário válido se o dia passar de 24h.
    seconds = max(0, min(int(seconds), 23 * 3600 + 59 * 60 + 59))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def format_duration(seconds):
    if seconds is None:
        return "Não estimado"
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"


def format_hours(seconds):
    """'6h45' — formato compacto usado nos totais da tela."""
    seconds = int(seconds or 0)
    hours, minutes = divmod(seconds // 60, 60)
    return f"{hours}h{minutes:02d}"


# Nós do ADF que são blocos: cada um termina uma linha. O resto é texto
# corrido dentro da mesma linha.
ADF_BLOCKS = {
    "paragraph", "heading", "blockquote", "codeBlock", "listItem",
    "panel", "rule", "mediaSingle", "taskItem", "decisionItem",
}


def adf_to_text(value):
    """Texto puro do Atlassian Document Format, com as quebras de linha.

    Juntar tudo com espaço, como antes, transformava um comentário de três
    linhas numa linha só na volta.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(adf_to_text(item) for item in value)
    if not isinstance(value, dict):
        return ""

    kind = value.get("type")
    if kind == "text":
        return value.get("text", "")
    if kind == "hardBreak":
        return "\n"

    inner = adf_to_text(value.get("content", []))
    return inner + "\n" if kind in ADF_BLOCKS else inner


def text_to_adf(text):
    """Documento ADF a partir de texto puro: uma linha, um parágrafo.

    Nó de texto do ADF não aceita ``\n`` — mandar o texto cru fazia o Jira
    receber o comentário espremido numa linha (ou recusar o documento).
    """
    lines = str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    paragraphs = []
    for line in lines:
        paragraph = {"type": "paragraph", "content": []}
        if line:
            paragraph["content"].append({"type": "text", "text": line})
        paragraphs.append(paragraph)
    return {"type": "doc", "version": 1, "content": paragraphs}


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #

def response_error(response, service):
    try:
        body = response.json()
        message = (
            body.get("message")
            or body.get("errorMessage")
            or body.get("errorMessages")
            or body.get("errors")
            or body
        )
    except (ValueError, AttributeError):
        message = response.text[:500]
    return ApiError(f"{service} respondeu HTTP {response.status_code}: {message}")


def jira_request(config, method, path, params=None, payload=None):
    response = _session().request(
        method,
        f"{config['JIRA_URL']}{path}",
        auth=(config["JIRA_EMAIL"], config["JIRA_API_TOKEN"]),
        params=params,
        json=payload,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        timeout=REQUEST_TIMEOUT,
    )
    if not response.ok:
        raise response_error(response, "Jira")
    if response.status_code == 204 or not response.content:
        return None
    return response.json()


def jira_get(config, path, params=None):
    return jira_request(config, "GET", path, params=params)


def tempo_request(config, method, path, params=None, payload=None):
    response = _session().request(
        method,
        f"{TEMPO_API_URL}{path}",
        headers={
            "Authorization": f"Bearer {config['TEMPO_API_TOKEN']}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        params=params,
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )
    if not response.ok:
        raise response_error(response, "Tempo")
    if response.status_code == 204 or not response.content:
        return None
    return response.json()


def get_myself(config):
    return jira_get(config, "/rest/api/3/myself")


# --------------------------------------------------------------------------- #
# Leitura de worklogs
# --------------------------------------------------------------------------- #

def get_tempo_worklogs(config, account_id, start_date, end_date):
    worklogs = []
    offset = 0
    limit = 500

    while True:
        data = tempo_request(
            config,
            "GET",
            f"/worklogs/user/{account_id}",
            params={"from": start_date, "to": end_date, "offset": offset, "limit": limit},
        )
        page = data.get("results", [])
        worklogs.extend(page)
        metadata = data.get("metadata", {})

        next_url = metadata.get("next")
        if not next_url or not page:
            break

        next_offset = parse_qs(urlparse(next_url).query).get("offset", [])
        offset = int(next_offset[0]) if next_offset else offset + len(page)

    return worklogs


def search_jql(config, jql, fields, max_results=100):
    """Busca paginada usando a API nova de busca do Jira."""
    issues = []
    next_page_token = None
    while True:
        params = {"jql": jql, "fields": fields, "maxResults": max_results}
        if next_page_token:
            params["nextPageToken"] = next_page_token
        data = jira_get(config, "/rest/api/3/search/jql", params=params)
        issues.extend(data.get("issues", []))
        if data.get("isLast", True):
            break
        next_page_token = data.get("nextPageToken")
        if not next_page_token:
            break
    return issues


def get_jira_worklog_issues(config, start_date, end_date):
    jql = (
        "worklogAuthor = currentUser() "
        f'AND worklogDate >= "{start_date}" AND worklogDate <= "{end_date}" '
        "ORDER BY updated DESC"
    )
    return search_jql(config, jql, "summary")


def get_issue_worklogs(config, issue_key):
    worklogs = []
    start_at = 0
    while True:
        data = jira_get(
            config,
            f"/rest/api/3/issue/{issue_key}/worklog",
            params={"startAt": start_at, "maxResults": 100},
        )
        page = data.get("worklogs", [])
        worklogs.extend(page)
        start_at += len(page)
        if not page or start_at >= data.get("total", start_at):
            break
    return worklogs


def get_jira_native_worklogs(config, account_id, start_date, end_date):
    # Uma chamada de worklog por issue, todas ao mesmo tempo.
    issues = get_jira_worklog_issues(config, start_date, end_date)
    logs_by_issue = parallel_map(
        lambda issue: get_issue_worklogs(config, issue["key"]), issues
    )

    result = []
    for issue, issue_logs in zip(issues, logs_by_issue):
        issue_key = issue["key"]
        summary = issue.get("fields", {}).get("summary", "")
        for log in issue_logs:
            if log.get("author", {}).get("accountId") != account_id:
                continue
            started = (log.get("started") or "")[:10]
            if not (start_date <= started <= end_date):
                continue
            result.append(
                {
                    "id": log.get("id"),
                    "key": issue_key,
                    "summary": summary,
                    "date": started,
                    "started": log.get("started"),
                    "timeSpentSeconds": log.get("timeSpentSeconds", 0),
                    "description": adf_to_text(log.get("comment", "")).strip(),
                }
            )
    return result


def latest_native_end_time(config, account_id, day):
    logs = get_jira_native_worklogs(config, account_id, day, day)
    latest = None
    for log in logs:
        started = log.get("started")
        if not started:
            continue
        try:
            value = datetime.strptime(started, "%Y-%m-%dT%H:%M:%S.%f%z")
        except ValueError:
            try:
                value = datetime.fromisoformat(started)
            except ValueError:
                continue
        end = value + timedelta(seconds=int(log.get("timeSpentSeconds", 0)))
        if latest is None or end > latest:
            latest = end
    if latest is None:
        local_tz = datetime.now().astimezone().tzinfo
        latest = datetime.strptime(f"{day} 09:00:00", "%Y-%m-%d %H:%M:%S")
        latest = latest.replace(tzinfo=local_tz)
    return latest


def issue_details(config, issue_reference, use_cache=True):
    """Id, chave e resumo de uma issue; em cache por padrao.

    Resumo de issue muda raramente e a mesma issue aparece em varios dias do
    periodo. Sem cache, cada repeticao era mais um round-trip.
    """
    cache_key = (config.get("JIRA_URL"), str(issue_reference).upper())
    if use_cache:
        cached = ISSUE_CACHE.get(cache_key)
        if cached is not None:
            return cached

    data = jira_get(
        config, f"/rest/api/3/issue/{issue_reference}", params={"fields": "summary"}
    )
    detail = {
        "id": int(data["id"]),
        "key": data["key"],
        "summary": data.get("fields", {}).get("summary", ""),
    }
    for key in (cache_key, (config.get("JIRA_URL"), str(detail["id"])),
                (config.get("JIRA_URL"), detail["key"].upper())):
        ISSUE_CACHE.set(key, detail)
    return detail


def resolve_issues(config, worklogs):
    """Chave e resumo das issues citadas nos worklogs, resolvidas em paralelo."""
    pending = []
    seen = set()
    for worklog in worklogs:
        issue = worklog.get("issue", {})
        issue_id = issue.get("id")
        issue_key = issue.get("key")
        reference = issue_key or issue_id
        if reference is None or str(reference) in seen:
            continue
        seen.add(str(reference))
        pending.append((issue_id, issue_key, reference))

    def resolve(item):
        issue_id, issue_key, reference = item
        try:
            return issue_id, issue_details(config, reference)
        except Exception as exc:
            print(f"Não foi possível resolver a issue {reference}: {exc}")
            return issue_id, {
                "id": issue_id,
                "key": issue_key or f"ID-{issue_id}",
                "summary": "Resumo indisponível",
            }

    details = {}
    for issue_id, resolved in parallel_map(resolve, pending):
        details[str(issue_id)] = resolved
        details[str(resolved["id"])] = resolved
        details[resolved["key"]] = resolved
    return details


def worklogs_normalized(config, start_date, end_date, account_id=None):
    """Worklogs do período num formato único, independente do backend."""
    if account_id is None:
        account_id = get_myself(config)["accountId"]

    entries = []
    if uses_tempo(config):
        raw = get_tempo_worklogs(config, account_id, start_date, end_date)
        issues_by_id = resolve_issues(config, raw)
        for log in raw:
            issue = log.get("issue", {})
            detail = issues_by_id.get(str(issue.get("id"))) or issues_by_id.get(
                issue.get("key")
            ) or {"key": issue.get("key") or f"ID-{issue.get('id')}", "summary": ""}
            entries.append(
                {
                    "id": log["tempoWorklogId"],
                    "key": detail["key"],
                    "summary": detail["summary"],
                    "date": log.get("startDate"),
                    "seconds": log.get("timeSpentSeconds", 0),
                    "description": log.get("description", ""),
                }
            )
    else:
        for log in get_jira_native_worklogs(config, account_id, start_date, end_date):
            entries.append(
                {
                    "id": log["id"],
                    "key": log["key"],
                    "summary": log["summary"],
                    "date": log["date"],
                    "seconds": log.get("timeSpentSeconds", 0),
                    "description": log.get("description", ""),
                }
            )
    return entries


def empty_time_data(start_date, end_date):
    result = {}
    current = datetime.strptime(start_date, "%Y-%m-%d")
    last = datetime.strptime(end_date, "%Y-%m-%d")
    while current <= last:
        result[current.strftime("%Y-%m-%d")] = {
            "hours": 0.0,
            "logs": [],
            "day_name": current.strftime("%A"),
        }
        current += timedelta(days=1)
    return result


# --------------------------------------------------------------------------- #
# Dias pendentes
# --------------------------------------------------------------------------- #

def pending_days(config, days_back=14, expected_seconds=WORKDAY_SECONDS, account_id=None):
    """Dias úteis do período com menos horas lançadas que o esperado."""
    end = date.today()
    start = end - timedelta(days=days_back)
    entries = worklogs_normalized(
        config, start.isoformat(), end.isoformat(), account_id=account_id
    )

    totals = {}
    for entry in entries:
        totals[entry["date"]] = totals.get(entry["date"], 0) + int(entry["seconds"] or 0)

    result = []
    current = start
    while current <= end:
        if current.weekday() < 5:  # segunda a sexta
            logged = totals.get(current.isoformat(), 0)
            result.append(
                {
                    "date": current.isoformat(),
                    "weekday": current.strftime("%A"),
                    "logged_seconds": logged,
                    "expected_seconds": expected_seconds,
                    "missing_seconds": max(expected_seconds - logged, 0),
                    "complete": logged >= expected_seconds,
                }
            )
        current += timedelta(days=1)
    return result


# --------------------------------------------------------------------------- #
# Pedido -> issue
# --------------------------------------------------------------------------- #

PEDIDO_PATTERN = re.compile(r"\b(\d{5,8})\b")

ISSUE_FIELDS = (
    "summary,status,assignee,description,duedate,timetracking,timeoriginalestimate,"
    "timeestimate,timespent,issuetype,priority,created,updated,parent"
)


def normalize_issue(config, issue):
    fields = issue.get("fields", {})
    original = fields.get("timeoriginalestimate")
    remaining = fields.get("timeestimate")
    spent = fields.get("timespent") or 0
    if remaining is None and original is not None:
        remaining = max(int(original) - int(spent), 0)

    description = adf_to_text(fields.get("description")).strip()
    summary = fields.get("summary") or ""
    due = fields.get("duedate")

    days_left = None
    if due:
        try:
            days_left = (date.fromisoformat(due) - date.today()).days
        except ValueError:
            days_left = None

    return {
        "id": issue.get("id"),
        "key": issue.get("key"),
        "summary": summary,
        "status": (fields.get("status") or {}).get("name", "Sem status"),
        "status_category": (
            (fields.get("status") or {}).get("statusCategory") or {}
        ).get("key", "undefined"),
        "issue_type": (fields.get("issuetype") or {}).get("name", "Issue"),
        "priority": (fields.get("priority") or {}).get("name", "Sem prioridade"),
        "assignee": (fields.get("assignee") or {}).get("displayName", "Não atribuído"),
        "description": description,
        "pedido": extract_pedido(f"{summary} {description}"),
        "due_date": due,
        "days_left": days_left,
        "original_seconds": original,
        "remaining_seconds": remaining,
        "spent_seconds": spent,
        "original": format_duration(original),
        "remaining": format_duration(remaining),
        "spent": format_duration(spent),
        "created": (fields.get("created") or "")[:10],
        "updated": (fields.get("updated") or "")[:10],
        "url": f"{config['JIRA_URL']}/browse/{issue.get('key')}",
    }


def extract_pedido(text):
    """Pega o número do pedido no início do texto.

    Funciona tanto no título do card do Planner ('688135 _DEV_FIN_72_HORAS')
    quanto no título da issue do Jira ('688135 - [RT] Matriz Tributária').
    """
    if not text:
        return None
    match = PEDIDO_PATTERN.search(text.strip())
    return match.group(1) if match else None


def escape_jql(value):
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def find_issues_by_pedido(config, pedido, project_key="PEDIDO"):
    """Acha a(s) issue(s) cujo número de pedido bate.

    O número vem do card do Planner, que é onde o usuário enxerga quais pedidos
    estão no nome dele. Aqui ele é procurado no título da issue do Jira. A busca
    textual serve de filtro amplo e a conferência exata é feita localmente —
    ``~`` pode trazer vizinhos por tokenização.
    """
    pedido = str(pedido).strip()
    if not pedido:
        return []

    safe = escape_jql(pedido)
    jql = (
        f'project = "{escape_jql(project_key)}" '
        f'AND (description ~ "{safe}" OR summary ~ "{safe}") '
        "ORDER BY updated DESC"
    )
    issues = [normalize_issue(config, issue) for issue in search_jql(config, jql, ISSUE_FIELDS)]

    exact = [issue for issue in issues if issue["pedido"] == pedido]
    return exact or issues


def get_my_issues(config, project_key="PEDIDO", status_category=None, limit=100):
    """Issues atribuídas a mim, opcionalmente filtradas por categoria de status."""
    clauses = [f'project = "{escape_jql(project_key)}"', "assignee = currentUser()"]
    if status_category:
        clauses.append(f'statusCategory = "{escape_jql(status_category)}"')
    jql = " AND ".join(clauses) + " ORDER BY duedate ASC, updated DESC"
    issues = search_jql(config, jql, ISSUE_FIELDS, max_results=limit)
    return [normalize_issue(config, issue) for issue in issues]


def get_issues(config, issue_keys):
    if not issue_keys:
        return []
    keys = ", ".join(f'"{escape_jql(key)}"' for key in issue_keys)
    jql = f"key in ({keys})"
    return [normalize_issue(config, issue) for issue in search_jql(config, jql, ISSUE_FIELDS)]


def get_all_project_issues(config, project_key):
    jql = f'project = "{escape_jql(project_key)}" ORDER BY updated DESC'
    issues = search_jql(config, jql, ISSUE_FIELDS)
    return [normalize_issue(config, issue) for issue in issues]


# --------------------------------------------------------------------------- #
# Escrita: worklogs
# --------------------------------------------------------------------------- #

def _next_start_seconds(config, account_id, day):
    """Onde o próximo worklog do dia deve começar, para não sobrepor."""
    logs = get_tempo_worklogs(config, account_id, day, day)
    return max(
        (
            start_time_to_seconds(log.get("startTime")) + int(log.get("timeSpentSeconds", 0))
            for log in logs
        ),
        default=DEFAULT_DAY_START,
    )


def add_worklog(config, issue_key, day, duration, description="", account_id=None):
    """Lança horas numa issue. Uma entrada por chamada.

    ``duration`` aceita '2h30m', '1.5h', '90m' ou um número de horas.
    """
    seconds = parse_jira_time(duration)
    if seconds <= 0:
        raise ApiError(f"Duração inválida: {duration!r}")

    if account_id is None:
        account_id = get_myself(config)["accountId"]
    issue = issue_details(config, str(issue_key).strip().upper())

    if uses_tempo(config):
        start_seconds = _next_start_seconds(config, account_id, day)
        payload = {
            "issueId": issue["id"],
            "timeSpentSeconds": seconds,
            "billableSeconds": seconds,
            "startDate": day,
            "startTime": seconds_to_start_time(start_seconds),
            "authorAccountId": account_id,
            "description": description,
        }
        created = tempo_request(config, "POST", "/worklogs", payload=payload)
        worklog_id = (created or {}).get("tempoWorklogId")
    else:
        started_at = latest_native_end_time(config, account_id, day)
        payload = {
            "timeSpentSeconds": seconds,
            "started": started_at.strftime("%Y-%m-%dT%H:%M:%S.000%z"),
        }
        if description:
            payload["comment"] = text_to_adf(description)
        created = jira_request(
            config, "POST", f"/rest/api/3/issue/{issue['key']}/worklog", payload=payload
        )
        worklog_id = (created or {}).get("id")

    return {
        "worklog_id": worklog_id,
        "issue_key": issue["key"],
        "issue_summary": issue["summary"],
        "date": day,
        "seconds": seconds,
        "duration": format_duration(seconds),
        "description": description,
        "backend": "Tempo" if uses_tempo(config) else "Jira nativo",
    }


def delete_worklog(config, worklog_id, issue_key=None):
    if uses_tempo(config):
        tempo_request(config, "DELETE", f"/worklogs/{worklog_id}")
    else:
        if not issue_key:
            raise ApiError("No modo Jira nativo é preciso informar a issue.")
        jira_request(config, "DELETE", f"/rest/api/3/issue/{issue_key}/worklog/{worklog_id}")
    return {"deleted": worklog_id}


# --------------------------------------------------------------------------- #
# Escrita: transições
# --------------------------------------------------------------------------- #

def get_transitions(config, issue_key):
    data = jira_get(config, f"/rest/api/3/issue/{issue_key}/transitions")
    return [
        {
            "id": transition.get("id"),
            "name": transition.get("name"),
            "to_status": (transition.get("to") or {}).get("name"),
        }
        for transition in (data or {}).get("transitions", [])
    ]


def transition_issue(config, issue_key, transition):
    """Move o card. ``transition`` pode ser o id ou o nome da transição."""
    issue_key = str(issue_key).strip().upper()
    available = get_transitions(config, issue_key)
    if not available:
        raise ApiError(f"{issue_key} não tem transições disponíveis para você.")

    wanted = str(transition).strip().lower()
    match = next(
        (
            item
            for item in available
            if str(item["id"]) == wanted
            or (item["name"] or "").lower() == wanted
            or (item["to_status"] or "").lower() == wanted
        ),
        None,
    )
    if match is None:
        names = ", ".join(f"{item['name']} → {item['to_status']}" for item in available)
        raise ApiError(f"Transição {transition!r} não existe em {issue_key}. Disponíveis: {names}")

    jira_request(
        config,
        "POST",
        f"/rest/api/3/issue/{issue_key}/transitions",
        payload={"transition": {"id": str(match["id"])}},
    )
    return {"issue_key": issue_key, "transition": match["name"], "new_status": match["to_status"]}


def add_comment(config, issue_key, text):
    jira_request(
        config,
        "POST",
        f"/rest/api/3/issue/{str(issue_key).strip().upper()}/comment",
        payload={
            "body": {
                "type": "doc",
                "version": 1,
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}],
            }
        },
    )
    return {"issue_key": issue_key, "comment": text}


# --------------------------------------------------------------------------- #
# Regras aprendidas (atividades sem pedido)
# --------------------------------------------------------------------------- #

def load_rules():
    path = credentials.rules_path()
    if not os.path.exists(path):
        return {"regras": []}
    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
        data.setdefault("regras", [])
        return data
    except (OSError, json.JSONDecodeError):
        return {"regras": []}


def save_rules(data):
    os.makedirs(credentials.home_dir(), exist_ok=True)
    path = credentials.rules_path()
    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)
    return path


def match_rule(title):
    """Acha a regra aprendida para um evento de calendário, se houver."""
    title_lower = (title or "").lower()
    for rule in load_rules().get("regras", []):
        pattern = (rule.get("padrao_evento") or "").lower()
        if pattern and pattern in title_lower:
            return rule
    return None
