/* Jira Time Tracker 3 — cliente.
 *
 * Regra desta versão: a página nunca recarrega. Toda ação é uma chamada à API,
 * o estado em memória é atualizado e só o pedaço afetado da tela é redesenhado.
 * Erro não navega para lugar nenhum: vira um aviso, e o que você digitou
 * continua onde estava.
 *
 * Sem framework e sem build — o navegador recebe um arquivo e pronto.
 */
(function () {
  'use strict';

  var CFG = window.JTT || {};

  /* ------------------------------------------------------------- estado -- */
  var state = {
    view: 'horas',
    start: null,
    end: null,
    preset: 'semana',
    targetHours: (CFG.prefs && CFG.prefs.target_hours) || 8,
    /* Vêm da engrenagem (gravadas em ~/.jira-time-tracker/config.json). */
    prefs: {
      weekStart: (CFG.prefs && CFG.prefs.week_start) || 'monday',
      monthCycleStart: (CFG.prefs && CFG.prefs.month_cycle_start) || 21
    },
    days: [],
    summary: null,
    loading: false,
    requestId: 0,
    issues: null,
    issuesLoading: false,
    issueFilter: { term: '', status: '' }
  };

  /* ----------------------------------------------------------- utilidades - */
  function $(selector, root) { return (root || document).querySelector(selector); }
  function $$(selector, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(selector));
  }

  function el(tag, props, children) {
    var node = document.createElement(tag);
    if (props) {
      Object.keys(props).forEach(function (key) {
        if (key === 'class') node.className = props[key];
        else if (key === 'text') node.textContent = props[key];
        else if (key === 'html') node.innerHTML = props[key];
        else if (key.slice(0, 2) === 'on') node.addEventListener(key.slice(2), props[key]);
        else if (props[key] !== null && props[key] !== undefined) node.setAttribute(key, props[key]);
      });
    }
    (children || []).forEach(function (child) {
      if (child === null || child === undefined || child === false) return;
      node.appendChild(typeof child === 'string' ? document.createTextNode(child) : child);
    });
    return node;
  }

  function icon(path, width) {
    var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', 'currentColor');
    svg.setAttribute('stroke-width', width || '2');
    svg.setAttribute('stroke-linecap', 'round');
    svg.setAttribute('stroke-linejoin', 'round');
    svg.innerHTML = path;
    return svg;
  }

  function iso(date) {
    return date.getFullYear() + '-' +
      String(date.getMonth() + 1).padStart(2, '0') + '-' +
      String(date.getDate()).padStart(2, '0');
  }

  function parseIso(text) {
    var parts = String(text).split('-');
    return new Date(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]));
  }

  function formatDay(isoText) {
    var date = parseIso(isoText);
    return String(date.getDate()).padStart(2, '0') + '/' +
      String(date.getMonth() + 1).padStart(2, '0');
  }

  /* Horas no formato curto do time: 6h45. */
  function formatHours(hours) {
    var totalMinutes = Math.round((hours || 0) * 60);
    var sign = totalMinutes < 0 ? '-' : '';
    totalMinutes = Math.abs(totalMinutes);
    return sign + Math.floor(totalMinutes / 60) + 'h' + String(totalMinutes % 60).padStart(2, '0');
  }

  /* '6h30m' — o que vai no campo de duração. Diferente de formatHours: ali
     '6h30' é só exibição, e o parser leria isso como 6h redondas. */
  function durationValue(hours) {
    var minutes = Math.max(0, Math.round((hours || 0) * 60));
    var whole = Math.floor(minutes / 60);
    var rest = minutes % 60;
    if (!minutes) return '';
    return (whole ? whole + 'h' : '') + (rest ? rest + 'm' : '');
  }

  var UNIT_SECONDS = { w: 5 * 8 * 3600, d: 8 * 3600, h: 3600, m: 60 };

  /* Mesma gramática do servidor ('2h30m', '1h30', '1.5h', '90m', '8'), para o
     total da caixa de lançamento ser calculado sem ida ao servidor.

     A unidade é opcional: número solto vale minutos depois de outro valor
     ('1h30') e horas quando está sozinho ('8'). */
  function parseDuration(text) {
    text = String(text || '').trim().toLowerCase().replace(/,/g, '.');
    if (!text) return 0;
    var token = /(\d+(?:\.\d+)?)\s*([wdhm])?/g;
    var seconds = 0;
    var index = 0;
    var found;
    while ((found = token.exec(text)) !== null) {
      if (!found[0]) { token.lastIndex += 1; continue; }  // não trava em texto solto
      var unit = found[2] || (index ? 'm' : 'h');
      seconds += parseFloat(found[1]) * UNIT_SECONDS[unit];
      index += 1;
    }
    return Math.round(seconds);
  }

  function debounce(fn, wait) {
    var timer;
    return function () {
      var args = arguments, self = this;
      clearTimeout(timer);
      timer = setTimeout(function () { fn.apply(self, args); }, wait);
    };
  }

  /* ---------------------------------------------------------------- HTTP -- */
  /* Toda resposta de erro da API vem como {error: "..."}; aqui ela vira uma
     Exception com mensagem legível, e quem chamou decide onde mostrar. */
  function api(path, options) {
    options = options || {};
    var init = { method: options.method || 'GET', headers: {} };
    if (options.body !== undefined) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(options.body);
    }
    if (options.signal) init.signal = options.signal;

    return fetch(path, init).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (data) {
        if (!response.ok && !(data && data.created && data.created.length)) {
          var error = new Error(data.error || ('Falha na comunicação (HTTP ' + response.status + ')'));
          error.status = response.status;
          error.needsConfig = !!data.needs_config;
          error.payload = data;
          throw error;
        }
        return data;
      });
    }, function () {
      throw new Error('Servidor local não respondeu. O tracker ainda está na bandeja?');
    });
  }

  /* -------------------------------------------------------------- toasts -- */
  var toasts = $('#toasts');

  function toast(kind, title, text, timeout) {
    var node = el('div', { class: 'toast toast--' + kind, role: 'status' }, [
      el('div', { class: 'toast__body' }, [
        el('div', { class: 'toast__title', text: title }),
        text ? el('div', { class: 'toast__text', text: text }) : null
      ]),
      el('button', {
        class: 'btn btn--ghost btn--icon toast__close',
        'aria-label': 'Fechar aviso',
        onclick: function () { close(); }
      }, [icon('<path d="M18 6 6 18M6 6l12 12"></path>', '2.2')])
    ]);

    function close() {
      if (node.dataset.closing) return;
      node.dataset.closing = 'true';
      setTimeout(function () { node.remove(); }, 180);
    }

    toasts.appendChild(node);
    var life = timeout === undefined ? (kind === 'error' ? 9000 : 4000) : timeout;
    if (life > 0) setTimeout(close, life);
    return close;
  }

  function banner(container, kind, text) {
    container.innerHTML = '';
    if (!text) return;
    container.appendChild(el('div', { class: 'banner banner--' + kind }, [
      el('span', { text: text })
    ]));
  }

  /* --------------------------------------------------------------- tema -- */
  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem('jtt-theme', theme); } catch (e) {}
    var sun = '<circle cx="12" cy="12" r="4"></circle><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"></path>';
    var moon = '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"></path>';
    var button = $('#icon-theme');
    if (button) button.innerHTML = theme === 'dark' ? sun : moon;
  }

  /* ------------------------------------------------------------ períodos -- */

  /* Dias a voltar de `date` até o primeiro dia da semana configurado.
     getDay(): domingo = 0 ... sábado = 6. */
  function daysToWeekStart(date) {
    var day = date.getDay();
    return state.prefs.weekStart === 'sunday' ? day : (day + 6) % 7;
  }

  function presetRange(preset) {
    var today = new Date();
    var start, end;
    if (preset === 'semana' || preset === 'anterior') {
      var offset = preset === 'anterior' ? 7 : 0;
      start = new Date(today);
      start.setDate(today.getDate() - daysToWeekStart(today) - offset);
      end = new Date(start);
      end.setDate(start.getDate() + 6);
    } else if (preset === 'mes') {
      /* Mês de fechamento da empresa, não o mês do calendário: do dia 21 ao dia
         20 do mês seguinte (o dia vem da configuração). Antes do dia de virada,
         o ciclo corrente é o que começou no mês passado. */
      var firstDay = state.prefs.monthCycleStart;
      var anchor = new Date(today.getFullYear(), today.getMonth(), 1);
      if (today.getDate() < firstDay) anchor.setMonth(anchor.getMonth() - 1);
      start = new Date(anchor.getFullYear(), anchor.getMonth(), firstDay);
      end = new Date(anchor.getFullYear(), anchor.getMonth() + 1, firstDay - 1);
    } else {
      end = today;
      start = new Date(today);
      start.setDate(today.getDate() - 29);
    }
    return { start: iso(start), end: iso(end) };
  }

  /* Rótulo do atalho de mês: "21/10 a 20/11" diz mais que "Este mês". */
  function describeMonthPreset() {
    var chip = $('.chip[data-preset="mes"]');
    if (!chip) return;
    var range = presetRange('mes');
    var from = parseIso(range.start);
    var to = parseIso(range.end);
    var pad = function (n) { return (n < 10 ? '0' : '') + n; };
    chip.title = 'Ciclo de fechamento: '
      + pad(from.getDate()) + '/' + pad(from.getMonth() + 1) + ' a '
      + pad(to.getDate()) + '/' + pad(to.getMonth() + 1);
  }

  function setPreset(preset, reload) {
    var range = presetRange(preset);
    state.preset = preset;
    state.start = range.start;
    state.end = range.end;
    $('#start-date').value = range.start;
    $('#end-date').value = range.end;
    $$('.chip[data-preset]').forEach(function (chip) {
      chip.setAttribute('aria-pressed', String(chip.dataset.preset === preset));
    });
    syncUrl();
    if (reload !== false) loadWorklogs();
  }

  function clearPreset() {
    state.preset = null;
    $$('.chip[data-preset]').forEach(function (chip) { chip.setAttribute('aria-pressed', 'false'); });
  }

  /* A URL guarda o período e a aba, então F5 e "abrir de novo" caem no mesmo
     lugar — sem que navegar na tela custe um reload. */
  function syncUrl() {
    var params = new URLSearchParams();
    if (state.start) params.set('start', state.start);
    if (state.end) params.set('end', state.end);
    if (state.view !== 'horas') params.set('view', state.view);
    history.replaceState(null, '', '?' + params.toString());
  }

  function readUrl() {
    var params = new URLSearchParams(location.search);
    return {
      start: params.get('start'),
      end: params.get('end'),
      view: params.get('view')
    };
  }

  /* -------------------------------------------------------------- dias --- */
  function dayByDate(value) {
    for (var i = 0; i < state.days.length; i++) {
      if (state.days[i].date === value) return state.days[i];
    }
    return null;
  }

  /* O feriado vem junto com o dia, calculado no servidor a partir do
     feriados.json. É só um aviso: não muda nada no que se pode fazer. */
  function holidayOn(value) {
    var day = dayByDate(value);
    return day && day.holiday ? day.holiday : null;
  }

  /* Um dia qualquer, mesmo fora do período na tela: quem lança no dia 3 com a
     semana atual aberta também merece saber quanto já tem lá. O servidor é
     consultado uma vez por data e o resultado fica guardado enquanto a caixa
     de lançamento estiver aberta. */
  var dayCache = {};

  function fetchDay(value) {
    if (dayCache[value]) return dayCache[value];
    var query = new URLSearchParams({ start: value, end: value, target: String(state.targetHours) });
    dayCache[value] = api('/api/worklogs?' + query.toString()).then(function (data) {
      return (data.days || [])[0] || null;
    }).catch(function () {
      delete dayCache[value];  // falhou: na próxima tentativa, tenta de novo
      return null;
    });
    return dayCache[value];
  }

  function dayInfo(value) {
    var known = dayByDate(value);
    if (known) return Promise.resolve(known);
    return fetchDay(value);
  }

  /* O que já está em dayCache/state.days, sem promessa. O indicador pinta com
     o que tiver agora e repinta quando a consulta chegar. */
  var dayKnown = {};

  function dayNow(value) {
    var day = dayByDate(value);
    if (day) return day;
    return Object.prototype.hasOwnProperty.call(dayKnown, value) ? dayKnown[value] : undefined;
  }

  /* --------------------------------------------------------------- horas -- */
  function dayLevel(hours, target) {
    if (!hours) return 'empty';
    var ratio = hours / target;
    if (ratio >= 1.02) return 'over';
    if (ratio >= 0.98) return 'full';
    if (ratio >= 0.5) return 'mid';
    return 'low';
  }

  function renderSkeleton() {
    var stats = $('#stats');
    stats.innerHTML = '';
    for (var i = 0; i < 4; i++) stats.appendChild(el('div', { class: 'skeleton skeleton--stat' }));
    var days = $('#days');
    days.innerHTML = '';
    for (var j = 0; j < 7; j++) days.appendChild(el('div', { class: 'skeleton skeleton--day' }));
  }

  function renderStats() {
    var stats = $('#stats');
    var summary = state.summary;
    stats.innerHTML = '';
    if (!summary) return;

    var balance = summary.balance_hours;
    var balanceClass = balance >= 0 ? 'stat__value--ok' : (balance < -2 ? 'stat__value--danger' : 'stat__value--warn');

    [
      {
        label: 'Lançado no período',
        value: formatHours(summary.total_hours),
        meta: summary.entries + (summary.entries === 1 ? ' lançamento' : ' lançamentos')
      },
      {
        label: 'Esperado',
        value: formatHours(summary.expected_hours),
        meta: summary.workdays + (summary.workdays === 1 ? ' dia útil' : ' dias úteis') +
              ' × ' + formatHours(state.targetHours)
      },
      {
        label: 'Saldo',
        value: (balance >= 0 ? '+' : '') + formatHours(balance),
        meta: balance >= 0 ? 'em dia' : 'faltam ' + formatHours(-balance),
        valueClass: balanceClass
      },
      {
        label: 'Dias completos',
        value: summary.complete_days + '/' + summary.workdays,
        meta: 'com ' + formatHours(state.targetHours) + ' ou mais'
      }
    ].forEach(function (item) {
      stats.appendChild(el('div', { class: 'stat' }, [
        el('div', { class: 'stat__label', text: item.label }),
        el('div', { class: 'stat__value ' + (item.valueClass || ''), text: item.value }),
        el('div', { class: 'stat__meta', text: item.meta })
      ]));
    });
  }

  function renderLog(day, log) {
    var row = el('li', {
      class: 'log',
      'data-log-id': log.id,
      'data-state': log.pending ? 'pending' : 'ok'
    }, [
      el('a', {
        class: 'log__key',
        href: log.url,
        target: '_blank',
        rel: 'noopener',
        text: log.key,
        onclick: function (event) { event.stopPropagation(); }
      }),
      el('div', { class: 'log__summary' }, [
        el('div', { class: 'log__title', text: log.summary || '—', title: log.summary || '' }),
        log.comment ? el('div', { class: 'log__comment', text: log.comment, title: log.comment }) : null
      ]),
      el('span', { class: 'log__hours', text: formatHours(log.hours) }),
      el('button', {
        class: 'btn btn--danger btn--icon log__delete',
        title: 'Excluir lançamento',
        'aria-label': 'Excluir lançamento ' + log.key,
        onclick: function (event) {
          event.stopPropagation();
          deleteLog(day, log, row);
        }
      }, [icon('<path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"></path>')])
    ]);
    return row;
  }

  function renderDay(day) {
    var level = dayLevel(day.hours, state.targetHours);
    var percent = Math.min(100, Math.round((day.hours / state.targetHours) * 100));

    var badges = el('div', { class: 'day__badges' });
    if (day.today) badges.appendChild(el('span', { class: 'badge badge--accent', text: 'hoje' }));
    if (day.weekend) badges.appendChild(el('span', { class: 'badge', text: 'fim de semana' }));
    if (day.holiday) {
      badges.appendChild(el('span', {
        class: 'badge badge--info',
        text: day.holiday,
        title: 'Feriado. Lançar é com você: em branco, o dia não é cobrado.'
      }));
    }
    /* Feriado em branco não é cobrado — tem time que lança nele e time que
       não, e a tela não tem por que escolher um dos dois. */
    if (!day.weekend && !(day.holiday && !day.seconds)) {
      if (!day.future && !day.seconds) badges.appendChild(el('span', { class: 'badge badge--danger', text: 'sem lançamento' }));
      else if (!day.future && level === 'mid') badges.appendChild(el('span', { class: 'badge badge--warn', text: 'incompleto' }));
      else if (level === 'full' || level === 'over') badges.appendChild(el('span', { class: 'badge badge--ok', text: 'ok' }));
    }

    var body = el('div', { class: 'day__body hidden', id: 'day-body-' + day.date });
    if (day.logs.length) {
      var list = el('ul', { class: 'logs' });
      day.logs.forEach(function (log) { list.appendChild(renderLog(day, log)); });
      body.appendChild(list);
    } else {
      body.appendChild(el('div', { class: 'empty-day', text: 'Nenhuma hora lançada neste dia.' }));
    }

    /* O botão aparece em todo dia, não só no vazio: um dia com 1h de 8h ainda
       precisa de um lugar para lançar o resto. Quando falta tempo para a meta,
       ele já leva a diferença preenchida. */
    var missing = state.targetHours - day.hours;
    var partial = !day.weekend && missing > 0.004;  // ~15 segundos
    body.appendChild(el('div', { class: 'day__actions' }, [
      el('button', {
        class: 'btn btn--sm',
        onclick: function () {
          openWorklogModal({ date: day.date, duration: partial ? durationValue(missing) : '' });
        }
      }, [el('span', {
        class: 'btn__label',
        text: partial && day.logs.length ? 'Lançar as ' + formatHours(missing) + ' restantes'
                                         : 'Lançar neste dia'
      })])
    ]));

    var head = el('button', {
      class: 'day__head',
      type: 'button',
      'aria-expanded': 'false',
      'aria-controls': 'day-body-' + day.date
    }, [
      el('span', { class: 'day__date', text: formatDay(day.date) + ' ' }, [
        el('span', { class: 'faint', text: String(parseIso(day.date).getFullYear()) })
      ]),
      el('span', { class: 'day__weekday', text: day.weekday }),
      el('span', { class: 'bar' }, [
        el('span', { class: 'bar__fill', 'data-level': level, style: 'width:' + percent + '%' })
      ]),
      badges,
      el('span', { class: 'day__hours', text: formatHours(day.hours) })
    ]);
    head.appendChild(icon('<path d="m9 18 6-6-6-6"></path>')).classList.add('day__chevron');

    var card = el('div', {
      class: 'day',
      'data-date': day.date,
      'data-weekend': String(day.weekend),
      'data-today': String(day.today),
      'data-open': 'false'
    }, [head, body]);

    head.addEventListener('click', function () {
      var open = card.dataset.open !== 'true';
      card.dataset.open = String(open);
      head.setAttribute('aria-expanded', String(open));
      body.classList.toggle('hidden', !open);
    });

    return card;
  }

  function renderDays() {
    var container = $('#days');
    var openDates = {};
    $$('.day[data-open="true"]', container).forEach(function (node) { openDates[node.dataset.date] = true; });

    container.innerHTML = '';

    if (!state.days.length) {
      container.appendChild(el('div', { class: 'placeholder' }, [
        el('h3', { text: 'Nenhum dia no período' }),
        el('p', { text: 'Ajuste as datas acima para ver seus lançamentos.' })
      ]));
      return;
    }

    var fragment = document.createDocumentFragment();
    state.days.forEach(function (day) {
      var card = renderDay(day);
      if (openDates[day.date]) {
        card.dataset.open = 'true';
        $('.day__head', card).setAttribute('aria-expanded', 'true');
        $('.day__body', card).classList.remove('hidden');
      }
      fragment.appendChild(card);
    });
    container.appendChild(fragment);
  }

  function renderHoras() {
    renderStats();
    renderDays();
  }

  /* Recarrega os dados do período. Chamadas antigas que chegarem atrasadas são
     descartadas (requestId), para uma troca rápida de filtro não pintar a tela
     com o resultado do filtro anterior. */
  function loadWorklogs(options) {
    options = options || {};
    if (!CFG.configured) return Promise.resolve();

    var id = ++state.requestId;
    state.loading = true;
    if (!options.quiet) renderSkeleton();

    var query = new URLSearchParams({
      start: state.start,
      end: state.end,
      target: String(state.targetHours)
    });

    return api('/api/worklogs?' + query.toString()).then(function (data) {
      if (id !== state.requestId) return;
      state.days = data.days;
      state.summary = data.summary;
      state.loading = false;
      banner($('#horas-banner'), 'error', '');
      renderHoras();
    }).catch(function (error) {
      if (id !== state.requestId) return;
      state.loading = false;
      if (error.needsConfig) {
        location.href = '/config';
        return;
      }
      $('#stats').innerHTML = '';
      $('#days').innerHTML = '';
      banner($('#horas-banner'), 'error', error.message);
      $('#days').appendChild(el('div', { class: 'placeholder' }, [
        el('h3', { text: 'Não deu para carregar as horas' }),
        el('p', { text: error.message }),
        el('button', {
          class: 'btn btn--primary',
          onclick: function () { loadWorklogs(); }
        }, [el('span', { class: 'btn__label', text: 'Tentar de novo' })])
      ]));
    });
  }

  /* Exclusão otimista: a linha some na hora e volta se o servidor recusar. */
  function deleteLog(day, log, row) {
    if (!confirm('Excluir o lançamento de ' + formatHours(log.hours) + ' em ' + log.key + '?')) return;

    row.dataset.state = 'removing';
    var query = log.key ? '?issue_key=' + encodeURIComponent(log.key) : '';

    api('/api/worklogs/' + encodeURIComponent(log.id) + query, { method: 'DELETE' })
      .then(function () {
        var target = state.days.filter(function (item) { return item.date === day.date; })[0];
        if (target) {
          target.logs = target.logs.filter(function (item) { return item.id !== log.id; });
          target.seconds -= log.seconds;
          target.hours = Math.round((target.seconds / 3600) * 100) / 100;
        }
        recalcSummary();
        renderHoras();
        toast('success', 'Lançamento excluído', log.key + ' · ' + formatHours(log.hours));
      })
      .catch(function (error) {
        row.dataset.state = 'ok';
        toast('error', 'Não foi possível excluir', error.message);
      });
  }

  /* Recalcula o resumo no cliente para o topo mudar junto com a lista, sem
     esperar um novo GET. O servidor continua sendo a fonte da verdade: a
     revalidação em segundo plano corrige qualquer divergência. */
  function recalcSummary() {
    if (!state.summary) return;
    var totalSeconds = 0;
    var entries = 0;
    var complete = 0;
    var workdays = 0;
    state.days.forEach(function (day) {
      totalSeconds += day.seconds;
      entries += day.logs.length;
      if (!day.weekend && !day.future && !(day.holiday && !day.seconds)) {
        workdays += 1;
        if (day.seconds >= state.targetHours * 3600) complete += 1;
      }
    });
    var expected = workdays * state.targetHours * 3600;
    state.summary.total_seconds = totalSeconds;
    state.summary.total_hours = Math.round((totalSeconds / 3600) * 100) / 100;
    state.summary.expected_seconds = expected;
    state.summary.expected_hours = Math.round((expected / 3600) * 100) / 100;
    state.summary.balance_hours = Math.round(((totalSeconds - expected) / 3600) * 100) / 100;
    state.summary.entries = entries;
    state.summary.complete_days = complete;
    state.summary.workdays = workdays;
  }

  /* ----------------------------------------------------- modal lançar -- */
  var modal = $('#modal-worklog');
  var entriesBox = $('#wl-entries');

  /* Dia útil anterior a uma data: sábado, domingo e feriado não contam.
     O feriado só é reconhecido dentro do período carregado; fora dele a
     sugestão pode cair num, e aí o próprio lançamento avisa. */
  function previousWorkday(date) {
    var day = new Date(date);
    for (var guard = 0; guard < 60; guard++) {
      day.setDate(day.getDate() - 1);
      if (day.getDay() === 0 || day.getDay() === 6) continue;
      if (holidayOn(iso(day))) continue;
      return day;
    }
    return day;
  }

  /* Data sugerida para uma linha nova: o dia útil anterior ao mais antigo que
     já está no formulário. Lançar três dias seguidos vira três cliques, sem
     abrir o calendário. */
  function nextSuggestedDate() {
    var dates = $$('.entry input[type="date"]', entriesBox)
      .map(function (input) { return input.value; })
      .filter(Boolean)
      .sort();
    var reference = dates.length ? parseIso(dates[0]) : new Date();
    return iso(previousWorkday(reference));
  }

  /* ---------------------------------------------------- indicador do dia -- */
  /* A conta que a pessoa faria de cabeça: o que o dia já tem, mais o que está
     sendo digitado, contra a meta. Sem isso, digitar 5h num dia que já tem 1h
     passa da meta sem ninguém perceber. */
  function hintFor(day, already, typing) {
    var target = state.targetHours;
    var total = already + typing;
    var prefix = day && day.holiday ? day.holiday + ' · ' : '';

    if (day && day.weekend) {
      return { tone: '', text: 'Fim de semana · não entra na meta do período' };
    }
    if (day && day.holiday && !already && !typing) {
      return { tone: '', text: prefix + 'feriado · só conta se tiver lançamento' };
    }

    var missing = target - total;
    var parts;
    if (!typing) {
      parts = already
        ? formatHours(already) + ' de ' + formatHours(target)
        : 'nada lançado neste dia';
    } else if (already) {
      parts = formatHours(already) + ' + ' + formatHours(typing)
        + ' = ' + formatHours(total) + ' de ' + formatHours(target);
    } else {
      parts = formatHours(total) + ' de ' + formatHours(target);
    }

    if (missing < -0.004) {
      return { tone: 'over', text: prefix + parts + ' · ' + formatHours(-missing) + ' acima da meta' };
    }
    if (missing <= 0.004) {
      return { tone: 'ok', text: prefix + parts + ' · meta fechada' };
    }
    return { tone: '', text: prefix + parts + ' · faltam ' + formatHours(missing) };
  }

  /* Horas já digitadas para a mesma data em OUTRAS linhas: dois meio-períodos
     no mesmo dia precisam ser somados, senão cada linha diria que ainda falta
     o dia inteiro. */
  function typedOn(date, except) {
    return $$('.entry', entriesBox).reduce(function (sum, row) {
      if (row === except) return sum;
      var inputs = $$('input', row);
      if (inputs[0].value !== date) return sum;
      return sum + parseDuration(inputs[1].value) / 3600;
    }, 0);
  }

  function paintHint(row) {
    var inputs = $$('input', row);
    var hint = $('.entry__hint', row);
    if (!hint) return;
    var date = inputs[0].value;
    if (!date) {
      hint.textContent = '';
      hint.removeAttribute('data-tone');
      return;
    }

    var day = dayNow(date);
    if (day === undefined) {
      // Dia de fora do período na tela: busca uma vez e repinta ao chegar.
      hint.textContent = 'consultando o dia…';
      hint.removeAttribute('data-tone');
      fetchDay(date).then(function (fetched) {
        dayKnown[date] = fetched;
        paintHints();
      });
      return;
    }

    var typing = parseDuration(inputs[1].value) / 3600 + typedOn(date, row);
    var info = hintFor(day, (day && day.hours) || 0, typing);
    hint.textContent = info.text;
    if (info.tone) hint.setAttribute('data-tone', info.tone);
    else hint.removeAttribute('data-tone');
  }

  function paintHints() {
    $$('.entry', entriesBox).forEach(paintHint);
  }

  function entryRow(value) {
    value = value || {};
    var row = el('div', { class: 'entry' }, [
      el('input', { type: 'date', 'aria-label': 'Data', value: value.date || '' }),
      el('input', {
        type: 'text',
        'aria-label': 'Duração',
        placeholder: '8h',
        value: value.duration || ''
      }),
      el('button', {
        type: 'button',
        class: 'btn btn--danger btn--icon',
        'aria-label': 'Remover linha',
        onclick: function () {
          row.remove();
          if (!entriesBox.children.length) {
            entriesBox.appendChild(entryRow({ date: iso(new Date()) }));
          }
          updateTotal();
          paintHints();
        }
      }, [icon('<path d="M18 6 6 18M6 6l12 12"></path>', '2.2')]),
      el('div', { class: 'entry__hint' })
    ]);
    $$('input', row).forEach(function (input) {
      input.addEventListener('input', updateTotal);
      // O indicador de todas as linhas, não só o desta: duas linhas no mesmo
      // dia somam, e a conta de uma depende do que está escrito na outra.
      input.addEventListener('input', paintHints);
      input.addEventListener('change', paintHints);
    });
    paintHint(row);
    return row;
  }

  function readEntries() {
    return $$('.entry', entriesBox).map(function (row) {
      var inputs = $$('input', row);
      return { date: inputs[0].value, duration: inputs[1].value, row: row };
    }).filter(function (entry) { return entry.date || entry.duration; });
  }

  function updateTotal() {
    var seconds = readEntries().reduce(function (sum, entry) {
      return sum + parseDuration(entry.duration);
    }, 0);
    $('#wl-total').textContent = formatHours(seconds / 3600);
  }

  function openWorklogModal(prefill) {
    prefill = prefill || {};
    banner($('#modal-banner'), 'error', '');
    // Cada abertura parte de dados frescos: o que foi lançado na anterior já
    // mudou o quanto falta nos dias consultados.
    dayCache = {};
    dayKnown = {};
    $('#wl-issue').value = prefill.issueKey || '';
    $('#wl-comment').value = '';
    $('#wl-issue').setAttribute('aria-invalid', 'false');
    $('#wl-issue-feedback').className = 'field__hint';
    $('#wl-issue-feedback').textContent = 'Digite a chave ou o número do pedido.';
    entriesBox.innerHTML = '';
    entriesBox.appendChild(entryRow({ date: prefill.date || iso(new Date()),
                                     duration: prefill.duration || '' }));
    updateTotal();
    if (!modal.open) modal.showModal();
    setTimeout(function () { $('#wl-issue').focus(); }, 30);
  }

  function saveWorklogs() {
    var issueKey = $('#wl-issue').value.trim().toUpperCase();
    var comment = $('#wl-comment').value.trim();
    var entries = readEntries();

    banner($('#modal-banner'), 'error', '');

    if (!issueKey) {
      $('#wl-issue').setAttribute('aria-invalid', 'true');
      $('#wl-issue').focus();
      banner($('#modal-banner'), 'error', 'Informe a issue antes de salvar.');
      return;
    }

    var invalid = entries.filter(function (entry) {
      return !entry.date || parseDuration(entry.duration) <= 0;
    });
    if (!entries.length || invalid.length) {
      invalid.forEach(function (entry) {
        $$('input', entry.row).forEach(function (input) { input.setAttribute('aria-invalid', 'true'); });
      });
      banner($('#modal-banner'), 'error',
        !entries.length ? 'Adicione ao menos um dia com duração.'
                        : 'Confira as linhas marcadas: data e duração são obrigatórias.');
      return;
    }

    var button = $('#wl-save');
    button.dataset.busy = 'true';

    api('/api/worklogs', {
      method: 'POST',
      body: {
        issue_key: issueKey,
        comment: comment,
        entries: entries.map(function (entry) {
          return { date: entry.date, duration: entry.duration };
        })
      }
    }).then(function (data) {
      delete button.dataset.busy;

      // Entra direto no estado local: a tela mostra o lançamento antes mesmo da
      // revalidação, e sem nenhum reload.
      (data.created || []).forEach(function (created) {
        var day = state.days.filter(function (item) { return item.date === created.date; })[0];
        if (!day) return;
        day.logs.push({
          id: created.id,
          key: created.key,
          summary: created.summary,
          seconds: created.seconds,
          hours: created.hours,
          comment: created.comment,
          url: created.url
        });
        day.logs.sort(function (a, b) { return a.key.localeCompare(b.key); });
        day.seconds += created.seconds;
        day.hours = Math.round((day.seconds / 3600) * 100) / 100;
      });
      recalcSummary();
      renderHoras();

      if (data.failed && data.failed.length) {
        var lines = data.failed.map(function (item) {
          return (item.date || 'sem data') + ': ' + item.error;
        }).join(' · ');
        if (data.created && data.created.length) {
          toast('warn', data.created.length + ' de ' +
            (data.created.length + data.failed.length) + ' dias lançados', lines, 12000);
        }
        banner($('#modal-banner'), 'error', lines);
        // Deixa na tela só o que falhou, já pronto para corrigir e salvar de novo.
        entriesBox.innerHTML = '';
        data.failed.forEach(function (item) {
          entriesBox.appendChild(entryRow({ date: item.date, duration: item.duration }));
        });
        updateTotal();
        return;
      }

      modal.close();
      var total = (data.created || []).reduce(function (sum, item) { return sum + item.seconds; }, 0);
      toast('success', 'Horas lançadas',
        issueKey + ' · ' + formatHours(total / 3600) +
        ' em ' + data.created.length + (data.created.length === 1 ? ' dia' : ' dias'));

      // Confere com o servidor em segundo plano, sem piscar a tela.
      loadWorklogs({ quiet: true });
    }).catch(function (error) {
      delete button.dataset.busy;
      banner($('#modal-banner'), 'error', error.message);
      if (error.status === 404) {
        $('#wl-issue').setAttribute('aria-invalid', 'true');
        $('#wl-issue').focus();
      }
    });
  }

  /* ------------------------------------------------- autocompletar issue -- */
  function setupCombo() {
    var input = $('#wl-issue');
    var feedback = $('#wl-issue-feedback');
    var list = null;
    var results = [];
    var active = -1;

    function closeList() {
      if (list) { list.remove(); list = null; }
      input.setAttribute('aria-expanded', 'false');
      active = -1;
    }

    function choose(index) {
      var item = results[index];
      if (!item) return;
      input.value = item.key;
      feedback.className = 'field__hint';
      feedback.textContent = item.summary;
      input.setAttribute('aria-invalid', 'false');
      closeList();
      var firstDate = $$('.entry input[type="date"]', entriesBox)[0];
      if (firstDate && !firstDate.value) firstDate.focus();
    }

    function openList() {
      closeList();
      if (!results.length) return;
      list = el('ul', { class: 'combo__list', id: 'wl-issue-list', role: 'listbox' });
      results.forEach(function (item, index) {
        list.appendChild(el('li', {
          class: 'combo__option',
          role: 'option',
          'aria-selected': 'false',
          onmousedown: function (event) { event.preventDefault(); choose(index); }
        }, [
          el('strong', { text: item.key + (item.pedido ? '  ·  pedido ' + item.pedido : '') }),
          el('span', { text: item.summary || '' })
        ]));
      });
      input.parentNode.appendChild(list);
      input.setAttribute('aria-expanded', 'true');
    }

    function highlight(index) {
      if (!list) return;
      var options = $$('.combo__option', list);
      options.forEach(function (option, i) { option.setAttribute('aria-selected', String(i === index)); });
      active = index;
    }

    var search = debounce(function (term) {
      api('/api/issues/suggest?q=' + encodeURIComponent(term))
        .then(function (data) {
          if (input.value.trim() !== term) return;
          results = data.results || [];
          openList();
        })
        .catch(function () { /* sugestão é conveniência: falhar aqui não atrapalha */ });
    }, 220);

    input.addEventListener('input', function () {
      input.setAttribute('aria-invalid', 'false');
      feedback.className = 'field__hint';
      feedback.textContent = 'Digite a chave ou o número do pedido.';
      var term = input.value.trim();
      if (term.length < 2) { results = []; closeList(); return; }
      search(term);
    });

    input.addEventListener('keydown', function (event) {
      if (!list) return;
      if (event.key === 'ArrowDown') { event.preventDefault(); highlight(Math.min(active + 1, results.length - 1)); }
      else if (event.key === 'ArrowUp') { event.preventDefault(); highlight(Math.max(active - 1, 0)); }
      else if (event.key === 'Enter' && active >= 0) { event.preventDefault(); choose(active); }
      else if (event.key === 'Escape') { event.stopPropagation(); closeList(); }
    });

    input.addEventListener('blur', function () { setTimeout(closeList, 120); });
  }

  /* -------------------------------------------------------------- issues -- */
  function renderIssues() {
    var wrap = $('#issues-wrap');

    if (state.issuesLoading) {
      wrap.innerHTML = '';
      for (var i = 0; i < 8; i++) wrap.appendChild(el('div', { class: 'skeleton skeleton--day', style: 'margin:6px' }));
      return;
    }
    if (!state.issues) { wrap.innerHTML = ''; return; }

    var term = state.issueFilter.term.toLowerCase();
    var statusFilter = state.issueFilter.status;
    var rows = state.issues.filter(function (issue) {
      var haystack = (issue.key + ' ' + issue.id + ' ' + issue.summary + ' ' +
                      issue.status + ' ' + issue.assignee + ' ' + (issue.pedido || '')).toLowerCase();
      return haystack.indexOf(term) !== -1 && (!statusFilter || issue.status === statusFilter);
    });

    wrap.innerHTML = '';
    if (!rows.length) {
      wrap.appendChild(el('div', { class: 'placeholder' }, [
        el('h3', { text: 'Nenhuma issue encontrada' }),
        el('p', { text: 'Tente outro termo ou limpe o filtro de status.' })
      ]));
      return;
    }

    var table = el('table', { class: 'data' }, [
      el('thead', {}, [
        el('tr', {}, ['Chave', 'Issue', 'Status', 'Responsável', 'Estimado', 'Registrado', 'Restante', '']
          .map(function (title) { return el('th', { text: title }); }))
      ])
    ]);

    var body = el('tbody');
    rows.forEach(function (issue) {
      var row = el('tr', { onclick: function () { openIssueModal(issue); } }, [
        el('td', {}, [el('span', { class: 'mono', text: issue.key })]),
        el('td', { class: 'issue-summary' }, [
          el('div', { text: issue.summary, title: issue.summary }),
          el('div', { class: 'faint', text: issue.issue_type + ' · ' + issue.priority })
        ]),
        el('td', {}, [el('span', { class: 'status status--' + issue.status_category, text: issue.status })]),
        el('td', { text: issue.assignee }),
        el('td', { class: 'mono', text: issue.original }),
        el('td', { class: 'mono', text: issue.spent }),
        el('td', { class: 'mono', text: issue.remaining }),
        el('td', {}, [
          el('button', {
            class: 'btn btn--sm',
            onclick: function (event) {
              event.stopPropagation();
              state.view = 'horas';
              switchView('horas');
              openWorklogModal({ issueKey: issue.key });
            }
          }, [el('span', { class: 'btn__label', text: 'Lançar' })])
        ])
      ]);
      body.appendChild(row);
    });
    table.appendChild(body);
    wrap.appendChild(table);

    var select = $('#issue-status');
    if (select.options.length <= 1) {
      var statuses = {};
      state.issues.forEach(function (issue) { statuses[issue.status] = true; });
      Object.keys(statuses).sort().forEach(function (status) {
        select.appendChild(el('option', { value: status, text: status }));
      });
    }
  }

  function loadIssues(refresh) {
    if (!CFG.tempoMode) return;
    state.issuesLoading = true;
    renderIssues();
    api('/api/issues' + (refresh ? '?refresh=1' : '')).then(function (data) {
      state.issuesLoading = false;
      state.issues = data.issues || [];
      banner($('#issues-banner'), 'error', '');
      renderIssues();
    }).catch(function (error) {
      state.issuesLoading = false;
      state.issues = state.issues || [];
      renderIssues();
      banner($('#issues-banner'), 'error', error.message);
    });
  }

  function openIssueModal(issue) {
    $('#modal-issue-title').textContent = issue.key + ' — ' + issue.summary;
    $('#modal-issue-link').href = issue.url;

    var fields = [
      ['Status', issue.status],
      ['Responsável', issue.assignee],
      ['Prioridade', issue.priority],
      ['Pedido', issue.pedido || '—'],
      ['Estimativa', issue.original],
      ['Registrado', issue.spent],
      ['Restante', issue.remaining],
      ['Criada', issue.created || '—'],
      ['Atualizada', issue.updated || '—']
    ];

    var grid = el('dl', { class: 'detail-grid' });
    fields.forEach(function (pair) {
      grid.appendChild(el('div', {}, [
        el('dt', { text: pair[0] }),
        el('dd', { text: pair[1] })
      ]));
    });

    var body = $('#modal-issue-body');
    body.innerHTML = '';
    body.appendChild(grid);
    body.appendChild(el('h3', { text: 'Descrição', style: 'font-size:13px;margin-bottom:8px' }));
    body.appendChild(el('div', { class: 'description', text: issue.description || 'Sem descrição.' }));

    $('#modal-issue').showModal();
  }

  /* ---------------------------------------------------------------- abas -- */
  function switchView(view) {
    state.view = view;
    $$('.tab').forEach(function (tab) {
      tab.setAttribute('aria-selected', String(tab.dataset.view === view));
    });
    var horas = $('#view-horas');
    var issues = $('#view-issues');
    if (horas) horas.classList.toggle('hidden', view !== 'horas');
    if (issues) issues.classList.toggle('hidden', view !== 'issues');
    syncUrl();
    if (view === 'issues' && state.issues === null) loadIssues(false);
  }

  /* ----------------------------------------------------------- ligações -- */
  function wire() {
    $('#btn-theme').addEventListener('click', function () {
      applyTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
    });

    if (!CFG.configured) return;

    $$('.chip[data-preset]').forEach(function (chip) {
      chip.addEventListener('click', function () { setPreset(chip.dataset.preset); });
    });

    ['#start-date', '#end-date'].forEach(function (selector) {
      $(selector).addEventListener('change', function () {
        state.start = $('#start-date').value;
        state.end = $('#end-date').value;
        if (!state.start || !state.end) return;
        clearPreset();
        syncUrl();
        loadWorklogs();
      });
    });

    $('#btn-reload').addEventListener('click', function () { loadWorklogs(); });
    $('#btn-new').addEventListener('click', function () { openWorklogModal(); });
    $('#wl-add-entry').addEventListener('click', function () {
      // A data já vem preenchida com o dia útil anterior, então o foco vai
      // direto para a duração.
      var row = entryRow({ date: nextSuggestedDate() });
      entriesBox.appendChild(row);
      $$('input', row)[1].focus();
      updateTotal();
    });

    $('#form-worklog').addEventListener('submit', function (event) {
      event.preventDefault();
      saveWorklogs();
    });

    $$('[data-close]').forEach(function (button) {
      button.addEventListener('click', function () { button.closest('dialog').close(); });
    });

    modal.addEventListener('keydown', function (event) {
      if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        saveWorklogs();
      }
    });

    $$('.tab').forEach(function (tab) {
      tab.addEventListener('click', function () { switchView(tab.dataset.view); });
    });

    var search = $('#issue-search');
    if (search) {
      search.addEventListener('input', debounce(function () {
        state.issueFilter.term = search.value.trim();
        renderIssues();
      }, 120));
    }
    var status = $('#issue-status');
    if (status) {
      status.addEventListener('change', function () {
        state.issueFilter.status = status.value;
        renderIssues();
      });
    }
    var reloadIssues = $('#btn-issues-reload');
    if (reloadIssues) {
      reloadIssues.addEventListener('click', function () { loadIssues(true); });
    }

    document.addEventListener('keydown', function (event) {
      if (event.ctrlKey || event.altKey || event.metaKey) return;
      var tag = (event.target.tagName || '').toLowerCase();
      if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
      if (event.key === 'n' || event.key === 'N') { event.preventDefault(); openWorklogModal(); }
      else if (event.key === 'r' || event.key === 'R') { event.preventDefault(); loadWorklogs(); }
      else if (event.key === 't' || event.key === 'T') {
        applyTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
      }
    });

    setupCombo();

    // Voltar para a aba depois de um tempo fora traz dados frescos sem reload.
    document.addEventListener('visibilitychange', function () {
      if (document.visibilityState === 'visible' && !state.loading) {
        loadWorklogs({ quiet: true });
      }
    });
  }

  /* ----------------------------------------------------------- arranque -- */
  function start() {
    applyTheme(document.documentElement.dataset.theme || 'light');
    wire();
    describeMonthPreset();

    if (!CFG.configured) return;

    var url = readUrl();
    if (url.start && url.end) {
      state.start = url.start;
      state.end = url.end;
      $('#start-date').value = url.start;
      $('#end-date').value = url.end;
      clearPreset();
      loadWorklogs();
    } else {
      setPreset('semana');
    }

    if (url.view === 'issues' && CFG.tempoMode) switchView('issues');

    // Busca a lista de issues em segundo plano, depois que as horas já estão na
    // tela: a aba do portal e o autocompletar abrem instantâneos.
    if (CFG.tempoMode && state.issues === null) {
      setTimeout(function () { if (state.issues === null) loadIssues(false); }, 1200);
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
