# Estado atual — verificacao de 08/08/2026

Este arquivo responde tres perguntas: **esta tudo certo?**, **em que horarios os
posts saem?** e **o que pode dar errado e quando?**

Nada aqui exige acao. E um retrato do sistema no momento em que ele passou a
rodar sozinho.

## 1. Verificacao

Tudo abaixo foi conferido de verdade, chamando a API ou lendo o arquivo, nao por
memoria.

| Item | Resultado |
|---|---|
| `doctor --check-assets` | `ok: true`, zero problemas |
| Itens agendados | **192** |
| Assets conferidos no Release | **192 de 192** (HTTP 200 + tamanho batendo) |
| Testes | **158 passando** |
| Lint (`ruff`) | limpo |
| Git | `5ebeca8`, arvore limpa, igual ao `origin/main` |
| Conta conectada | `_lukasmax`, tipo `MEDIA_CREATOR` |
| Quota da Meta | 1 de 100 usados nas ultimas 24h |
| Secrets do repo | `INSTAGRAM_ACCESS_TOKEN`, `INSTAGRAM_USER_ID`, `SECRETS_PAT` |
| Variable `PUBLISH_ENABLED` | `true` |
| Ultima renovacao de token | sucesso, ponta a ponta |

**Acervo:** 194 videos baixados, 194 normalizados (zero falhas), 193 legendas
aprovadas, 192 na fila. O que sobra: 1 ja publicado a mao (o piloto) e 1 sem
legenda aprovada.

### Uma nota sobre o `doctor` local

O `doctor` rodando no Mac imprime `PUBLISH_ENABLED nao esta true`. **Isso esta
correto e nao e um problema.** Ele le a variavel de ambiente do seu terminal, que
de fato nao existe aqui. Quem publica e o runner do GitHub, e la a variable do
repositorio esta `true` — confirmado pela API, nao pelo `gh variable list`, que
serve cache velho.

O aviso e proposital: e a garantia de que rodar `doctor` no Mac nunca publica
nada.

## 2. Os horarios

**Dois posts por dia.** Os horarios nao sao fixos — cada dia sorteia dois slots
de um conjunto, e o horario exato varia ate 20 minutos para os dois lados.

### O conjunto de horarios

| Slot | Dias | Hora base | Peso | Por que |
|---|---|---|---|---|
| `wd-morning` | seg–sex | 09:15 | 1.00 | 09–12 e a 2ª melhor faixa dele no TikTok (38k medianas, n=51) |
| `wd-lunch` | seg–sex | 12:15 | 0.80 | almoco; 32,5k medianas, a mais fraca das diurnas |
| `wd-afternoon` | seg–sex | 17:15 | 1.10 | **15–18 e a melhor faixa dele** (40k medianas, n=51) |
| `wd-commute` | seg–sex | 18:45 | 0.95 | volta do trabalho; 34k medianas |
| `we-late-am` | sab–dom | 10:30 | 1.00 | fim de semana acorda tarde; domingo rende 40,4k |
| `we-afternoon` | sab–dom | 16:30 | 1.05 | mesma faixa da tarde que lidera nos dias uteis |
| `we-evening` | sab–dom | 18:30 | 0.95 | inicio da noite, antes da queda depois das 21h |

Os pesos vem do **historico real dele no TikTok** (199 posts), nao de artigo
generico de blog. Vale destacar dois pontos em que os dados dele contrariam o
conselho padrao: **domingo e um dos melhores dias** para ele, e **depois das 21h
cai** — por isso nada e agendado a noite, que era exatamente a sua intuicao.

### Como fica na pratica

Nenhum horario se repete todo dia: um `deque.rotate` gira o conjunto, entao o
slot mais forte ganha frequencia mas o segundo varia em ciclo. O jitter e
deterministico (`sha256(data:slot) % 41 - 20`), o que significa reproduzivel mas
nunca no minuto cravado — dois posts nunca saem 12:15 em ponto.

Distribuicao real dos 192 posts por hora:

```
08h   6  ███
09h  29  ██████████████
10h  27  █████████████
11h   4  ██
12h  31  ███████████████
16h  19  █████████
17h  33  ████████████████
18h  39  ███████████████████
19h   4  ██
```

**Os proximos:**

| Quando | Slot |
|---|---|
| dom 09/08, 10:13 | `we-late-am` |
| dom 09/08, 18:16 | `we-evening` |
| seg 10/08, 12:13 | `wd-lunch` |
| seg 10/08, 18:31 | `wd-commute` |
| ter 11/08, 12:26 | `wd-lunch` |
| ter 11/08, 17:12 | `wd-afternoon` |

**Ate quando:** o ultimo post agendado e **13/11/2026 as 09:20**. Sao 97 dias, 95
deles com dois posts e 2 com um so.

### O fuso: tudo em horario de Sao Paulo

Os horarios da tabela acima sao **horario de Brasilia**. Isso importa porque o
runner do GitHub roda em **UTC**, e um erro de fuso aqui nao daria erro nenhum —
so postaria tres horas fora da hora, calado.

Os quatro pontos foram conferidos contra os 192 itens reais da fila:

| O que | Como esta |
|---|---|
| O horario e montado | com `ZoneInfo("America/Sao_Paulo")`, nao offset fixo |
| Todo `scheduled_at` na fila | tem offset explicito — **zero** sem fuso |
| O offset gravado | bate com o de Sao Paulo em todas as 192 datas |
| Convertidos para UTC | **192 de 192** caem dentro da janela do cron |

E o comparador do runner, simulado com o processo em `TZ=UTC` no primeiro post
(09/08 as 10:13 SP = 13:13 UTC):

```
   1h antes  (12:13 UTC / 09:13 SP):  0 devidos
1 min antes  (13:12 UTC / 10:12 SP):  0 devidos
    na hora  (13:13 UTC / 10:13 SP):  1 devido   ←
1 min depois (13:14 UTC / 10:14 SP):  1 devido
```

Ele vira no minuto certo. O motivo e que os dois lados da comparacao carregam
fuso: `queue.now()` devolve UTC-aware e o `scheduled_at` tem `-03:00`. Python
converte sozinho — e um `scheduled_at` sem fuso levantaria excecao em vez de
comparar errado, por isso `_parse` trata timestamp sem offset como UTC de
proposito, em vez de adivinhar o fuso de quem escreveu.

**A unica dependencia de fuso fixo** e o `OFFSET_UTC = -3` do teste
`test_cron_cobre_os_slots.py`. O Brasil nao tem horario de verao desde 2019; se
voltar, o **agendamento continua certo sozinho** (o `ZoneInfo` ajusta), mas a
janela do cron precisaria comecar uma hora antes — o slot das 09:15 com jitter
minimo cairia as 10:55 UTC, fora dos `11-23`. E por isso que a constante existe
com comentario em vez de estar escondida no codigo.

### Por que o horario e um piso, nao uma promessa

O cron do GitHub Actions e best-effort: ele atrasa sob carga e, pela propria
documentacao deles, jobs agendados **podem ser descartados** quando a carga esta
alta. O `scheduled_at` da fila significa "nao antes disso".

Na pratica o atraso e de **no maximo 29 minutos, mediana de 15** — conferido
contra os 192 itens reais da fila. O cron acorda nos minutos 13 e 43 de cada
hora, das 08h as 20h59.

## 3. O que roda sozinho

```
a cada 30 min, 08h-21h    publish.yml    reconcilia + publica 1 item vencido
dia 1 de cada mes, 03h17  token.yml      renova o token por mais 60 dias
```

O `publish.yml` publica **no maximo um item por execucao**. Isso limita o raio de
dano de qualquer bug a um unico post.

Depois de publicar, ele commita a fila de volta no repositorio. Esse commit tem
uma segunda funcao: workflows agendados em repo publico sao **desativados
automaticamente apos 60 dias sem atividade no repositorio**, e o proprio commit
do bot mantem o cron vivo.

### As travas

**Nada e publicado por acidente.** Sao duas condicoes independentes: a variable
`PUBLISH_ENABLED` precisa ser exatamente `true`, e so itens em `scheduled` sao
elegiveis.

**Nada e publicado duas vezes.** `media_publish` nao e idempotente e post
duplicado nao tem desfazer. Antes de criar o container o runner marca o item como
`publishing` e da push — **push rejeitado aborta o run sem publicar**. Se algo
morrer no meio, o `reconcile` pergunta a Meta o que de fato aconteceu em vez de
tentar de novo as cegas.

**A chave da Anthropic nao esta no GitHub.** As legendas sao congeladas na fila
antes do push, entao o runner nunca chama IA e nunca precisa da chave.

## 4. Sobre limite do GitHub

**Nao ha limite a respeitar.** Repositorios publicos tem minutos ilimitados de
Actions — a cota de 2.000 min/mes vale so para repos privados. Foi por isso que o
endpoint de billing devolveu 404: nao existe cota sendo contada.

Uso ate aqui: 36 execucoes de ~15 segundos cada, menos de 15 minutos no total.

A reducao de 48 para 26 execucoes diarias foi por **ruido, nao por economia**: as
22 execucoes da madrugada so encontravam fila vazia, ja que nenhum slot passa das
18:45.

## 5. O que pode dar errado, e quando

Em ordem de quando acontece.

### 13/11/2026 — a fila acaba

Depois do ultimo post agendado, o cron continua rodando e nao encontra nada. Nao
quebra, so para de postar em silencio. Para continuar: baixar os videos novos do
TikTok e rodar o pipeline de novo (`docs/OPERATIONS.md`).

### A cada 60 dias — o token

Este e o unico prazo que pode matar a automacao **em silencio**. O token vale 60
dias e, depois de vencido, **nao existe renovacao** — so gerar outro a mao no
painel da Meta.

O `token.yml` roda dia 1 de cada mes e devolve o relogio para 60 dias, entao a
folga nunca cai abaixo de 30 dias, mesmo que uma execucao falhe. Ja foi provado
funcionando ponta a ponta.

Ele depende do secret `SECRETS_PAT`. **Tokens fine-grained do GitHub tambem
expiram** — se voce escolheu prazo na hora de criar, marque a data. Sem o PAT, o
job falha alto de proposito: renovar o token e nao conseguir grava-lo seria pior,
porque a renovacao ja teria sido gasta.

### Se um post falhar

Falha transitoria vira `retry` com backoff (30 min → 2h → 6h) e depois `failed`.
Erro permanente (token invalido, video recusado) vai direto para `failed` sem
gastar tentativas. Nada disso trava a fila: o proximo item sai normalmente.

### Se voce quiser mudar algo

Legenda e horario ficam **congelados** no item da fila — editar
`data/captions/` ou `data/slots.json` de proposito nao altera sozinho um post ja
agendado. Trocar tem caminho explicito:

```bash
uv run lukasmax refresh-captions   # recopia as legendas aprovadas
uv run lukasmax reschedule         # redistribui sobre os horarios atuais
uv run lukasmax pick-covers --force
```

Nenhum dos tres toca em `published`, `publishing` ou `scheduled_external`.

## 6. Onde olhar se quiser conferir

| O que | Onde |
|---|---|
| Se os posts estao saindo | aba **Actions** do repositorio |
| O que ja saiu | `data/publish_log.jsonl` (append-only) |
| A agenda inteira | `data/queue.json` |
| O perfil | instagram.com/_lukasmax |

Um comando resume tudo:

```bash
uv run lukasmax status
```

---

## Resumo

**Sim, esta tudo certo, e nao, voce nao precisa mais conferir.**

O primeiro post automatico sai **domingo, 09/08 as 10:13**. Depois disso sao dois
por dia ate 13/11.

A unica coisa que vale marcar no calendario e **novembro**, quando a fila acaba.
