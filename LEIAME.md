# Robô CVM → Telegram — Market Makers FIA

Toda vez que uma empresa da carteira protocolar **qualquer documento na CVM** (fato relevante, comunicado, ITR/DFP, aviso aos acionistas, assembleia, negociação de administradores etc.), chega uma mensagem no Telegram assim:

```
🚨 VLID3 — VALID SOLUÇÕES S.A.
Fato Relevante
🗓️ Entregue: 24/09/2026 18:32 | Ref.: 24/09/2026
🔗 Abrir documento na CVM
```

- Checa a CVM **a cada 10 minutos, das 7h às 23h** (Brasília), todos os dias.
- Roda **de graça** no GitHub, mesmo com seu computador desligado.
- A carteira fica numa **planilha do Google**: entrou ou saiu empresa, é só editar a planilha.

Configurar leva uns 20 minutos, uma vez só. São 5 passos.

---

## Passo 1 — Criar o bot e o canal no Telegram (5 min)

1. No Telegram, procure **@BotFather** → mande `/newbot` → escolha um nome (ex.: *MM FIA CVM*) e um usuário terminado em `bot` (ex.: `mmfia_cvm_bot`).
2. Ele responde com um **token** (algo como `7123456789:AAF...`). **Guarde** — é a senha do bot, não compartilhe.
3. Crie um **canal** (ou grupo) no Telegram, ex.: *MM FIA — Documentos CVM*. Pode ser privado.
4. Nas configurações do canal → **Administradores** → **Adicionar administrador** → procure o seu bot → confirme (só precisa da permissão "Publicar mensagens").
5. Escreva qualquer mensagem no canal (ex.: "oi").

## Passo 2 — Criar a planilha da carteira (3 min)

1. Abra o Google Planilhas e crie uma planilha nova.
2. **Arquivo → Importar → Fazer upload** → envie o arquivo `modelo_planilha.csv` desta pasta (já vem com as 16 posições da lâmina de 04/09). Ou digite à mão, com estas colunas na linha 1:

   | Ticker | Codigo_CVM | Ativo |
   |---|---|---|
   | VLID3 | *(pode deixar vazio)* | SIM |

   - **Ticker**: obrigatório.
   - **Codigo_CVM**: pode ficar vazio — o robô descobre sozinho pela B3. Só preencha se o teste do Passo 5 disser que não encontrou.
   - **Ativo**: coloque `NÃO` para pausar uma empresa sem apagar a linha.
3. Botão **Compartilhar** → em "Acesso geral" escolha **Qualquer pessoa com o link → Leitor** → **Copiar link**. Guarde esse link.

> O link não aparece em lugar público nenhum: ele fica guardado como segredo no GitHub (Passo 4).

## Passo 3 — Colocar o código no GitHub (5 min)

1. Crie uma conta em **github.com** (se ainda não tiver).
2. Clique em **+** (canto superior direito) → **New repository** → nome `robo-cvm` → marque **Public** → **Create repository**.
   - *Por que público?* No público o GitHub não cobra nada, sem limite. No privado há um limite de 2.000 minutos/mês, e a checagem a cada 10 min gastaria ~2.900. O repositório público mostra **só o código** — a carteira, o token e o canal ficam em segredos criptografados, e o robô foi feito para não escrever nomes de empresas nos registros.
3. Na página do repositório, clique em **uploading an existing file** e arraste **todo o conteúdo desta pasta** (inclusive a pasta `.github`). Clique em **Commit changes**.
   - Se a pasta `.github` não aparecer para arrastar (ela é "oculta"): no Windows, no Explorador de Arquivos, marque **Exibir → Itens ocultos**. Ou peça ao Claude Code: *"suba esta pasta para o meu repositório robo-cvm no GitHub"*.

## Passo 4 — Guardar os 3 segredos no GitHub (3 min)

No repositório: **Settings → Secrets and variables → Actions → New repository secret**. Crie os três:

| Nome (exatamente assim) | Valor |
|---|---|
| `TELEGRAM_TOKEN` | o token do Passo 1 |
| `PLANILHA_URL` | o link da planilha do Passo 2 |
| `TELEGRAM_CHAT_ID` | o número do canal (veja abaixo) |

**Como descobrir o `TELEGRAM_CHAT_ID`:** abra no navegador
`https://api.telegram.org/botSEU_TOKEN/getUpdates` (troque `SEU_TOKEN` pelo token, sem espaços).
Procure `"chat":{"id":-100...` — o número que começa com **-100** é o chat_id (copie com o sinal de menos).
Se aparecer vazio (`"result":[]`), mande outra mensagem no canal e recarregue a página.
*(Alternativa: `python ferramentas/descobrir_chat_id.py SEU_TOKEN`.)*

## Passo 5 — Ligar e testar (2 min)

1. No repositório, aba **Actions**. Se aparecer um botão verde pedindo para habilitar workflows, clique nele.
2. Clique em **Robô CVM → Telegram** (à esquerda) → **Run workflow** → em *modo* escolha **teste** → **Run workflow**.
3. Em ~1 minuto chega no canal a lista das empresas monitoradas com o código CVM de cada uma.
   - Se alguma aparecer como "sem código CVM", preencha a coluna `Codigo_CVM` na planilha. (O TXSA34 é BDR da Ternium e normalmente não publica na CVM — pode deixar `Ativo = NÃO`.)
4. Rode de novo com *modo* **normal**. Chega a mensagem **"✅ Robô CVM ligado"**. Pronto — daqui em diante ele roda sozinho a cada 10 minutos.

> Na primeira execução o robô **não** despeja os documentos antigos; ele só memoriza o que já existe e passa a avisar o que for novo.

---

## Dia a dia

- **Mudou a carteira?** Edite a planilha. Na próxima checagem (até 10 min) já vale.
- **Quer checar agora?** Actions → Robô CVM → Run workflow → normal.
- **CVM fora do ar?** Se o site da CVM ficar ~1h sem responder, o robô avisa no canal uma vez e avisa de novo quando normalizar. Enquanto isso ele usa a base de dados abertos da CVM (atualizada 1x por dia) como plano B.
- **Atraso:** o GitHub às vezes atrasa agendamentos em alguns minutos nos horários de pico. Na prática, o aviso chega entre 10 e ~20 minutos depois do protocolo.
- **Adicionar o Thiago ou outras pessoas:** é só convidar para o canal do Telegram.
- **Mudar horário/frequência:** arquivo `.github/workflows/robo-cvm.yml`, linhas `cron` (o horário do GitHub é UTC = Brasília + 3h).

## Se algo der errado

Aba **Actions** → clique na execução com ❌ vermelho → **checar-cvm** → **Rodar o robô**. A última linha diz o erro em português. Os mais comuns:

| Mensagem | O que fazer |
|---|---|
| `O Google devolveu uma página em vez da planilha` | Compartilhamento da planilha precisa ser "Qualquer pessoa com o link: Leitor". |
| `Telegram recusou a mensagem: ... chat not found` | `TELEGRAM_CHAT_ID` errado (lembre do `-100` no início) ou o bot não é administrador do canal. |
| `Telegram recusou ... Unauthorized` | `TELEGRAM_TOKEN` errado. |
| `KeyError: 'PLANILHA_URL'` (ou outro nome) | Faltou criar esse segredo no Passo 4, ou o nome está diferente. |

Se não resolver, cole a mensagem de erro no Claude.

## O que tem nesta pasta

| Arquivo | Para quê |
|---|---|
| `robo.py` | o robô |
| `.github/workflows/robo-cvm.yml` | agenda do GitHub (a cada 10 min, 7h–23h) |
| `.github/workflows/manter-ativo.yml` | reativa o agendamento todo mês (o GitHub desliga robôs parados há 60 dias) |
| `modelo_planilha.csv` | carteira de 04/09 para importar no Google Planilhas |
| `ferramentas/descobrir_chat_id.py` | ajuda a achar o chat_id |
| `testes/` | testes automáticos do código (`python -m pytest testes`) |

### Como funciona por dentro (para quem quiser saber)
Consulta o sistema de documentos da CVM (RAD/ENET — o mesmo do site "Consulta de documentos de companhias abertas") por código CVM, olhando os documentos dos últimos 2 dias; compara com a lista do que já foi enviado (guardada no cache do GitHub por 30 dias) e manda só o que é novo. O código CVM de cada ticker vem da B3. Reapresentações (versão 2, 3…) são enviadas de novo, marcadas como "reapresentação".
