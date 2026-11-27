# `.env` do gerenciador e senhas das contas

## Python

O gerenciador precisa do **Python 3.12 ou mais novo**. `bots/vale_base_metais/` usa uma f-string que não compila em versões
anteriores, e os testes (`python -m pytest -q` ou `python -m unittest discover -p "test_*.py"`) só passam no 3.12+.

## Variáveis obrigatórias no `.env`

| Variável | Para quê | Sem ela |
|---|---|---|
| `ROBO_SECRET` | conexão com o portal (32+ caracteres, igual ao do portal) | o gerenciador não sobe |
| `PORTAL_SENHA` | login dos robôs Coupa e Vale | o login do robô falha com "PORTAL_SENHA não configurada" |

## Variáveis do DAV pelo Cotafrete (`ponte_dav.py`)

| Variável | Para quê | Sem ela |
|---|---|---|
| `COTAFRETE_TOKEN` | token da API `/api/dav` do Cotafrete; igual ao `DAV_API_TOKEN` do `.env` do Cotafrete | o portal mostra "Gerenciador sem COTAFRETE_TOKEN" e não gera o DAV |
| `COTAFRETE_URL` | endereço do Cotafrete na rede local (padrão `http://192.168.1.250:8000`) | usa o padrão |

Se o Cotafrete passar a ouvir só em `127.0.0.1` (`setx /M COTAFRETE_HOST 127.0.0.1`), a rede local fecha e o portal
mostra "Cotafrete fora do alcance".

## Variáveis do Cotabot na VM (`ponte_cotabot.py`)

| Variável | Para quê | Sem ela |
|---|---|---|
| `COTABOT_TOKEN` | token da API do Cotabot (VM); igual ao `COTABOT_TOKEN` do `.env` do Cotabot | a tela do Cotabot mostra "Gerenciador sem COTABOT_TOKEN" e não cota |
| `COTABOT_URL` | endereço do Cotabot na rede local (padrão `http://192.168.1.250:8765`) | usa o padrão |

Senhas e chaves ficam só no `dados\.env` e no `dados\CONFIG.json` do servidor, nunca no código: o repositório é público.
O atualizador automático (`scripts/atualizador.py`) preserva esses arquivos.

## Senhas das contas do portal (`banco_usuarios.json`)

- Cadastro e "esqueci a senha" gravam a senha como hash PBKDF2-SHA256 com sal (`pbkdf2_sha256$...`), em `seguranca.py`.
- Contas antigas, com a senha em texto, continuam entrando. No primeiro login certo a senha é trocada pelo hash.
- O painel `dev.html` saiu (a gestão de contas está no `dashboard.html`): senha e tokens não saem do servidor.
