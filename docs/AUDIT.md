# Auditoria inicial — 7–8 de agosto de 2026

## TikTok `_lukasmax`

- Perfil público confirmado.
- Aproximadamente 72,4 mil seguidores.
- 218 vídeos informados pelo perfil.
- Aproximadamente 2,2 milhões de curtidas acumuladas.
- 199 vídeos públicos retornaram metadados na extração automatizada.
- O restante pode estar indisponível, privado, removido ou não ter sido
  retornado pelo endpoint público do TikTok.
- 129 vídeos chegaram a ser arquivados sem marca-d'água antes de a prioridade
  mudar para o motor de um único piloto; ocupam aproximadamente 1,3 GB.
- Há 33 recusas temporárias registradas e dois arquivos `.part` retomáveis. O
  arquivamento em massa foi interrompido deliberadamente, não por falha do motor.

> **Atualização de 8 de agosto (tarde).** As 33 recusas não eram temporárias nem
> culpa da versão do yt-dlp: faltava *impersonation*. Sem ela o TikTok devolve
> uma página sem os dados de rehydration, e a extração falha sempre com a mesma
> mensagem. O CLI do yt-dlp negocia isso sozinho; a API Python precisa do alvo
> explícito. Com a correção, vídeos que falhavam de forma consistente passaram a
> baixar, e o acervo saiu de 129 para 194 dos 199 do inventário. As poucas
> recusas restantes seguem sendo do mesmo tipo e podem ser vídeos que o endpoint
> público realmente não entrega mais.

## Instagram `_lukasmax`

- Perfil público acessível.
- Nome exibido: Lucas Ferreira.
- 170 seguidores, 2 contas seguidas e 0 publicações no momento da auditoria.
- Em 8 de agosto de 2026, a sessão autenticada confirmou que a conta ainda era
  pessoal. Ela foi convertida para conta profissional do tipo **Criador de
  conteúdo**, categoria **Digital creator**.
- A categoria permaneceu oculta no perfil para preservar a apresentação atual.
- A conta já era pública antes da conversão.

## Primeiro candidato

- TikTok: `7278034913729907974`.
- Cerca de 3,1 milhões de visualizações.
- Cerca de 353,5 mil curtidas.
- 1.541 comentários.
- Cerca de 14,5 mil compartilhamentos.
- Arquivo localizado em 1080×1920, H.265/AAC.
- Quadros do início, meio e final foram inspecionados: não há logo nem username
  do TikTok. A interface de player vista no vídeo faz parte da criação original.
- Áudio identificado: “Toda Toda”, de Pikeno & Menor. Em 8 de agosto, a
  verificação do próprio Meta Business Suite informou que o vídeo estava seguro
  para publicação e que nenhum problema de direitos autorais foi encontrado.
- A cópia pronta para o Instagram foi convertida para H.264/AAC, 1080×1920,
  30 fps e áudio de 48 kHz. Todas as verificações técnicas passaram.
- O Reel foi agendado somente para o Instagram `_lukasmax`, em 12 de agosto de
  2026 às 18:00 (`America/Sao_Paulo`). O Planner confirmou a entrada às 18:00
  na quarta-feira, dia 12. Identificação do post: `27996092376710770`.
