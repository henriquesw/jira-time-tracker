"""Feriados: um aviso na tela, e nada além disso.

A tabela mora em ``feriados.json``, ao lado deste arquivo, e é do projeto:
pode ser editada, versionada e entregue junto.

O programa não decide nada por causa de um feriado — lançar é permitido, e
quem trabalhou sabe se trabalhou. A marca só diz "este dia é feriado", porque
há time que lança num card do Jira e time que deixa o dia em branco, e os dois
estão certos. A única consequência está na soma do período: um feriado em
branco não é cobrado (ver ``app.py``); com horas lançadas, ele conta como
qualquer outro dia útil.
"""

import json
import logging
import os
import threading
from datetime import date, timedelta

LOGGER = logging.getLogger(__name__)

TABLE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "feriados.json")

_cache = {"mtime": None, "data": None}
_lock = threading.Lock()


def easter(year):
    """Domingo de Páscoa (algoritmo de Meeus/Jones/Butcher, calendário gregoriano).

    Carnaval, Sexta-feira Santa e Corpus Christi são contados a partir dele, o
    que evita ter de listar esses dias ano a ano no JSON.
    """
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    el = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * el) // 451
    month, day = divmod(h + el - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _table():
    """Conteúdo do feriados.json, relido quando o arquivo muda."""
    try:
        mtime = os.path.getmtime(TABLE_PATH)
    except OSError:
        LOGGER.warning("feriados.json não encontrado em %s", TABLE_PATH)
        return {}

    with _lock:
        if _cache["mtime"] == mtime and _cache["data"] is not None:
            return _cache["data"]
        try:
            with open(TABLE_PATH, "r", encoding="utf-8") as file:
                data = json.load(file)
        except (OSError, ValueError):
            # Arquivo quebrado não pode derrubar a tela: sem tabela, nenhum dia
            # é marcado, e o erro fica no log.
            LOGGER.exception("feriados.json inválido; nenhum dia será marcado")
            data = {}
        _cache["mtime"] = mtime
        _cache["data"] = data
        return data


# As listas do JSON, e o escopo que cada uma dá aos seus dias. Separadas por
# origem de propósito: quem não é de São Paulo esvazia duas listas e pronto,
# sem precisar caçar linhas no meio dos feriados nacionais.
SECTIONS = (
    ("nacionais", "nacional"),
    ("estaduais", "estadual"),
    ("municipais", "municipal"),
    ("facultativos", "facultativo"),
    ("extras", ""),
)


def _scope(item, base):
    """O escopo sai da lista em que o dia está: 'estadual' + uf SP -> 'estadual-sp'.

    Um campo ``escopo`` no próprio item manda, para quem quiser escrever outra
    coisa.
    """
    if item.get("escopo"):
        return str(item["escopo"])
    uf = str(item.get("uf") or "").strip().lower()
    if uf and base in ("estadual", "municipal"):
        return f"{base}-{uf}"
    return base


def _when(item, year, sunday):
    """Data do item nesse ano: por 'dia' (MM-DD), 'pascoa' ou 'data' fixa."""
    if "pascoa" in item:
        try:
            return sunday + timedelta(days=int(item["pascoa"]))
        except (TypeError, ValueError):
            return None
    if "dia" in item:
        try:
            month, day = str(item["dia"]).split("-")
            return date(year, int(month), int(day))
        except ValueError:
            return None
    if "data" in item:
        try:
            fixed = date.fromisoformat(str(item["data"]))
        except (TypeError, ValueError):
            return None
        return fixed if fixed.year == year else None
    return None


def _of_year(year):
    """{'2026-12-25': {'nome': ..., 'escopo': ...}} para um ano."""
    table = _table()
    sunday = easter(year)
    found = {}
    for section, base in SECTIONS:
        for item in table.get(section) or []:
            if not isinstance(item, dict):
                continue
            when = _when(item, year, sunday)
            if when is None:
                continue
            found[when.isoformat()] = {
                "nome": item.get("nome") or "Feriado",
                "escopo": _scope(item, base),
            }
    return found


def between(start, end):
    """{'2026-12-25': {'nome', 'escopo'}} dos feriados do período (``date``)."""
    found = {}
    for year in range(start.year, end.year + 1):
        for day, info in _of_year(year).items():
            if start.isoformat() <= day <= end.isoformat():
                found[day] = info
    return found


def check(day):
    """Info do feriado nessa data ISO ('2026-12-25'), ou None."""
    try:
        when = date.fromisoformat(day)
    except (TypeError, ValueError):
        return None
    return _of_year(when.year).get(day)
