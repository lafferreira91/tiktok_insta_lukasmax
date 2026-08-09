# Operacao

## Instalacao

```bash
uv sync --extra local --extra dev   # no Mac: tudo
uv sync                             # no CI: so o nucleo
```

O extra `local` traz yt-dlp, ffmpeg, PyAV e o SDK da Anthropic. O job de
publicacao nao instala nada disso: ele so fala HTTP com a Graph API.

Copie `.env.example` para `.env` e preencha. `ANTHROPIC_API_KEY` fica **so no
Mac** -- o runner publica legendas ja congeladas na fila e nunca chama a IA.

## O fluxo, do inicio ao fim

Tudo antes de `git push` roda no seu Mac. Dali em diante o cron do GitHub
Actions assume.

```
audit-tiktok → download-archive → draft-captions → review-captions
    → approve-caption → prepare → plan-queue → pick-covers → host-media
    → doctor → git push
         ↓ [CI, a cada 30 min]
    reconcile → publish-due
```

### 1. Inventariar e ranquear

```bash
uv run lukasmax audit-tiktok
```

Le o perfil, salva `data/tiktok_inventory.json` e gera `data/tiktok_ranking.csv`
ordenado por um score que pondera views, likes, comentarios e shares.

O TikTok limita requisicoes: um HTTP 429 aqui e transitorio, tente de novo mais
tarde. O inventario anterior nao e sobrescrito quando a chamada falha.

### 2. Baixar o acervo

```bash
uv run lukasmax download-archive            # tudo que falta
uv run lukasmax download-archive --limit 3  # teste
```

Retomavel: pula o que ja esta em `data/downloaded.txt`, e as falhas registradas
em `reports/download_errors.json` sao naturalmente retentadas na proxima
execucao. Espaça os downloads em 4 segundos e recua progressivamente diante de
um 429.

Formatos com marca-d'agua nunca sao escolhidos -- `best_unwatermarked_format`
descarta qualquer formato marcado como `watermark` ou com id `download`.

### 3. Gerar as legendas

```bash
uv run lukasmax draft-captions --top 40
uv run lukasmax review-captions
uv run lukasmax approve-caption --all
```

`draft-captions` manda apenas metadados (titulo, musica, metricas) para o
modelo -- nunca o video. O resultado vai para `data/captions/<id>.json`.

Um cache por fingerprint faz re-execucoes serem gratuitas: so o que mudou de
metadados ou de versao do prompt e regerado. Use `--force` para ignorar o cache.

`review-captions` imprime cada legenda com contagem de caracteres e avisos.
Voce pode editar o JSON a mao antes de aprovar.

`approve-caption` **recusa** legendas com avisos do validador (tamanho,
quantidade de hashtags, termos proibidos). Use `--force` se souber o que esta
fazendo.

### 4. Normalizar a midia

```bash
uv run lukasmax prepare --all-approved
uv run lukasmax prepare --id 7278034913729907974
```

Transcodifica para H.264/AAC 1080x1920 30fps 48kHz com `-map_metadata -1`, que
remove os metadados da plataforma de origem. O resultado vai para
`media/ready/` e so passa se a validacao completa der certo.

### 5. Agendar

```bash
uv run lukasmax plan-queue --days 14 --per-day 2 --dry-run
uv run lukasmax plan-queue --days 14 --per-day 2
```

Casa os videos elegiveis com os horarios de `data/slots.json`, em ordem de
ranking. Um video so e elegivel se tiver **midia preparada** e **legenda
aprovada** -- o resumo mostra quantos foram recusados e por que.

Planeje em janelas de 14 dias, nao o acervo inteiro: replanejar fica barato
quando os horarios mudarem.

A legenda e **copiada** para o item da fila. Editar o arquivo depois nao muda um
post ja agendado.

### 6. Escolher a capa

```bash
uv run lukasmax pick-covers
```

O Instagram usa `thumb_offset` para a capa do Reel, e **o padrao e o quadro 0**.
Num clipe de TikTok esse quadro e quase sempre transicao, movimento borrado ou
uma piscada -- foi assim que o primeiro post saiu com o olho semicerrado.

`pick-covers` pontua 24 quadros da janela util (a partir de 1s, ate 12s) por
nitidez e exposicao, grava o melhor offset no item da fila e deixa dois JPEGs em
`reports/covers/`: a capa escolhida e um contact sheet com todos os candidatos.

Ele **nao** detecta olho fechado -- isso exigiria um modelo de marcos faciais.
O que ele faz e nunca usar o quadro 0 e preferir quadros estaveis, o que resolve
a maioria dos casos. Se discordar de alguma, olhe o contact sheet e fixe a mao:

```bash
uv run lukasmax set-cover --id 7276110548935249158 --at 4.2
```

O valor fica congelado na fila como `media.thumb_offset_ms`; o runner nao
decodifica video nenhum. Post ja publicado nao entra: a API nao troca a capa
depois de publicado.

### 7. Hospedar e conferir

```bash
uv run lukasmax host-media --tag media-v1
uv run lukasmax doctor --check-assets
git add data/ && git commit -m "Agenda das proximas duas semanas" && git push
```

`host-media` sobe cada MP4 como asset de Release e grava a URL na fila. A Meta
baixa desse link direto; o runner nunca transfere o arquivo.

`doctor` e a checagem final: token valido, quota, todo item agendado com legenda
e asset acessivel, e nenhum horario duplicado. Sai com codigo diferente de zero
se algo estiver errado -- e o mesmo comando que o CI roda antes de publicar.

## Publicacao (automatica)

O workflow `publish.yml` roda a cada 30 minutos, reconcilia o que ficou preso e
publica no maximo **um** item por execucao.

**Duas travas independentes** impedem publicacao acidental:

1. A variable `PUBLISH_ENABLED` do repositorio precisa ser exatamente `true`.
2. So itens em `scheduled` (ou `retry` com backoff vencido) sao elegiveis.

### Sequencia de estreia

1. `PUBLISH_ENABLED=false` e rode o workflow manualmente com `dry_run` -- ele
   imprime o que faria.
2. Rode manualmente com `dry_run` desligado e `max_per_run=1`: um post real.
3. Confira o permalink no perfil.
4. So entao mude `PUBLISH_ENABLED` para `true` e deixe o cron assumir.
5. Observe tres dias antes de planejar janelas maiores.

## Medir o que foi publicado

```bash
uv run lukasmax collect-insights --dry-run   # o que coletaria
uv run lukasmax collect-insights             # coleta e grava
uv run lukasmax audience                     # quem segue e quando esta online
```

Roda sozinho em `insights.yml`, uma vez por dia. **Insight nao e recuperavel
depois**: a Meta nao devolve retroativamente qual era o alcance nas primeiras
24h, entao cada dia sem coletar e um dia perdido para sempre.

Cada Reel e medido duas vezes, **as 24h e aos 7 dias de vida**. A comparacao e
por idade, nao por data de coleta -- comparar um post de dois dias com um de
sessenta mede idade, nao qualidade, e o numero continua parecendo razoavel.

`data/insights.csv` e append-only, uma linha por `(media_id, idade)`. A chave ja
presente no arquivo *e* o registro de "ja coletei", entao rodar duas vezes no
mesmo dia nao duplica nada e nada precisa ser escrito em `queue.json` -- que e
disputado pelo job de publicacao.

Quando houver umas 8 amostras por horario, `scheduling.tune_weights` (ja escrito
e testado) pode reponderar o pool. Nao ha comando para isso ainda, de proposito:
a 2 posts/dia isso leva meses, e pesos novos **nao mexem em quem ja tem horario
gravado** -- exigiriam `reschedule` depois.

## Reels de teste

```bash
uv run lukasmax mark-trials --dry-run           # o que marcaria
uv run lukasmax mark-trials --limit-days 1      # so o primeiro dia
uv run lukasmax mark-trials                     # todos os dias
uv run lukasmax mark-trials --clear             # desfaz
```

Marca **um dos dois posts de cada dia** como reel de teste: ele vai apenas para
quem **nao** segue o perfil, e nao aparece no grid. Com 170 seguidores, quase
todo alcance possivel esta fora deles.

Continuam dois posts por dia, com **videos diferentes** -- nada e duplicado e o
consumo do acervo nao muda. Publicar o mesmo video nas duas versoes seria pior:
o reel normal tambem e distribuido para nao seguidores, entao as copias
disputariam o mesmo publico com o mesmo conteudo.

O escolhido e o de **rank mais baixo do par**, e a estrategia e `MANUAL`: nada
sobe sozinho para o perfil. Se um teste explodir, a graduacao e um toque no app.

**A conta nao tem essa permissao.** Testado ao vivo em 09/08/2026: o mesmo
video, na mesma chamada, com `trial_params` devolve `400: Application does not
have permission for this action`, e sem ele o container e criado normalmente.
Nao e bug do codigo. A Meta nao documenta como obter a permissao.

Por isso o comando **recusa por padrao** -- marcar um item aqui significa um post
que falha em producao. Se a permissao aparecer, `--force` libera.

**Libere em etapas.** `reconcile` detecta "o run morreu depois de publicar"
cruzando com `GET /me/media`, e nao esta confirmado que um reel de teste aparece
nessa lista. Se nao aparecer, um crash na hora errada vira post duplicado -- a
unica falha irreversivel do sistema. Por isso o primeiro vai com
`--limit-days 1`: publique, confirme que ele aparece em `/me/media`, e so entao
rode sem limite.

## O token vence em 60 dias

Este e o unico prazo que pode matar a automacao em silencio. O token do Painel
de Apps vale 60 dias e, **depois de vencido, nao existe renovacao** -- so gerar
outro a mao no painel da Meta.

`token.yml` roda dia 1 de cada mes e devolve o relogio para 60 dias. Com essa
cadencia a folga nunca cai abaixo de 30 dias, mesmo que uma execucao falhe.

Ele exige o secret **`SECRETS_PAT`**, um token fine-grained do GitHub com
permissao `Secrets: Read and write` neste repositorio. O `GITHUB_TOKEN` do
Actions nao escreve secrets, entao nao ha como evitar esse passo manual. Sem o
PAT o job falha alto -- de proposito, porque renovar o token e perder o valor
novo seria pior: o antigo continuaria valendo so ate vencer.

Para renovar a mao:

```bash
uv run lukasmax refresh-token           # mostra a validade, esconde o token
uv run lukasmax refresh-token --print-token | gh secret set INSTAGRAM_ACCESS_TOKEN
```

O `--print-token` existe so para o pipe acima. O log do Actions neste
repositorio e publico, e um token vazado vale ate ser revogado a mao.

## Mudar de ideia depois de agendar

A legenda e o horario ficam **congelados** no item da fila. Isso e proposital:
editar `data/captions/` ou `data/slots.json` nunca deve alterar sozinho um post
ja agendado. Trocar de propositio tem caminho explicito:

```bash
uv run lukasmax refresh-captions          # recopia as legendas aprovadas
uv run lukasmax reschedule                # redistribui sobre o pool atual
uv run lukasmax pick-covers --force       # reescolhe as capas
```

Nenhum dos tres toca em `published`, `publishing` ou `scheduled_external`.
Depois de qualquer um deles: `doctor`, `git commit`, `git push`.

## Quando algo da errado

**Item preso em `publishing`** -- um run morreu no meio.
`uv run lukasmax reconcile` resolve: se o post ja tinha saido, marca como
publicado; se nao, reagenda. Nunca edite o status a mao, porque `media_publish`
nao e idempotente e um item reaberto errado vira post duplicado.

**Item em `failed`** -- as tentativas acabaram ou o erro era permanente. Veja
`last_error`, corrija a causa, e devolva para a fila:

```bash
uv run lukasmax requeue --dry-run
uv run lukasmax requeue --note "por que"
uv run lukasmax requeue --ids 7146966322545446149
```

Nunca edite o JSON a mao: `media_publish` nao e idempotente e um item reaberto
errado vira post duplicado. **Corrija a causa antes** -- se o item carregava uma
marca que causou a falha, limpe primeiro (ex.: `mark-trials --clear`), senao ele
volta para a fila e falha de novo do mesmo jeito.

**Downloads falhando com "Unable to extract universal data for rehydration"** --
falta impersonation. Confirme com `uv run yt-dlp --list-impersonate-targets`:
se os alvos aparecem como `(unavailable)`, a versao do `curl-cffi` esta fora da
faixa que o yt-dlp aceita. Rode `uv sync --extra local` para restaurar.

**Token perto de expirar** -- o token de longa duracao vale 60 dias.
`lukasmax status` mostra o estado, e `refresh_long_lived_token()` estende por
mais 60 (exige token com mais de 24h de vida).

## Estados da fila

| Estado | Escrito por | Significado |
|---|---|---|
| `planned` | local | tem horario, midia ainda nao preparada |
| `prepared` | local | MP4 normalizado e validado, legenda congelada |
| `hosted` | local | asset no Release |
| `scheduled` | local | **unico estado que o CI enxerga** |
| `publishing` | CI | claim feito -- invisivel para outras execucoes |
| `published` | CI | terminal |
| `retry` | CI | falha transitoria, aguardando backoff |
| `failed` | CI | tentativas esgotadas ou erro permanente |
| `skipped` | local | fora de rotacao, preservado |
| `scheduled_external` | legado | o piloto agendado a mao, terminal |

Transicoes ilegais levantam excecao. Comandos locais escrevem apenas os estados
locais; o CI apenas os dele. Nenhum campo e escrito pelos dois lados.
