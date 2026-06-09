# 🤖 ThzyxBoTS — Bot de WhatsApp com IA

Bot de WhatsApp em **Python** com inteligência artificial (via **OpenRouter**) e
**47 comandos** divididos em Administração, Utilitários e Jogos.

> Transporte: [`neonize`](https://github.com/krypton-byte/neonize) (binding do
> `whatsmeow`, WhatsApp **multidevice** — sem precisar de navegador aberto).

---

## 📱 Instalação no Termux (Android)

```bash
# dependências do sistema (IMPORTANTE no Termux)
pkg update && pkg upgrade -y
pkg install python git ffmpeg -y
# o neonize precisa do libmagic/file:
pkg install file -y

git clone https://github.com/theosantos1040/Aha.git
cd Aha
git checkout claude/whatsapp-ai-bot-python-hk270m
pip install -r requirements.txt

cp .env.example .env
nano .env          # cole sua OPENROUTER_API_KEY
python run.py      # escaneie o QR code
```

> **`ffmpeg`** é necessário para `/va` (vídeo→áudio) e figurinhas de vídeo.
> **`file`/`libmagic`** é necessário para o neonize iniciar.

---

## ⚡ Instalação rápida (PC/Linux)

```bash
# 1. Instalar dependências
pip install -r requirements.txt

# 2. Configurar
cp .env.example .env
# edite o .env e coloque sua OPENROUTER_API_KEY

# 3. Rodar (vai aparecer um QR code no terminal)
python run.py
```

### 🔑 Login por código (recomendado) ou QR

Ao rodar `python run.py`, o bot pergunta como conectar:

```
🚀 ThzyxBoTS — como deseja conectar?
  [1] Código de pareamento (digitar o número)  ← recomendado
  [2] QR Code
```

- **Opção 1 (código):** digite seu número com DDI+DDD (ex: `5511999999999`).
  O bot mostra um código tipo `ABCD-1234`. No celular:
  **WhatsApp > Aparelhos conectados > Conectar um aparelho >
  Conectar com número de telefone** e digite o código.
- **Opção 2 (QR):** escaneie o QR code que aparece no terminal.

Para pular a pergunta, defina no `.env`:
```
LOGIN_METHOD=code
PHONE_NUMBER=5511999999999
```

A sessão fica salva — nas próximas vezes ele conecta sozinho.

---

## 🔑 Configuração (`.env`)

| Variável | Descrição |
|---|---|
| `OPENROUTER_API_KEY` | Sua chave do [OpenRouter](https://openrouter.ai/keys) |
| `BOT_NAME` | Nome da IA (padrão: `ThzyxBoTS`) |
| `DEFAULT_PREFIX` | Prefixo dos comandos (padrão: `/`) |
| `SESSION_DB` | Arquivo da sessão do WhatsApp |
| `DATA_DB` | Banco de dados do bot (economia, XP, etc.) |
| `OWNERS` | (opcional) números de donos globais, separados por vírgula |

> ⚠️ **Segurança:** sua chave foi compartilhada em texto puro. **Gere uma nova**
> em https://openrouter.ai/keys e nunca a coloque dentro do código ou em
> arquivos versionados. O `.env` já está no `.gitignore`.

---

## 🧠 IA — comando `/IA`

Três modelos gratuitos, **testados de verdade (HTTP 200)**:

| Apelido | Modelo OpenRouter |
|---|---|
| `chatgpt` | `openai/gpt-oss-120b:free` (padrão, rápido) |
| `nex` | `nex-agi/nex-n2-pro:free` |
| `glm` | `z-ai/glm-4.5-air:free` |

```
/IA Quem descobriu o Brasil?
/IA chatgpt Explique buracos negros
/IA nex Escreva um poema
```

> Observação: o modelo `sourceful/riverflow-v2.5-pro:free` que você citou **não
> existe** no OpenRouter, por isso foi substituído pelo `z-ai/glm-4.5-air:free`.
> O cliente de IA faz **retry automático** (429/5xx/timeout) e troca de modelo
> caso um falhe.

---

## 📜 Comandos

### 👮 Administração (19)
`/ttkvd` (baixa vídeo do TikTok) · `/ban` · `/unban` · `/kick` · `/mute` ·
`/unmute` · `/clear` · `/lock` · `/unlock` · `/warn` · `/checkwarns` ·
`/welcome` (boas-vindas com foto) · `/setprefix` · `/addrole` · `/removerole` ·
`/slowmode` · `/announce` · `/nuke`

### 🛠️ Gerais & Utilitários (23)
`/IA` · `/ping` · `/help` · `/userinfo` · `/serverinfo` · `/avatar` ·
`/fg` (vira figurinha) · `/va` (vídeo→áudio) · `/calc` · `/weather` ·
`/translate` · `/remind` · `/poll` · `/afk` · `/invite` · `/uptime` ·
`/report` · `/suggest` · `/level` · `/leaderboard` · `/daily` · `/balance` · `/pay`

### 🛡️ Segurança & Moderação
`/antibot` · `/antilink` · `/antispam` · `/setlogs` · `/whitelist-add` ·
`/whitelist-remove` · `/auditlog`

### ⚙️ Configurações Globais
`/setprefix` · `/maintenance` · `/backup-create` · `/backup-load`

### ✨ Recursos especiais
- **`/fg`** — envie (ou responda) uma imagem/vídeo com `/fg` e o bot devolve uma **figurinha**.
- **`/va`** — envie (ou responda) um vídeo com `/va` e o bot devolve o **áudio (mp3)**.
- **`/welcome on`** — boas-vindas com **foto de perfil** do novo membro.
- **`/clear N`** — apaga as últimas N mensagens (admins e usuários) via *revoke*.
- **`/mute`** — silencia: apaga as mensagens do usuário e avisa *"Você foi silenciado ‼️⚠️"* (até 3x).
- **`/antilink` / `/antispam` / `/antibot`** — moderação automática (apaga links, pune flood, bloqueia entradas não autorizadas).
- **`/setlogs`** — canal de auditoria em tempo real; **`/auditlog`** lista as últimas ações.

> ⚠️ **Para apagar mensagens de OUTROS** (`/clear`, `/mute`, `/antilink`) o bot
> precisa ser **administrador** do grupo. **`/ttkvd`, `/fg` (vídeo) e `/va`**
> exigem **ffmpeg** (`pkg install ffmpeg -y`).

### 🎮 Jogos & Brincadeiras (10)
`/coinflip` · `/jokenpo` · `/8ball` · `/roll` · `/tictactoe` · `/trivia` ·
`/hangman` · `/akinator` · `/russianroulette` · `/ship`

---

## ⚠️ WhatsApp ≠ Discord (limitações reais)

A lista original tinha conceitos de **Discord** que o WhatsApp não suporta da
mesma forma. O que foi adaptado:

| Comando | Comportamento no WhatsApp |
|---|---|
| `/ban` | Remove do grupo **+ banlist**: se voltar, é removido automaticamente |
| `/kick` | Remove o participante (precisa o bot ser admin) |
| `/lock` `/unlock` | Usa o modo "só admins enviam" (nativo do WhatsApp) |
| `/mute` `/slowmode` | Registrados pelo bot — o WhatsApp **não** permite impedir o envio de terceiros |
| `/clear` `/nuke` | O WhatsApp **não** permite apagar mensagens de outros nem clonar chats via API |
| `/addrole` `/removerole` | Cargos a nível de bot; `role=admin` promove/rebaixa de verdade |

Tudo o que é possível na API foi implementado; o resto é registrado pelo bot e
documentado de forma honesta nas respostas.

---

## ✅ Testes

```bash
python tests/test_logic.py     # calc, jogos, economia, DB (offline)
python tests/test_commands.py  # todos os comandos com cliente WhatsApp mock
python tests/test_ai.py        # IA AO VIVO — confirma HTTP 200 nos 3 modelos
```

Todos passam. Os testes de comando usam um cliente WhatsApp **falso**, então
rodam sem precisar de conta/QR.

---

## 📁 Estrutura

```
run.py          # ponto de entrada
bot.py          # eventos + handlers de todos os comandos (neonize)
ai.py           # cliente OpenRouter (retry + fallback de modelo/reasoning)
database.py     # SQLite: economia, XP, warns, AFK, prefixo, banlist...
games.py        # lógica pura dos jogos
utils.py        # calc seguro, parsing de duração, formatação
services.py     # clima (wttr.in) e tradução (Google + fallback IA)
tiktok.py       # download de vídeos (tikwm.com)
config.py       # configuração via .env
tests/          # suíte de testes
```
