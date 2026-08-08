# Automacao TikTok → Instagram de @_lukasmax

Projeto gratuito para inventariar os TikToks públicos, ranquear os melhores,
baixar somente formatos sem marca-d'água e publicar Reels pela API oficial do
Instagram.

## Segurança

- A publicação nasce desativada (`PUBLISH_ENABLED=false`).
- Tokens ficam somente em `.env` ou nos GitHub Secrets.
- A fila só publica itens com `status: "ready"`.
- Formatos identificados como `watermarked` nunca são escolhidos.

## Uso local

```bash
uv sync --extra dev
uv run lukasmax audit-tiktok
uv run lukasmax prepare-pilot
uv run lukasmax status
uv run pytest
```

Para publicar são necessários `INSTAGRAM_USER_ID` e
`INSTAGRAM_ACCESS_TOKEN`, obtidos com Instagram API with Instagram Login para
uma conta profissional.

O primeiro candidato está em `data/queue.json` com estado
`scheduled_external`: foi agendado pelo Meta Business Suite somente para o
Instagram em 12/08/2026 às 18:00 (`America/Sao_Paulo`). A verificação de direitos
autorais da plataforma foi aprovada. Consulte `docs/OPERATIONS.md` para os
detalhes e a recuperação manual.

## Arquivos gerados

- `data/tiktok_inventory.json`: metadados públicos extraídos do perfil.
- `data/tiktok_ranking.csv`: ranking reproduzível dos melhores vídeos.
- `media/tiktok/`: vídeos e metadados baixados sem marca-d'água.
- `media/ready/`: arquivos normalizados e prontos para a API do Instagram.
- `data/downloaded.txt`: registro retomável; evita downloads repetidos.
- `reports/download_errors.json`: itens que o TikTok não permitiu baixar.
- `reports/frames/`: quadros usados para inspeção visual.

A pasta `media/` está ignorada pelo Git porque os arquivos são grandes, mas
permanece dentro deste diretório no computador.
