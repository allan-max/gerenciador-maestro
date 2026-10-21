# `.env` do gerenciador e senhas das contas

## Python

O gerenciador precisa do **Python 3.12 ou mais novo**. `bots/vale_bot.py` usa uma f-string que não compila em versões
anteriores, e os testes (`python -m pytest -q` ou `python -m unittest discover -p "test_*.py"`) só passam no 3.12+.

## Variáveis obrigatórias no `.env`

| Variável | Para quê | Sem ela |
|---|---|---|
| `ROBO_SECRET` | conexão com o portal (32+ caracteres, igual ao do portal) | o gerenciador não sobe |
| `PORTAL_SENHA` | login dos robôs Coupa e Vale | o login do robô falha com "PORTAL_SENHA não configurada" |
| `DEV_MASTER_TOKEN` | chave do painel `dev.html` | o painel dev fica fechado |

Senhas e chaves ficam só no `.env` e no `CONFIG.json` do servidor, nunca no código: o repositório é público.
O atualizador automático (`scripts/atualizador.py`) preserva esses arquivos.

## Senhas das contas do portal (`banco_usuarios.json`)

- Cadastro e "esqueci a senha" gravam a senha como hash PBKDF2-SHA256 com sal (`pbkdf2_sha256$...`), em `seguranca.py`.
- Contas antigas, com a senha em texto, continuam entrando. No primeiro login certo a senha é trocada pelo hash.
- O painel dev recebe perfil, acessos e último login. Senha e tokens não saem do servidor.
