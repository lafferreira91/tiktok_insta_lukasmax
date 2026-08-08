# Criar o app na Meta e obter o token

Guia conferido contra a documentação oficial em **08/08/2026**. As páginas de
origem foram atualizadas pela Meta em 30/jun/2026 (Publicação de conteúdo e
Visão geral) e 13/mar/2026 (Login de Empresa), então os nomes de botão abaixo
são os atuais do Painel de Apps.

O que você vai conseguir no final: **`INSTAGRAM_USER_ID`** e
**`INSTAGRAM_ACCESS_TOKEN`**. Nada mais.

---

## Antes de começar — confira estes 3 pré-requisitos

Se qualquer um falhar, o botão de gerar token não aparece, e é aí que a maioria
das tentativas trava.

1. **A conta `_lukasmax` precisa ser profissional** (Empresa ou Criador de
   conteúdo). Conta pessoal não é aceita por nenhuma API oficial.
   No app do Instagram: *Configurações → Tipo de conta e ferramentas → Mudar
   para conta profissional*.
2. **A conta precisa estar pública.** A documentação exige isso explicitamente
   na hora de atribuir a conta ao app ("A conta deve ser pública").
3. **Você precisa estar registrado como desenvolvedor da Meta.** Se nunca fez:
   <https://developers.facebook.com> → *Começar*. É um formulário curto com
   confirmação de e-mail/telefone.

Você **não** precisa de Página do Facebook, nem de portfólio empresarial
verificado, nem de Análise do App. Explico o porquê no final.

---

## Passo 1 — Criar o app

Abra <https://developers.facebook.com/apps> e clique em **Criar app** (canto
superior direito).

> Se o botão recusar: existe um limite de **15 apps** em que você pode ser
> administrador ou desenvolvedor, e apps arquivados contam. Remova algum antigo.

O assistente tem quatro telas. O que escolher em cada uma:

| Tela | O que fazer |
|---|---|
| **Detalhes do app** | Nome (ex.: `lukasmax-publisher`) e e-mail de contato. O nome é interno, não aparece para ninguém. |
| **Caso de uso** | Escolha **Outro**. As opções prontas ("Gerenciar mensagens…") existem, mas levam a fluxos com webhooks que você não vai usar. |
| **Tipo de app** | **Empresa**. Obrigatório — o produto Instagram só pode ser adicionado a apps do tipo Empresa. |
| **Portfólio empresarial** | Pode deixar **em branco / "Não conectar agora"**. Só é exigido para publicar o app ao público. |

Clique em **Criar app** e confirme sua senha.

---

## Passo 2 — Adicionar o produto Instagram

Você cai no painel do app, numa lista de produtos disponíveis.

1. Role até o card **Instagram** ("permite que criadores de conteúdo e empresas
   gerenciem mensagens e comentários, publiquem conteúdo…").
2. Clique em **Configurar**.

A Meta adiciona automaticamente a **Configuração da API com o Login do
Instagram**. É essa que você quer.

> ⚠️ Se a tela oferecer escolher entre **"API com login do Instagram"** e
> **"API com login do Facebook"**, escolha a **primeira**. A segunda exige uma
> Página do Facebook vinculada e muda o host da API de `graph.instagram.com`
> para `graph.facebook.com` — o código deste projeto usa o primeiro.

---

## Passo 3 — Conectar a conta e gerar o token

No menu esquerdo: **Instagram → Configuração da API com login do Instagram**.

Procure a seção **"2. Gerar tokens de acesso"** (a numeração das seções é da
própria página).

1. Clique em **Adicionar conta** / **Adicionar uma conta do Instagram**.
2. Abre um pop-up do Instagram. Faça login como **`_lukasmax`**.
3. A tela de permissões aparece. **Clique em Permitir.**
4. De volta ao painel, a conta aparece numa lista. Clique em **Gerar token** ao
   lado dela.
5. Confirme e **copie o token**. Ele só é mostrado uma vez — se perder, gere
   outro (é só clicar em Gerar token de novo, sem quebrar nada).

**Esse token já é de longa duração: vale 60 dias.** É a diferença entre o token
do Painel de Apps e o do fluxo de OAuth — o do OAuth expira em 1 hora, o do
painel não. Por isso o projeto não implementa OAuth: para publicar na sua
própria conta, ele é desnecessário.

### As permissões

Na tela de consentimento você vai ver algo próximo de:

- `instagram_business_basic` — ler perfil e mídia. **Obrigatória.**
- `instagram_business_content_publish` — publicar. **Obrigatória.**
- `instagram_business_manage_comments` — comentários. Opcional, pode deixar.
- `instagram_business_manage_messages` — direct. Opcional, pode deixar.
- `instagram_business_manage_insights` — métricas.

Não se preocupe em desmarcar as opcionais: o token do painel vem com o conjunto
padrão do produto, e permissão sobrando não atrapalha. As duas obrigatórias são
o que importa.

> Sobre `instagram_business_manage_insights`: ela existe (aparece na lista da
> Análise do App), mas as métricas por post que a Fase 5 vai usar já vêm com a
> `basic` no fluxo de Login do Instagram. Se ela não aparecer na sua tela, siga
> em frente — não é bloqueio.

---

### 🛑 Se aparecer "Função de desenvolvedor é insuficiente"

É o erro mais comum deste passo. A mensagem é enganosa: ela não fala do seu
papel no app, fala do papel da **conta do Instagram** que o navegador mandou.

Ser dono da `_lukasmax` no Instagram e ser administrador do app na Meta são
duas identidades **separadas**. Enquanto o app está em desenvolvimento, só quem
tem papel nele pode autorizar. Se a Meta não consegue ligar a conta do
Instagram que apareceu no pop-up a um papel no app, devolve esse texto.

A Meta não documenta essa mensagem. As três causas abaixo estão em ordem de
frequência — teste nesta ordem, a primeira resolve a maioria dos casos.

**1. Sessão errada no navegador (mais provável)**

O pop-up não pede login se já existe sessão do Instagram aberta: ele reusa a
que estiver lá, sem avisar. Se você tem outra conta logada — pessoal, antiga,
de outro projeto — é ela que vai para a Meta.

- Abra uma **janela anônima**.
- Entre em <https://www.instagram.com> **só** com a `_lukasmax`.
- Na mesma janela anônima, abra <https://developers.facebook.com/apps>, entre
  na sua conta Meta e refaça o Passo 3.

**2. A conta não foi convidada como testadora**

Se o fluxo automático não atribuiu o papel:

- Menu esquerdo → **Funções do app → Funções** (*App roles → Roles*).
- Procure a seção de **testadores do Instagram** e adicione `_lukasmax`.
- **O convite fica pendente até ser aceito, e isso é feito do lado do
  Instagram, não do painel** — é aqui que quase todo mundo empaca:
  <https://www.instagram.com/accounts/manage_access/> → aba
  **Convites de testador** (*Tester Invites*) → **Aceitar**.
- Volte ao painel e gere o token.

**3. A conta do Instagram não está ligada à sua conta Meta**

Se as duas ainda não se conhecem, não há como a Meta mapear o papel. Vincule as
duas na **Central de Contas** (*Accounts Center*), pelo app do Instagram:
*Configurações → Central de Contas → Contas → Adicionar contas*, entrando com a
mesma conta Meta/Facebook que criou o app.

**Como saber qual das três é a sua:** na tela de consentimento do pop-up,
confira o `@` que aparece. Se não for `_lukasmax`, é a causa 1. Se for a conta
certa e ainda assim falhar, é a 2 ou a 3.

---

## Passo 4 — Pegar o `INSTAGRAM_USER_ID`

Com o token na mão, rode no terminal (troque `SEU_TOKEN`):

```bash
curl -s "https://graph.instagram.com/v26.0/me?fields=user_id,username&access_token=SEU_TOKEN"
```

Resposta esperada:

```json
{"user_id": "178414...", "username": "_lukasmax"}
```

O `user_id` é o `INSTAGRAM_USER_ID`. Confirme que o `username` é mesmo
`_lukasmax` — se vier outro, você logou na conta errada no Passo 3.

> Atenção: esse `user_id` **não** é o mesmo número que o campo `id` devolve. Use
> o `user_id`.

---

## Passo 5 — Guardar as credenciais

**Local (para rodar `doctor` e testar):** copie `.env.example` para `.env` e
preencha. O `.env` está no `.gitignore`.

```
INSTAGRAM_USER_ID=178414...
INSTAGRAM_ACCESS_TOKEN=IGAA...
```

**No GitHub (para o cron publicar):**
*Settings → Secrets and variables → Actions*

- Aba **Secrets** → `INSTAGRAM_USER_ID` e `INSTAGRAM_ACCESS_TOKEN`
- Aba **Variables** → `PUBLISH_ENABLED` = `false` **(por enquanto)**

Nunca coloque a chave da Anthropic aqui. O runner publica legendas já
congeladas na fila e nunca chama IA.

---

## Passo 6 — Validar

```bash
uv run lukasmax doctor --check-assets
```

Ele confere token válido, dias até expirar, quota de publicação e se todo item
agendado tem asset acessível.

Depois: um post real de teste com `workflow_dispatch` e `max_per_run=1`,
conferir o permalink no perfil, e só então virar `PUBLISH_ENABLED` para `true`.

---

## Renovação do token (a cada 60 dias)

Duas formas, ambas válidas:

- **Painel:** *Instagram → Configuração da API com login do Instagram →
  Gerar token*. O novo vale imediatamente; atualize o secret no GitHub.
- **API:** com um token que tenha **pelo menos 24h de vida** e ainda **não
  expirado**:

  ```bash
  curl -s "https://graph.instagram.com/refresh_access_token?grant_type=ig_refresh_token&access_token=SEU_TOKEN"
  ```

  Já implementado em `InstagramPublisher.refresh_long_lived_token()`.

**Se passar de 60 dias sem renovar, o token morre e não há como recuperá-lo** —
só gerar outro pelo painel. O `doctor` avisa quando faltam menos de 14 dias.

---

## Por que você NÃO precisa de Análise do App

A Meta tem dois níveis de acesso:

- **Standard Access** — o padrão de todo app novo. A documentação diz
  textualmente: *"Caso o app seja usado apenas na sua conta profissional do
  Instagram ou em uma conta que você gerencia, o acesso padrão será
  suficiente."* É exatamente o nosso caso.
- **Advanced Access** — necessário só para atender contas de terceiros. Exige
  Análise do App, verificação de empresa e gravações de tela.

Ou seja: pode ignorar a seção "Concluir a análise do app" do painel, e também
os passos de **webhooks** e de **Configurar login da empresa** (URL de
redirecionamento, URL de exclusão de dados). Esses só existem para apps com
usuários externos. Se o painel mostrar esses cards como pendentes, tudo bem —
publicar na sua própria conta funciona assim mesmo.

---

## Se travar

| Sintoma | Causa quase certa |
|---|---|
| Não aparece o card **Instagram** nos produtos | O app não é do tipo **Empresa**. Crie outro — o tipo não muda depois. |
| "Adicionar conta" não aceita a `_lukasmax` | A conta ainda é pessoal, ou está privada. |
| **"Função de desenvolvedor é insuficiente"** | Sessão de outra conta no navegador, ou convite de testador não aceito. Ver a seção no Passo 3. |
| Token gerado mas `/me` devolve erro 190 | Token copiado com espaço/quebra de linha, ou já revogado por uma nova geração. |
| `/me` devolve outro `username` | Sessão do Instagram no navegador estava em outra conta. Saia e refaça o Passo 3 numa janela anônima. |
| Publicar devolve erro sobre PPA | A conta está vinculada a uma Página que exige Autorização de Publicação. Só ocorre com Página vinculada. |

---

## Fontes

- [Publicação de conteúdo](https://developers.facebook.com/docs/instagram-platform/content-publishing) — atualizada 30/jun/2026
- [Visão geral da Plataforma do Instagram](https://developers.facebook.com/docs/instagram-platform/overview) — atualizada 30/jun/2026
- [Primeiros passos — API com Login do Instagram](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/get-started)
- [Login de Empresa no Instagram](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/business-login) — atualizada 13/mar/2026
- [Personalizar o caso de uso do Instagram](https://developers.facebook.com/docs/instagram-platform/create-an-instagram-app)
