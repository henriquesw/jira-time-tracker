# Jira Time Tracker 3

Aplicação local para consultar, registrar e excluir horas no Jira Cloud, com os
worklogs nativos do Jira ou com o Tempo Timesheets.

Roda na sua máquina, sem janela de terminal: o programa fica como ícone em
**Mostrar ícones ocultos**, ao lado do relógio do Windows.

## Começando

### 1. Descompacte onde quiser

Evite pasta sincronizada pelo OneDrive, se puder. Funciona mesmo assim: o
ambiente Python e as credenciais ficam fora da pasta de qualquer jeito.

### 2. Clique duas vezes em `start.bat`

Na primeira vez ele chama o instalador, que **pergunta antes de instalar** e
então:

- cria o ambiente Python isolado em `%USERPROFILE%\.jira-time-tracker\venv`
  (não encosta no Python do sistema);
- instala as dependências lá dentro;
- desenha o ícone e cria o atalho **Jira Time Tracker 3** na área de trabalho.

Precisa de Python 3.10 ou superior. Se não tiver:
[python.org/downloads](https://www.python.org/downloads/) — marque *Add Python
to PATH*.

### 3. Depois disso, use o atalho

O navegador abre sozinho em `http://127.0.0.1:5000` e **nenhuma janela de
terminal aparece**. O programa continua rodando em segundo plano, representado
pelo ícone na área de notificação:

- **clique** no ícone — abre a tela de novo;
- **botão direito → Encerrar** — para o programa.

Clicar no atalho com o tracker já aberto não abre uma segunda cópia: só traz a
tela de volta.

### 4. Na primeira abertura, informe seus tokens

A tela de configuração aparece sozinha (depois, engrenagem no canto superior
direito).

**Jira** (necessário nos dois modos): em
[id.atlassian.com](https://id.atlassian.com/manage-profile/security/api-tokens),
use **Create API token** *sem escopos* — tokens com escopo usam outro endpoint e
não são suportados.

**Tempo** (só para quem registra pelo Tempo Timesheets): Jira → Apps → Tempo →
Settings → **API Integration** → New Token, escopo **Worklogs: View + Manage**.
Copie na hora: o Tempo mostra o token uma vez só. *Manage* é apenas o nível de
escrita do token; não torna ninguém gerente nem ignora as permissões do Jira.

O modo é escolhido pelo campo do Tempo: preenchido usa o Tempo, vazio usa os
worklogs nativos do Jira. A aba do Portal de Pedidos aparece só no modo Tempo.

Os tokens vão para o Windows Credential Manager. Nunca são gravados na pasta do
projeto.

### Atualizar para uma versão nova

Descompacte a versão nova **numa pasta própria** e clique em `start.bat`. Ele
percebe que já existe uma instalação, pergunta se pode atualizar e então
instala só o que falta, encerra a versão que estiver aberta e aponta o atalho
para a pasta nova. Credenciais e preferências continuam onde estão.

Depois disso a pasta antiga pode ser apagada.

## Preferências (engrenagem)

| Preferência | Padrão | O que muda |
|---|---|---|
| A semana começa em | Segunda-feira | Os filtros *Esta semana* e *Semana passada* |
| Dia de início do mês de fechamento | 21 | O filtro de mês vai do dia 21 ao dia 20 do mês seguinte |
| Horas esperadas por dia útil | 8 | O saldo do período e a barra de cada dia |

Ficam em `%USERPROFILE%\.jira-time-tracker\config.json`, junto da URL e do
e-mail — nunca com os tokens.

## Feriados

O dia aparece na lista com o nome do feriado, e só. **Lançar continua
permitido**, pela caixa de lançamento ou pelo próprio dia: tem time que lança
num card do Jira e tem time que deixa o dia em branco, e a tela não escolhe
por ninguém.

A única consequência está na soma do período: **feriado em branco não é
cobrado** — nada de "sem lançamento" nem de 8h somadas ao esperado — e
**feriado com horas lançadas conta como qualquer dia útil**, para o saldo de
quem trabalhou fechar certo.

A lista vem de **`feriados.json`**, na pasta do programa, separada por origem:

| Lista | O que tem |
|---|---|
| `nacionais` | 01/01, Sexta-feira Santa, Tiradentes, 1º de Maio, Independência, Aparecida, Finados, Proclamação da República, Consciência Negra e Natal |
| `estaduais` | Revolução Constitucionalista (09/07, SP) |
| `municipais` | Aniversário de São Paulo (25/01) |
| `facultativos` | Carnaval (segunda e terça) e Corpus Christi |
| `extras` | o que você quiser acrescentar, data a data |

É um arquivo comum, feito para ser editado — e separado assim justamente para
isso: quem não é de São Paulo esvazia `estaduais` e `municipais` sem encostar
nos nacionais. Cada item aceita `"dia": "12-25"` (todo ano), `"pascoa": -2`
(dias a contar do Domingo de Páscoa, que o programa calcula sozinho) ou
`"data": "2026-12-24"` (uma vez só).

## Interface

- Tema claro e escuro, seguindo o sistema, com alternância manual (tecla `T`).
- Resumo do período no topo: lançado, esperado, **saldo** e dias completos.
- Atalhos de período: esta semana (padrão), semana passada, mês de fechamento,
  últimos 30 dias.
- Autocompletar de issue por chave, resumo **ou número do pedido**.
- **Comentário de várias linhas**: `Enter` quebra linha, `Ctrl`+`Enter` salva.
  No modo Tempo o texto vai inteiro para a descrição do worklog; no modo Jira
  nativo cada linha vira um parágrafo do comentário.
- Duração aceita `1h30`, `2h30m`, `1.5h`, `90m` ou `8` (um número sozinho são
  horas; depois de outro valor, são minutos).
- Todo dia aberto tem o botão de lançar. Num dia que já tem horas mas não
  fechou a meta, ele vira *Lançar as 7h00 restantes* e abre a caixa com a
  diferença já preenchida.
- Várias datas no mesmo lançamento, com o total calculado enquanto você digita.
  Cada linha nova já vem com o **dia útil anterior** preenchido, então lançar
  três dias seguidos são três cliques — sem abrir o calendário.
- Cada linha mostra **como fica o dia**: `7h00 + 1h00 = 8h00 de 8h00 · meta
  fechada`, recalculado a cada tecla. Em verde quando fecha a meta, em âmbar
  quando passa dela (`7h00 + 5h00 = 12h00 de 8h00 · 4h00 acima da meta`), para
  o estouro aparecer antes de salvar. Duas linhas no mesmo dia somam. Dias de
  outro período são consultados na hora, então a conta nunca sai de um dia que
  não está na tela.
- Atalhos de teclado: `N` lançar, `R` recarregar, `T` tema,
  `Ctrl`+`Enter` salvar, `Esc` fechar.
- O período fica na URL, então atualizar a página cai no mesmo lugar.

## O que mudou em relação à versão 2

### A tela não recarrega mais

Na v2, lançar ou excluir horas era um `POST` que terminava num `redirect`: o
navegador recarregava a página inteira, refazia todas as chamadas ao Jira e,
quando dava erro, a mensagem só aparecia depois desse recarregamento — com o
formulário já limpo.

Na v3 o servidor entrega uma página e, daí em diante, só JSON:

| Ação | Antes | Agora |
|---|---|---|
| Lançar horas | POST → redirect → recarrega tudo | `fetch` → a linha aparece na hora |
| Excluir | POST → redirect → recarrega tudo | some na hora, volta se o servidor recusar |
| Trocar o período | recarrega a página | só a lista é redesenhada |
| Erro | recarrega e mostra no topo | aviso na própria tela, com o que você digitou preservado |
| Erro parcial (3 dias, 1 falhou) | tudo ou nada na mensagem | os que entraram ficam; só os que falharam voltam para correção |

A exclusão e o lançamento são **otimistas**: a tela muda imediatamente e o
servidor é conferido em segundo plano. Se ele recusar, o estado anterior volta e
o motivo aparece num aviso.

### Mais rápido

- **Sem framework de CSS/JS e sem CDN.** A v2 baixava Bootstrap (CSS + JS) de
  `cdn.jsdelivr.net` em toda página. A v3 serve um CSS e um JS próprios, menores
  que o Bootstrap sozinho, e abre mesmo quando a rede corporativa implica com CDN.
- **Chamadas ao Jira em paralelo.** Resolver o resumo de 40 issues custava 40
  idas e voltas em série; agora são 8 ao mesmo tempo
  (`JIRA_TRACKER_PARALLEL` ajusta).
- **Cache de issues** por 10 minutos (`JIRA_TRACKER_CACHE_TTL`): a mesma issue
  aparece em vários dias e não precisa ser consultada de novo.
- **Conexão reaproveitada** (`requests.Session` por thread), poupando o
  handshake TLS a cada chamada.
- **Lista do Portal de Pedidos em cache** por 2 minutos e carregada em segundo
  plano, para a aba e o autocompletar abrirem instantâneos.
- Estáticos versionados pela data do arquivo: cache agressivo no navegador que
  mesmo assim pega a versão nova quando você edita o CSS.

### Sem janela de terminal

A v2 vivia dentro de um console: a janela preta ficava aberta o tempo todo e
fechá-la era a única forma de parar. Na v3 o atalho aponta para `pythonw.exe`,
o Flask roda em uma thread e o processo é representado pelo ícone da bandeja.

## Segurança

| O quê | Onde |
|---|---|
| Token do Jira e do Tempo | Windows Credential Manager |
| URL, e-mail e preferências | `%USERPROFILE%\.jira-time-tracker\config.json` |
| Registro de erros | `%USERPROFILE%\.jira-time-tracker\tracker.log` |
| Marca da instalação | `%USERPROFILE%\.jira-time-tracker\instalado.json` |

Nada de credencial na pasta do projeto: ela pode ser sincronizada pelo OneDrive
corporativo, e o histórico de versões guarda cópias mesmo de arquivos apagados.
A tela de configuração nunca devolve um token já salvo — ela só informa que
existe, e um campo em branco significa "manter o que está lá".

- Nunca envie tokens por e-mail, chat ou captura de tela.
- Token que já esteve em arquivo dentro do OneDrive deve ser considerado
  vazado: revogue e gere outro.

## Estrutura

```
start.bat           ponto de entrada: instala/atualiza e depois so abre
instalar.ps1        ambiente + dependencias + icone + atalho
app.py              rotas: duas paginas e a API JSON
tray.py             icone na area de notificacao e Flask em segundo plano
tracker_core.py     tudo que fala com Jira e Tempo (cache + paralelismo)
credentials.py      cofre de credenciais e preferencias, fora da pasta
feriados.py         calcula os feriados do periodo (Pascoa inclusive)
feriados.json       a tabela de feriados - so um aviso, edite a vontade
templates/          index.html e config.html — so a casca
static/css/app.css  design system completo, claro e escuro
static/js/app.js    estado, renderizacao e chamadas a API
assets/             o .ico usado pelo atalho e pela bandeja
```

## Variáveis de ambiente

| Variável | Padrão | Para quê |
|---|---|---|
| `PORT` | 5000 | Porta do servidor local |
| `NO_BROWSER` | — | `1` não abre o navegador sozinho |
| `JTT_NO_TRAY` | — | `1` roda no terminal, sem ícone na bandeja |
| `JIRA_PROJECT_KEY` | `PEDIDO` | Projeto da aba de issues |
| `JIRA_TRACKER_PARALLEL` | 8 | Chamadas simultâneas ao Jira |
| `JIRA_TRACKER_CACHE_TTL` | 600 | Segundos de cache de issue |
| `ISSUES_CACHE_TTL` | 120 | Segundos de cache da lista do projeto |
| `USE_SYSTEM_PROXY` | — | `1` respeita o proxy do sistema |

## Problemas comuns

**Erro de autenticação:** confira se o e-mail pertence à mesma conta que gerou o
token e se o token não expirou. A tela de configuração testa contra o Jira (e
contra o Tempo, se houver token) *antes* de gravar.

**"O servidor local não respondeu":** o tracker foi encerrado. Abra pelo atalho
de novo.

**Não aparece nada e o ícone some:** veja
`%USERPROFILE%\.jira-time-tracker\tracker.log`. Para ver o erro na hora, rode
pelo terminal:

```powershell
$env:JTT_NO_TRAY=1; %USERPROFILE%\.jira-time-tracker\venv\Scripts\python.exe app.py
```

**Porta ocupada:** se a 5000 estiver com outro programa (a versão 2, por
exemplo), a v3 usa a próxima livre — 5001, 5002 — e anota qual em
`%USERPROFILE%\.jira-time-tracker\port`.

**Refazer só o ícone e o atalho:**

```powershell
powershell -ExecutionPolicy Bypass -File instalar.ps1 -AtalhoApenas
```
