# O que a automação não faz — e você faz

A automação publica. Ela **não interage**, e isso não é uma lacuna do projeto: a
API oficial do Instagram simplesmente não tem como seguir contas, curtir posts
ou comentar em publicações de terceiros.

As permissões que existem para o nosso tipo de app são exatamente estas quatro:
`instagram_business_basic`, `instagram_business_content_publish`,
`instagram_business_manage_comments` e `instagram_business_manage_messages`.
Não há nada de "seguir" nem de "curtir" em lugar nenhum.

Você vai achar blogs afirmando o contrário. Eles copiam a documentação da **API
antiga do Instagram, desligada em 2020**. Ferramentas que prometem isso hoje
automatizam o aplicativo por fora, o que **viola os Termos** e é a forma mais
comum de perder uma conta que já tem tração.

Então esta parte é manual. A boa notícia é que é pouca coisa e cabe em minutos
por dia.

---

## 1. Compartilhar o Reel no story — 5 segundos, o maior retorno

**Faça isso todo dia, nos dois posts.**

Abra o Reel que acabou de sair, toque em compartilhar e mande para o seu story
com a **figurinha de link**. É isso que leva quem viu o story a assistir o vídeo
completo.

Por que não automatizamos: a API **não publica figurinhas** — link, enquete e
localização estão explicitamente fora. Ela consegue publicar um story novo a
partir de um arquivo de vídeo, mas seria uma cópia separada, **sem nenhum link
de volta para o Reel**. Não levaria ninguém a lugar nenhum, que é justamente o
objetivo.

O caminho nativo do app faz o que a API não faz. São cinco segundos.

## 2. A janela dos 30 minutos

O que acontece logo depois de um post pesa mais que o que acontece depois.

Nos 30 minutos seguintes a cada publicação:

- **Responda todo comentário.** Não em bloco no fim do dia — na hora.
- **Fique no app.** Curta e comente em outros perfis do nicho nesse intervalo.
  Sua atividade nesse momento é o sinal mais barato que você tem.

Você não precisa fazer isso nos dois posts. Escolha **um por dia** e faça bem.
Constância vale mais que intensidade.

## 3. Como achar as contas do nicho

A busca por hashtag via API não está disponível para o nosso tipo de app (exige
um recurso que só apps com Login do Facebook recebem). Então a busca é no app
mesmo, e o método que funciona é este:

1. Procure as hashtags que **você já usa**: `#carjams`, `#dublagemnocarro`,
   `#dubsmash`. São as suas do TikTok, e é onde está gente do mesmo mundo.
2. Na aba **Reels**, quando aparecer um vídeo parecido com o seu, entre no
   perfil. Se for do nicho, siga e comente em algo recente.
3. Use os **áudios**. Toque no som de um Reel de música e você vê todo mundo que
   usou aquele áudio — é a lista mais bem segmentada que existe, e ninguém
   precisa de API para ela.
4. **Contas do seu tamanho**, não as gigantes. Um perfil de 500 a 20 mil
   seguidores responde, devolve visita e vira colaboração. Um de 2 milhões não
   te vê.

Meta razoável: **10 a 15 interações por dia**, feitas de verdade. Comentário que
diz algo sobre o vídeo, não "top demais 🔥".

## 4. Comentário é conteúdo, não cortesia

Um comentário seu num perfil grande é visto por muito mais gente que um post seu
hoje. Trate como microconteúdo: se for engraçado ou observador, gera perfil
visitado. "Arrasou" não gera nada.

O mesmo vale para responder os comentários no **seu** post: a resposta aparece
para quem comentou e para quem está lendo. É palco, não protocolo.

## 5. Quem está do outro lado

Vale escrever pensando em quem realmente segue você hoje — dado da API, não
palpite:

- **25 a 34 anos** é a maior faixa, com 40% do total
- **Praticamente meio a meio**: 49% mulheres, 43% homens
- **Campinas e São Paulo** concentram a base; 95% no Brasil

Não é um público adolescente. Referência de 2010 funciona melhor que gíria de
2026, e o sarcasmo pega — o que combina com a voz que já está nas legendas.

Para conferir isso de novo daqui a alguns meses, conforme a conta cresce:

```bash
uv run lukasmax audience
```

## 6. O que não fazer

- **Não automatize follows, curtidas ou comentários.** Viola os Termos e derruba
  conta. Nenhum ganho de alcance compensa perder o perfil.
- **Não siga em massa para depois deixar de seguir.** É o padrão mais fácil de
  detectar que existe.
- **Não compre seguidor.** Além de óbvio, estraga a única coisa que estamos
  construindo aqui: métrica confiável para decidir horário e conteúdo.
- **Não responda comentário com texto pronto.** O robô aparece.

---

## Uma observação sobre as hashtags

Hoje `#carjams` está em **193 de 193 legendas** e `#dublagemnocarro` em 187.

Isso não é erro — é identidade de nicho, e funciona. Mas significa que
**hashtag não explica variação de desempenho** no seu perfil: como não varia,
não dá para aprender nada com ela. Se um dia quisermos descobrir se hashtag
importa, elas precisam variar primeiro.

Não é trabalho para agora. Fica registrado.
