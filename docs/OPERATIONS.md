# Operação e recuperação

## Preparar o ambiente

```bash
uv sync --extra dev
```

## Atualizar inventário e ranking

```bash
uv run lukasmax audit-tiktok
```

Se a página do TikTok interromper uma extração, o último JSON válido pode ser
fornecido com `--input caminho.json`.

## Baixar todo o acervo sem marca-d'água

```bash
uv run lukasmax download-archive
```

O comando:

1. baixa primeiro os vídeos mais bem ranqueados;
2. rejeita o formato que o TikTok identifica como `watermarked`;
3. prefere a maior resolução limpa disponível;
4. salva um `.info.json` ao lado de cada vídeo;
5. registra sucessos em `data/downloaded.txt`;
6. pode ser interrompido e retomado sem repetir arquivos.

No estado atual, não é necessário executar esse comando: o acervo em massa foi
pausado para validar primeiro o piloto completo. Dois arquivos `.part` foram
preservados porque permitem retomar downloads interrompidos futuramente.

Para testar com poucos itens:

```bash
uv run lukasmax download-archive --limit 3
```

## Publicação

A publicação possui duas travas independentes:

- `PUBLISH_ENABLED` precisa ser `true`;
- o item da fila precisa ter `status` igual a `ready`.

Sem ambas, nenhum conteúdo é enviado. O fluxo de publicação é: criar container
de Reel, enviar o MP4 diretamente pelo upload resumível, aguardar o processamento
e publicar o container.

O piloto já foi agendado pelo Meta Business Suite e usa o estado
`scheduled_external`. Esse estado não é elegível para o publicador local e evita
duplicação. O registro confirmado é:

- destino: somente Instagram `_lukasmax`;
- data e hora: 12/08/2026 às 18:00 (`America/Sao_Paulo`);
- identificação do post: `27996092376710770`;
- direitos autorais: verificação do Business Suite aprovada;
- arquivo: `media/ready/7278034913729907974.mp4`.

Para conferir manualmente, abra **Meta Business Suite → Planner**, avance para
a semana de 9 a 15 de agosto de 2026 e localize o item do dia 12 às 18:00.

Para conferir o motor sem publicar:

```bash
uv run lukasmax status
```

Depois de configurar o token, valide conta, username e tipo profissional:

```bash
uv run lukasmax check-instagram
```

## Segredos necessários

- `INSTAGRAM_USER_ID`
- `INSTAGRAM_ACCESS_TOKEN`
- opcionalmente `INSTAGRAM_API_VERSION`

Nunca salvar esses valores no repositório. Localmente use `.env`; no GitHub use
Actions Secrets.
