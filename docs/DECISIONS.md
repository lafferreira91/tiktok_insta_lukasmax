# Decisões técnicas

Registro do porquê, não do quê. O que o código faz está no código; aqui ficam as
escolhas que não seriam óbvias para quem chega depois.

## Fundamentais

- **API oficial do Instagram**, para não depender de automação de interface nem
  guardar senha em lugar nenhum.
- **O agendamento é um cron externo** porque a Content Publishing API não tem
  agendamento nativo: não existe `scheduled_publish_time` para Reels, e
  `media_publish` publica sempre na hora. Qualquer solução de agendamento aqui
  seria um cron; a única escolha real é onde ele roda.
- **`graph.instagram.com`, não `graph.facebook.com`** — é o caminho correto para
  conta Criador sem Página do Facebook vinculada.
- **JSON e CSV em vez de SQLite.** O estado fica auditável no diff do Git e
  recuperável à mão. A fila é pequena (centenas de itens), então o custo de não
  ter índices é zero.
- **Arquivos grandes ficam em `media/`, fora do Git.** O padrão no `.gitignore`
  é `/media/` com barra inicial: sem ela, ele também engoliria
  `reports/media/`, que guarda os relatórios de validação.

## Publicação

- **`media_publish` não é idempotente**, e um post duplicado não tem desfazer.
  É a única falha irreversível do sistema, e por isso concentra três camadas de
  proteção: apenas `scheduled` é elegível; o claim é persistido *antes* do
  container existir; e `reconcile` cruza com as mídias recentes da conta em vez
  de retentar às cegas.
- **O push do Git é o lock.** Não há banco nem serviço de coordenação: a
  execução marca o item como `publishing` e dá push imediatamente. Um push
  rejeitado significa que outra execução chegou antes, e o run aborta sem
  publicar.
- **Um post por execução, por padrão.** Limita o raio de dano de qualquer bug a
  um único post.
- **Transições passam por `transition()`.** Atribuir `item["status"]` direto é
  bug: um item reaberto para um estado errado vira post duplicado.

## Mídia

- **`video_url` em vez de upload resumível.** Com o repositório público, os
  assets de Release têm URL pública e a Meta baixa o arquivo sozinha — o runner
  não transfere bytes e o job de publicação não precisa de yt-dlp, ffmpeg nem
  banda. O upload resumível continua implementado como alternativa, e foi
  reescrito de verdade: a versão anterior lia o arquivo inteiro na memória e
  mandava `offset: 0` fixo, ou seja, o `upload_type=resumable` era decorativo.
- **PyAV em vez de parsear o stderr do ffmpeg.** O parser antigo usava regex
  sobre o log e rodava `-f null -`, o que decodificava o vídeo inteiro só para
  ler a resolução — duas vezes por normalização. PyAV lê o header. O
  `imageio-ffmpeg` não fornece `ffprobe`, então essa não era uma opção.
- **A rotação do container é levada em conta.** Vídeo gravado no celular costuma
  vir em paisagem com flag de rotação; ignorar isso reprova um vídeo vertical
  perfeitamente válido no teste de 9:16.
- **Formatos com marca-d'água nunca são escolhidos**, e a cópia normalizada
  perde os metadados de origem (`-map_metadata -1`).

## Coleta

- **O downloader do TikTok é não oficial** porque a API pública não entrega o
  arquivo. O inventário e o log de erros tornam qualquer falha visível.
- **`curl-cffi` via o extra do próprio yt-dlp.** As 33 falhas registradas em
  `reports/download_errors.json` eram todas `Unable to extract universal data
  for rehydration` — o TikTok detectava o cliente HTTP comum e devolvia uma
  página sem os dados. A faixa de versão vem do yt-dlp: instalar `curl-cffi`
  solto acaba numa versão que ele rejeita, e a impersonation volta a ficar
  indisponível silenciosamente.
- **Downloads são espaçados e recuam diante de 429.** O TikTok limita rajadas, e
  insistir só aprofunda o bloqueio e grava falhas que não dizem nada sobre o
  vídeo.

## Legendas

- **Geração roda só no Mac.** O runner publica legendas já congeladas na fila,
  então nunca precisa da chave da Anthropic — ela não é secret do GitHub.
- **A legenda é copiada para a fila, não referenciada.** Editar
  `data/captions/<id>.json` depois não pode alterar em silêncio um post que já
  está agendado.
- **O validador é código, não outra chamada de modelo.** Tamanho, gancho,
  quantidade de hashtags e termos proibidos são regras determinísticas, e
  avisos bloqueiam a aprovação por padrão.
- **Nada de "#viral" nem menção ao TikTok.** Hashtags genéricas não entregam
  alcance, e citar a plataforma de origem num repost é problema de política e de
  distribuição ao mesmo tempo.

## Horários

- **Rotação, jitter e reserva de exploração** existem por motivos distintos: a
  rotação impede que todo dia caia no mesmo horário; o jitter é determinístico
  para que um replanejamento reproduza o mesmo plano; e a reserva de exploração
  garante que a fase 2 tenha dados sobre horários que a heurística nunca
  favoreceu — sem ela o motor só aprenderia sobre os três slots iniciais.
- **Encolhimento bayesiano na fase 2.** Com três amostras por slot, a média crua
  é ruído. Puxar cada slot para a média global até ele ter dados próprios é o
  que impede o motor de se autodestruir cedo.
- **`zoneinfo`, não offset fixo.** O Brasil não tem horário de verão desde 2019,
  mas se voltar, o offset fixo erraria silenciosamente.
- **`scheduled_at` é piso, não horário exato.** O cron do GitHub Actions é
  best-effort e atrasa sob carga.

## Histórico

- A conta `_lukasmax` foi convertida de pessoal para profissional tipo Criador
  em 8 de agosto de 2026, categoria `Digital creator`, sem exibição do rótulo.
- O primeiro teste usou o Meta Business Suite como agendador, o que validou o
  fluxo sem criar token de API. Foi a única vez que um navegador entrou no
  processo, e foi manual.
- O estado `scheduled_external` marca esse item como responsabilidade do
  agendador da Meta e é terminal — o motor local nunca pode publicá-lo de novo.
- O piloto foi só Instagram, sem publicação simultânea no Facebook, para medir
  o canal novo sem misturar audiências.
- **Música licenciada é aprovação separada.** Ausência de marca-d'água não
  significa que o áudio possa ser republicado automaticamente.

## Metricas e crescimento

- **Insight e coletado por idade, nao por data.** Cada Reel e medido as 24h e aos
  7 dias de vida. Comparar um post de dois dias com um de sessenta mede idade,
  nao qualidade -- e o numero continua parecendo razoavel, que e por que o erro
  passaria despercebido.
- **`insights.csv` e append-only.** Sobrescrever destruiria a informacao de
  idade, e a Meta nao devolve isso retroativamente: o que nao foi gravado as 24h
  nao existe mais. De brinde, a chave `(media_id, idade)` ja gravada e o proprio
  registro de "ja coletei" -- idempotencia sem estado extra e, principalmente,
  sem escrever em `queue.json`, que o job de publicacao disputa.
- **`is_trial` entrou no CSV antes de existirem reels de teste.** Uma coluna
  adicionada depois deixaria todo o historico anterior sem ela, e trial e normal
  ficariam inseparaveis -- eles tem distribuicao de alcance estruturalmente
  diferente, entao misturar envenena o ajuste de horarios sem que o numero
  pareca errado.
- **`insights.yml` compartilha o `concurrency: group` do `publish.yml`.** Os dois
  commitam em `data/`; sem isso o loop de rebase daquele job, hoje um caminho
  frio, viraria caminho quente em producao.
- **O coletor nunca chama `transition`.** `published` e estado terminal com
  transicoes vazias: qualquer tentativa levantaria excecao.
- **Reel de teste usa `MANUAL`, nao `SS_PERFORMANCE`.** Nada sobe sozinho para o
  perfil. A graduacao de um vencedor e um toque no app -- a decisao continua
  sendo humana.
- **Um dos dois posts do dia e teste, com videos diferentes.** Publicar o mesmo
  video nas duas versoes seria pior: o reel normal tambem e distribuido para nao
  seguidores, entao as copias disputariam o mesmo publico com o mesmo conteudo.
- **`tune-slots` nao foi escrito.** O motor (`tune_weights`) existe e e testado,
  mas a 2 posts/dia so ha sinal depois de meses, e pesos novos nao mexem em quem
  ja tem horario gravado. Um comando inutilizavel por dois meses e peso morto.
- **Story automatico foi descartado por impossibilidade, nao por custo.** A API
  nao publica figurinhas (link, enquete, localizacao) e nao republica midia ja
  publicada. O "compartilhar nos stories" do app e um SDK de celular que exige
  toque humano. O que a API faria e uma copia sem link de volta -- que nao leva
  ninguem ao Reel, o unico objetivo.
- **Seguir, curtir e comentar em terceiros nao existe na API.** As permissoes do
  Login do Instagram sao `basic`, `content_publish`, `manage_comments` e
  `manage_messages`. Automatizar por fora viola os Termos.
- **Reels de teste nao funcionam nesta conta.** Testado ao vivo em 09/08/2026: o
  mesmo video, na mesma chamada, com `trial_params` devolve `400: Application
  does not have permission for this action`; sem ele o container e criado
  normalmente. Nao e bug do codigo, e a Meta nao documenta como obter a
  permissao. `mark-trials` passou a recusar por padrao -- marcar um item
  significaria um post que falha em producao. O codigo fica: se a permissao
  aparecer, `--force` libera.
- **A liberacao em etapas se pagou na primeira vez.** O plano mandava marcar um
  post, publicar e so entao escalar. O primeiro falhou -- se tivessem sido os 95,
  seriam 95 posts perdidos em vez de um recuperavel.
- **`mark-trials --clear` limpa todo item nao terminal, nao so os marcaveis.** Um
  item que falhou ao publicar sai de `TRIALABLE` mas mantem a marca; devolve-lo
  para a fila reintroduziria a mesma falha. Foi o que quase aconteceu em 09/08.

## Horarios, segunda rodada (13/08/2026, n=7)

- **A faixa do almoco saiu do pool.** Os 7 primeiros posts medidos as 24h dao
  mediana de 1.460 views entre 12h e 14h contra 15.738 a partir das 17h -- ~11x.
  O que torna isso acionavel com n=7 e o controle: os dois grupos tem ranking e
  views de TikTok praticamente identicos (762k contra 722k de mediana), entao a
  diferenca nao e qualidade de video. E a serie alterna alto-baixo em vez de so
  subir, o que descarta crescimento da conta.
- **Isso contradiz o historico do TikTok dele** (onde 15-18h liderava e a noite
  caia) e **confirma a curva de `online_followers`** da API, cujo pico esta entre
  19h e 22h. Plataformas diferentes, publicos diferentes: vale o dado do
  Instagram.
- **O slot da manha ficou, mesmo sem nenhuma amostra.** Corta-lo por suposicao
  seria repetir exatamente o erro que o almoco acabou de expor. Ele fica com ~39%
  dos posts, o que resolve a duvida em ~3 semanas.
- **`wd-afternoon` foi de 17:15 para 16:45.** Nao e ajuste fino de horario: com
  17:15 a distancia ate as 21:15 era de exatamente 240 minutos, o minimo, e o
  jitter derrubava o par noturno -- empurrando o segundo post do dia para a
  manha. 270 minutos deixam o par sempre viavel.
- **O cron ganhou a hora 0 UTC** para cobrir o slot das 21:15. Quem pegou isso
  foi `test_cron_cobre_os_slots.py`, que existe exatamente para amarrar o pool de
  horarios a janela do cron -- sem ele o sintoma seria um post que nao sai, sem
  erro em lugar nenhum.
- **`plan_slots` passou a devolver os horarios em ordem cronologica.** Dentro de
  um dia os slots sao escolhidos por peso, e quem consome a lista casa o video
  melhor ranqueado com o primeiro horario -- sem a ordenacao o melhor video podia
  cair no horario mais tarde por acaso.
