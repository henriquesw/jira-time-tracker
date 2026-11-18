/* Tela de configuração: salva por fetch e mostra o resultado na própria página.
   Credencial errada antes recarregava a tela e apagava o que foi digitado;
   aqui o erro vira um aviso e os campos continuam preenchidos. */
(function () {
  'use strict';

  function $(selector) { return document.querySelector(selector); }

  function bannerIn(id, kind, text) {
    var box = $(id);
    box.innerHTML = '';
    if (!text) return;
    var div = document.createElement('div');
    div.className = 'banner banner--' + kind;
    div.appendChild(document.createElement('span')).textContent = text;
    box.appendChild(div);
    box.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }

  function banner(kind, text) { bannerIn('#config-banner', kind, text); }

  var form = $('#config-form');
  var button = $('#config-save');

  form.addEventListener('submit', function (event) {
    event.preventDefault();
    banner('', '');

    var payload = {
      jira_url: $('#jira_url').value.trim(),
      jira_email: $('#jira_email').value.trim(),
      jira_api_token: $('#jira_api_token').value.trim(),
      tempo_api_token: $('#tempo_api_token').value.trim(),
      clear_tempo: !!($('#clear_tempo') && $('#clear_tempo').checked)
    };

    if (!payload.jira_url || !payload.jira_email) {
      banner('error', 'URL e e-mail do Jira são obrigatórios.');
      return;
    }

    button.dataset.busy = 'true';

    fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    })
      .then(function (response) {
        return response.json().catch(function () { return {}; })
          .then(function (data) { return { ok: response.ok, data: data }; });
      })
      .then(function (result) {
        delete button.dataset.busy;
        if (!result.ok) {
          banner('error', result.data.error || 'Não foi possível salvar.');
          return;
        }
        banner('info', 'Tudo certo' +
          (result.data.display_name ? ', ' + result.data.display_name : '') +
          '. Modo: ' + (result.data.tempo_mode ? 'Tempo' : 'Jira nativo') + '. Abrindo o tracker…');
        setTimeout(function () { location.href = '/'; }, 900);
      })
      .catch(function () {
        delete button.dataset.busy;
        banner('error', 'O servidor local não respondeu. O tracker ainda está na bandeja?');
      });
  });

  /* ------------------------------------------------- preferências da tela -- */
  var prefsForm = $('#prefs-form');
  var prefsButton = $('#prefs-save');
  var cycleField = $('#month_cycle_start');
  var cyclePreview = $('#cycle-preview');

  if (cycleField && cyclePreview) {
    cycleField.addEventListener('input', function () {
      cyclePreview.textContent = cycleField.value || '?';
    });
  }

  if (prefsForm) {
    prefsForm.addEventListener('submit', function (event) {
      event.preventDefault();
      bannerIn('#prefs-banner', '', '');

      var payload = {
        week_start: $('#week_start').value,
        month_cycle_start: parseInt(cycleField.value, 10),
        target_hours: parseFloat($('#target_hours').value)
      };

      if (!(payload.month_cycle_start >= 1 && payload.month_cycle_start <= 28)) {
        bannerIn('#prefs-banner', 'error', 'O dia de fechamento precisa estar entre 1 e 28.');
        return;
      }
      if (!(payload.target_hours > 0 && payload.target_hours <= 24)) {
        bannerIn('#prefs-banner', 'error', 'Horas por dia precisa estar entre 0 e 24.');
        return;
      }

      prefsButton.dataset.busy = 'true';

      fetch('/api/prefs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      })
        .then(function (response) {
          return response.json().catch(function () { return {}; })
            .then(function (data) { return { ok: response.ok, data: data }; });
        })
        .then(function (result) {
          delete prefsButton.dataset.busy;
          if (!result.ok) {
            bannerIn('#prefs-banner', 'error', result.data.error || 'Não foi possível salvar.');
            return;
          }
          var prefs = result.data.prefs || {};
          bannerIn('#prefs-banner', 'info',
            'Salvo. Semana começando ' +
            (prefs.week_start === 'sunday' ? 'no domingo' : 'na segunda') +
            ', mês do dia ' + prefs.month_cycle_start +
            ', ' + prefs.target_hours + 'h por dia útil.');
        })
        .catch(function () {
          delete prefsButton.dataset.busy;
          bannerIn('#prefs-banner', 'error',
            'O servidor local não respondeu. O tracker ainda está na bandeja?');
        });
    });
  }

})();
