# lukasmax — TikTok para Instagram Reels

Republica os videos do TikTok [@_lukasmax](https://www.tiktok.com/@_lukasmax) como
Reels no Instagram, com legendas geradas por IA e agendamento automatico de dois
posts por dia.

Tudo pela **API oficial do Instagram**. Nenhuma automacao de navegador, nenhuma
senha, em nenhum momento.

## Como funciona

O preparo roda no seu Mac; a publicacao roda sozinha no GitHub Actions.

```
Mac                                          GitHub Actions (a cada 30 min)
────────────────────────────────────         ──────────────────────────────
inventaria e ranqueia o perfil
baixa os videos sem marca-d'agua
gera legendas com IA (voce aprova)
normaliza para o formato de Reels
agenda nos melhores horarios          ──▶    reconcilia o que ficou preso
sobe os videos como assets de Release        publica o que estiver vencido
```

**Por que um cron e nao um agendamento nativo:** a Content Publishing API do
Instagram nao tem agendamento. `media_publish` publica sempre na hora. O
agendamento tem que ser externo, e e isso que o workflow faz.

## Comecando

```bash
uv sync --extra local --extra dev
cp .env.example .env     # preencha as credenciais

uv run lukasmax audit-tiktok        # inventaria e ranqueia
uv run lukasmax download-archive    # baixa o acervo
uv run lukasmax draft-captions --top 40
uv run lukasmax review-captions     # confira antes de aprovar
uv run lukasmax approve-caption --all
uv run lukasmax prepare --all-approved
uv run lukasmax plan-queue --days 14 --per-day 2
uv run lukasmax pick-covers         # capa: sem isso, o Instagram usa o quadro 0
uv run lukasmax host-media --tag media-v1
uv run lukasmax doctor              # checagem final
git push
```

Passo a passo detalhado em [docs/OPERATIONS.md](docs/OPERATIONS.md).

## As garantias

**Nada e publicado por acidente.** Sao duas travas independentes: a variable
`PUBLISH_ENABLED` do repositorio precisa ser `true`, e apenas itens em
`scheduled` sao elegiveis.

**Nada e publicado duas vezes.** `media_publish` nao e idempotente, e um post
duplicado nao tem desfazer. Antes de criar o container, a execucao marca o item
como `publishing` e da push -- um push rejeitado aborta o run sem publicar. Se
algo morrer no meio, `reconcile` pergunta a Meta o que realmente aconteceu, em
vez de tentar de novo as cegas.

**Nenhuma marca-d'agua.** Formatos marcados como `watermark` nunca sao
escolhidos, e a copia normalizada perde os metadados de origem
(`-map_metadata -1`).

**Nenhuma legenda entra sem revisao.** Um validador deterministico checa
tamanho, gancho, quantidade de hashtags e termos proibidos, e voce aprova cada
uma. A legenda e congelada na fila: editar o arquivo depois nao altera um post
ja agendado.

**Segredos ficam onde devem.** A chave da Anthropic vive so no `.env` do Mac. O
runner nunca chama a IA, entao nunca precisa dela.

## Onde as coisas ficam

| Caminho | Conteudo |
|---|---|
| `data/tiktok_inventory.json` | inventario cru do perfil |
| `data/tiktok_ranking.csv` | videos ordenados por engajamento |
| `data/captions/<id>.json` | legenda, hashtags e status de aprovacao |
| `data/queue.json` | a agenda -- quem publica quando |
| `data/slots.json` | os horarios candidatos e seus pesos |
| `data/publish_log.jsonl` | trilha de auditoria, append-only |
| `media/` | os videos (fora do Git) |
| `reports/` | validacoes de midia e erros de download |

## Desenvolvimento

```bash
uv run pytest              # 57 testes
uv run ruff check src tests
uv run ruff format src tests
```

Os testes P0 cobrem o unico erro irreversivel do sistema: publicar o mesmo Reel
duas vezes. Eles rodam o cron repetidamente sobre a mesma fila e simulam crashes
em cada ponto onde uma retentativa ingenua duplicaria o post.
