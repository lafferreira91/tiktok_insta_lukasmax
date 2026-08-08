# Decisões técnicas

- API oficial do Instagram, para não depender de automação de interface ou
  armazenamento de senha.
- Upload resumível direto do arquivo, eliminando hospedagem paga de mídia.
- GitHub Actions como agendador gratuito; execução local continua disponível.
- SQLite não é necessário no piloto: JSON e CSV deixam o estado auditável e
  fácil de recuperar.
- Arquivos grandes permanecem em `media/`, dentro do projeto e fora do Git.
- O downloader do TikTok é não oficial porque a API pública não entrega o
  arquivo do vídeo. O inventário e os registros tornam qualquer falha visível.
- Música licenciada é uma aprovação separada: ausência de marca-d'água não
  significa que o áudio possa ser republicado automaticamente.
- O arquivamento completo foi pausado após 129 vídeos. Até o primeiro envio
  real ser validado, o desenvolvimento e os testes usam somente o candidato
  `7278034913729907974`.
- A conta `_lukasmax` foi convertida de pessoal para profissional do tipo
  Criador em 8 de agosto de 2026. A categoria escolhida foi `Digital creator`,
  sem exibição pública do rótulo.
- Para o primeiro teste, o Meta Business Suite foi usado como agendador oficial
  gratuito. Isso permitiu validar o fluxo completo sem criar token de API.
- O piloto foi destinado somente ao Instagram, sem publicação simultânea no
  Facebook, para medir o desempenho do canal novo sem misturar audiências.
- O estado `scheduled_external` indica que o item já está sob responsabilidade
  do agendador da Meta e impede que o motor local tente publicá-lo novamente.
