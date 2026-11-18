"""Resolução de credenciais fora da pasta do projeto.

Os tokens do Jira e do Tempo nunca devem ficar na pasta do projeto: ela é
sincronizada pelo OneDrive corporativo e pode ser compartilhada com colegas.
Este módulo procura as credenciais nesta ordem:

1. Variáveis de ambiente (útil para CI ou execução pontual);
2. Windows Credential Manager, via keyring — é onde os tokens ficam;
3. ``~/.jira-time-tracker/config.json`` — guarda só URL e e-mail, e serve de
   reserva para os tokens quando o keyring não está disponível;
4. ``config-tempo.json`` / ``config.json`` na pasta do projeto — formato antigo,
   mantido apenas para não quebrar quem ainda não migrou. Emite aviso.

Para sair do passo 4, basta salvar de novo na tela de configuração: os tokens
vão para o Credential Manager e os arquivos antigos podem ser apagados.
"""

import json
import os
import subprocess
import sys

SECRET_KEYS = ("JIRA_API_TOKEN", "TEMPO_API_TOKEN")
PLAIN_KEYS = ("JIRA_URL", "JIRA_EMAIL")
ALL_KEYS = PLAIN_KEYS + SECRET_KEYS

KEYRING_SERVICE = "jira-time-tracker"

LEGACY_FILES = ("config-tempo.json", "config.json")

_legacy_warning_shown = False


def home_dir():
    """Pasta de dados do usuário, fora do OneDrive."""
    override = os.getenv("JIRA_TRACKER_HOME")
    if override:
        return os.path.abspath(override)
    return os.path.join(os.path.expanduser("~"), ".jira-time-tracker")


def config_path():
    return os.path.join(home_dir(), "config.json")


def rules_path():
    return os.path.join(home_dir(), "regras-apontamento.json")


def _read_json(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Não foi possível ler {path}: {exc}", file=sys.stderr)
        return {}


def _keyring():
    try:
        import keyring
    except ImportError:
        return None
    return keyring


def _keyring_get(key):
    keyring = _keyring()
    if keyring is None:
        return None
    try:
        return keyring.get_password(KEYRING_SERVICE, key)
    except Exception as exc:  # backend ausente ou cofre bloqueado
        print(f"Keyring indisponível ({exc}); usando arquivo.", file=sys.stderr)
        return None


def _keyring_set(key, value):
    keyring = _keyring()
    if keyring is None:
        return False
    try:
        keyring.set_password(KEYRING_SERVICE, key, value)
        return True
    except Exception as exc:
        print(f"Não foi possível gravar no keyring ({exc}).", file=sys.stderr)
        return False


def _keyring_delete(key):
    keyring = _keyring()
    if keyring is None:
        return
    try:
        keyring.delete_password(KEYRING_SERVICE, key)
    except Exception:
        pass


def legacy_config(project_dir=None):
    """Lê os arquivos antigos da pasta do projeto, se ainda existirem."""
    base = project_dir or os.path.dirname(os.path.abspath(__file__))
    merged = {}
    for name in reversed(LEGACY_FILES):  # config-tempo.json tem prioridade
        merged.update(
            {k: v for k, v in _read_json(os.path.join(base, name)).items() if v}
        )
    return {key: merged.get(key, "") for key in ALL_KEYS}


def _warn_legacy_once(found_keys):
    global _legacy_warning_shown
    if _legacy_warning_shown or not found_keys:
        return
    _legacy_warning_shown = True
    print(
        "\n  AVISO: credenciais ainda estão na pasta do projeto "
        f"({', '.join(sorted(found_keys))}).\n"
        "  Essa pasta pode ser sincronizada pelo OneDrive. Salve de novo na\n"
        "  tela de configuração e apague os arquivos antigos.\n",
        file=sys.stderr,
    )


def load_config(project_dir=None):
    """Monta a configuração a partir da primeira fonte que tiver cada valor."""
    stored = _read_json(config_path())
    legacy = legacy_config(project_dir)
    legacy_used = set()

    result = {}
    for key in ALL_KEYS:
        value = os.getenv(key)
        if not value and key in SECRET_KEYS:
            value = _keyring_get(key)
        if not value:
            value = stored.get(key)
        if not value and legacy.get(key):
            value = legacy[key]
            legacy_used.add(key)
        result[key] = (value or "").strip()

    _warn_legacy_once(legacy_used & set(SECRET_KEYS))
    return result


def save_config(values):
    """Grava tokens no Credential Manager e o resto no arquivo do usuário.

    Retorna a lista de destinos usados, para a interface poder informar onde os
    dados foram parar.
    """
    destinations = []
    os.makedirs(home_dir(), exist_ok=True)
    _restrict_permissions(home_dir())

    stored = _read_json(config_path())
    for key in PLAIN_KEYS:
        if key in values:
            stored[key] = (values[key] or "").strip().rstrip("/") if key == "JIRA_URL" \
                else (values[key] or "").strip()

    file_secrets = {}
    for key in SECRET_KEYS:
        if key not in values:
            continue
        value = (values[key] or "").strip()
        if not value:
            stored.pop(key, None)
            _keyring_delete(key)
            continue
        if _keyring_set(key, value):
            stored.pop(key, None)  # não duplicar em disco
            if "Windows Credential Manager" not in destinations:
                destinations.append("Windows Credential Manager")
        else:
            file_secrets[key] = value

    stored.update(file_secrets)

    try:
        with open(config_path(), "w", encoding="utf-8") as file:
            json.dump(stored, file, indent=4, ensure_ascii=False)
        _restrict_permissions(config_path())
        destinations.append(config_path())
    except OSError as exc:
        print(f"Não foi possível gravar {config_path()}: {exc}", file=sys.stderr)
        return destinations

    return destinations


DEFAULT_PREFS = {
    # Primeiro dia da semana nos filtros "Esta semana" e "Semana passada".
    "week_start": "monday",  # "monday" | "sunday"
    # Dia em que começa o ciclo de fechamento da empresa: 21 faz o filtro de mês
    # ir do dia 21 ao dia 20 do mês seguinte.
    "month_cycle_start": 21,
    # Horas esperadas por dia útil.
    "target_hours": 8,
}


def load_prefs():
    """Preferências da tela. Moram no mesmo config.json, fora do projeto."""
    stored = _read_json(config_path()).get("prefs") or {}
    prefs = dict(DEFAULT_PREFS)
    if stored.get("week_start") in ("monday", "sunday"):
        prefs["week_start"] = stored["week_start"]
    try:
        day = int(stored.get("month_cycle_start", prefs["month_cycle_start"]))
        if 1 <= day <= 28:
            prefs["month_cycle_start"] = day
    except (TypeError, ValueError):
        pass
    try:
        hours = float(stored.get("target_hours", prefs["target_hours"]))
        if 0 < hours <= 24:
            prefs["target_hours"] = hours
    except (TypeError, ValueError):
        pass
    return prefs


def save_prefs(values):
    """Grava só as preferências, sem encostar nas credenciais."""
    prefs = load_prefs()
    prefs.update({k: v for k, v in values.items() if k in DEFAULT_PREFS})

    os.makedirs(home_dir(), exist_ok=True)
    stored = _read_json(config_path())
    stored["prefs"] = prefs
    with open(config_path(), "w", encoding="utf-8") as file:
        json.dump(stored, file, indent=4, ensure_ascii=False)
    _restrict_permissions(config_path())
    return load_prefs()


def _restrict_permissions(path):
    """No Windows, deixa o item acessível somente ao usuário atual."""
    if os.name != "nt":
        try:
            os.chmod(path, 0o700 if os.path.isdir(path) else 0o600)
        except OSError:
            pass
        return
    user = os.getenv("USERNAME")
    if not user:
        return
    grant = f"{user}:(OI)(CI)F" if os.path.isdir(path) else f"{user}:F"
    try:
        subprocess.run(
            ["icacls", path, "/inheritance:r", "/grant:r", grant],
            check=False,
            capture_output=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def is_configured(config):
    return all(config.get(key) for key in ("JIRA_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"))


def uses_tempo(config):
    return bool((config.get("TEMPO_API_TOKEN") or "").strip())


def describe_sources(project_dir=None):
    """Onde cada credencial está sendo lida. Nunca devolve o valor em si."""
    stored = _read_json(config_path())
    legacy = legacy_config(project_dir)
    report = {}
    for key in ALL_KEYS:
        if os.getenv(key):
            report[key] = "variável de ambiente"
        elif key in SECRET_KEYS and _keyring_get(key):
            report[key] = "Windows Credential Manager"
        elif stored.get(key):
            report[key] = config_path()
        elif legacy.get(key):
            report[key] = "pasta do projeto (LEGADO - migre)"
        else:
            report[key] = "não configurado"
    return report
