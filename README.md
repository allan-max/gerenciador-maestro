# Gerenciador Maestro

Serviço Python que roda no servidor Windows da Ventura e executa o trabalho pesado do Maestro: comanda os robôs dos portais de compras, extrai e responde cotações, mantém as planilhas e bases em JSON, gera DAVs pelo Cotafrete e cuida das contas do portal.

Ele se conecta por Socket.IO ao portal web ([`allan-max/portaismaestro`](https://github.com/allan-max/portaismaestro)), que repassa os comandos dos usuários e mostra o progresso em tempo real.

## Principais recursos

- **Robôs Coupa e Vale (Selenium)**: extração de eventos e itens, verificação diária, respostas de cotação, aceite de termos e aviso de pedidos novos. O OTP da Vale pode ser lido direto do e-mail (IMAP).
- **Piloto automático**: ciclos agendados por portal, com fila de extração e trava para não rodar dois ciclos ao mesmo tempo.
- **Outros portais**: liga, desliga e acompanha os robôs de ME, Ariba, Findes, Compras.gov e ArcelorMittal.
- **Inteligência dos itens**: extração de marca, modelo e part number, classificação de itens de tecnologia, sugestão de fornecedores e de vendedor (Groq, Gemini e Anthropic).
- **Relatórios em Word**: eventos agrupados por tipo (hardware, Dell, eletroportáteis, marcas) com a orientação de onde cotar.
- **Planilhas e base**: `COTAÇÕES.json` e `PEDIDOS.json` com gravação protegida por trava entre processos e máquinas.
- **DAV pelo Cotafrete**: gera o DAV a partir da Planilha Base anexada pelo vendedor (`ponte_dav.py`).
- **Contas do portal**: cadastro, login e recuperação de senha com hash PBKDF2-SHA256 e validação de entradas (`seguranca.py`).
- **Operação**: instância única, reinício suave, eventos para o dashboard de Atividade e atualização automática a partir do GitHub.

## Estrutura

```
gerenciador.py        Processo principal: conexão com o portal, fila e piloto automático
bots/                 Robôs e regras de negócio
  coupa/              Robô do Coupa (principal.py + mixins por etapa)
  vale_bot.py         Robô da Vale Base Metals
ambiente.py           Produção x teste local e onde ficam as bases
planilha_manager.py   Leitura e gravação das planilhas JSON
ponte_dav.py          Ponte com o Cotafrete para gerar o DAV
seguranca.py          Senhas e validação do que chega do navegador
eventos.py            Eventos em logs/eventos.jsonl para o dashboard
instancia_unica.py    Garante um gerenciador por vez
reinicio.py           Reinício suave pedido pelo atualizador
scripts/              Atualizador automático e scripts de manutenção
teste/                Testes automatizados (unittest)
docs/                 Documentação e planos
*.bat                 Iniciar, parar e ver o log no Windows
```

## Requisitos

- Windows (produção em Windows Server)
- Python 3.12 ou mais novo
- Google Chrome
- Acesso à pasta de rede `\\SERVIDOR2\Publico\ALLAN`, onde ficam as bases de produção

Bibliotecas usadas: `python-socketio[client]`, `selenium`, `openpyxl`, `python-docx`, `pandas`, `python-dotenv`, `groq`, `google-genai`, `psutil`, `requests`, `schedule` e `Pillow`.

```bash
pip install "python-socketio[client]" selenium openpyxl python-docx pandas python-dotenv groq google-genai psutil requests schedule Pillow
```

## Como rodar

1. Crie o `.env` na pasta do gerenciador (veja a seção Configuração).
2. Execute `iniciar_gerenciador.bat`. Ele localiza o Python e sobe o gerenciador em segundo plano.
3. Use `ver_log.bat` para acompanhar e `parar_gerenciador.bat` para encerrar.

Logs: `logs\gerenciador.log` (local) e `\\SERVIDOR2\Publico\ALLAN\Logs\gerenciador.txt` (central).

### Teste local

Com `URL_SERVIDOR` apontando para `localhost`/`127.0.0.1` ou com `MAESTRO_TESTE=1`, as bases ficam em `base_teste/`, ao lado do código, e a produção não é tocada. Use `teste\testar_local.bat`.

## Configuração

Senhas e chaves ficam só no `.env` e no `CONFIG.json` do servidor, nunca no código: o repositório é público. Os dois arquivos estão no `.gitignore`.

| Variável | Uso |
|---|---|
| `ROBO_SECRET` | Conexão com o portal (32+ caracteres, igual ao do portal). Obrigatória. |
| `URL_SERVIDOR` | Endereço do portal |
| `PORTAL_SENHA` | Login dos robôs Coupa e Vale. Obrigatória. |
| `COTAFRETE_TOKEN` / `COTAFRETE_URL` | Geração do DAV pelo Cotafrete |
| `GROQ_API_KEY_COUPA` / `GROQ_API_KEY_VALE` | IA dos robôs |
| `GOOGLE_API_KEY` | Gemini |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` | Claude |
| `SMTP_USER` / `SMTP_PASSWORD` / `EMAILS_RELATORIO` | Envio de relatórios e avisos por e-mail |
| `LOCAL_WEB_EMAIL` / `LOCAL_WEB_SENHA` | Leitura do OTP da Vale por IMAP (opcional) |
| `MAESTRO_TESTE` | `1` liga o modo de teste local |

Mais detalhes em [`docs/env-e-contas.md`](docs/env-e-contas.md).

## Testes

```bash
python -m unittest discover -s teste -p "test_*.py"
```

## Atualização automática

O `scripts\atualizador.py` roda a cada 5 minutos pelo Agendador de Tarefas. Quando nenhum robô está trabalhando, ele traz o commit novo do `main`, preserva os arquivos de dados e segredos, roda os testes (volta a versão se falharem) e pede o reinício suave. Ele também atualiza o portal. Commits no `main` chegam à produção em até 5 minutos.

Veja [`docs/melhorias-bots/instalacao.md`](docs/melhorias-bots/instalacao.md).

## Documentação

- [`docs/env-e-contas.md`](docs/env-e-contas.md): `.env` e senhas das contas
- [`docs/eventos.md`](docs/eventos.md): eventos do dashboard de Atividade
- [`docs/protecao-json.md`](docs/protecao-json.md): proteção das bases JSON
- [`docs/melhorias-bots/`](docs/melhorias-bots/): plano, instalação e testes das melhorias dos robôs
