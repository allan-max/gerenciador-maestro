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
gerenciador.py            Processo principal: conexão com o portal, fila e piloto automático
iniciar_gerenciador.bat   Sobe o gerenciador em segundo plano
parar_gerenciador.bat     Encerra o gerenciador
ver_log.bat               Acompanha o log ao vivo

nucleo/                   Módulos de apoio (importar como `from nucleo import x`)
  ambiente.py             Produção x teste local e onde ficam as bases
  planilha_manager.py     Leitura e gravação das planilhas JSON
  resposta_cotacao.py     Marca cotação respondida
  trava_arquivo.py        Trava entre processos e máquinas para gravar as bases
  eventos.py              Eventos em logs/eventos.jsonl para o dashboard
  log_robos.py            Logs dos robôs Coupa e Vale
  instancia_unica.py      Garante um gerenciador por vez
  reinicio.py             Reinício suave pedido pelo atualizador
  processos_robos.py      Detecta robô externo já rodando
  navegador.py            Chrome de cada robô com perfil próprio
  otp_email.py            Lê o código da Vale no e-mail
  seguranca.py            Senhas e validação do que chega do navegador
  donos_vendedor.py       Dono da cotação pelo nome do vendedor
  ponte_dav.py            Ponte com o Cotafrete para gerar o DAV
  ponte_cotabot.py        Ponte com o Cotabot na VM
  ariba_api.py            Comandos para o robô do Ariba
  winserver.py            Métricas do servidor (CPU/RAM/GPU)
dados/                    .env e JSON do servidor (CONFIG.json, banco_usuarios.json...); fora do git
bots/                     Robôs e regras de negócio
  vale/                   Robô do Coupa da Vale (CoupaScraper: principal.py + mixins por etapa)
  vale_base_metais/       Robô da Vale Base Metals (ValeScraper: principal.py + mixins por etapa, como o Coupa;
                          a resposta herda a do Coupa em mixins/resposta.py)
  itens/                  Entender o item: marca/modelo, tecnologia, onde cotar (fornecedores_ti.json), vendedor, validação
  word/                   Seções dos Words por tipo: Dell, hardware, eletroportáteis, ar-condicionado, marcas
  avisos/                 E-mails e avisos: relatório, erros, termos, vencimento alterado
  portal/                 Telas comuns do portal: termos de participação, transmissão do captcha
scripts/                  Atualizador automático e scripts de manutenção
teste/                    Testes automatizados (unittest)
docs/                     Documentação e planos
```

Segredos e JSON do servidor ficam em `dados/`: `.env`, `CONFIG.json`, `banco_usuarios.json`, `banco_ips_bloqueados.json`
e `fila_extracao.json`. Instalação antiga com esses arquivos na raiz: o gerenciador os move para `dados/` ao iniciar
(`nucleo/ambiente.py`, `migrar_dados`). Se a raiz e `dados/` tiverem versões diferentes, vale a de `dados/` e o log avisa.

Na raiz, fora do git, ficam os arquivos de controle do gerenciador e do atualizador (`gerenciador.estado`,
`gerenciador.instancia`) e as pastas `logs/`, `base_teste/` e `perfis_chrome/`. Não mova esses.

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

1. Crie o `.env` em `dados\.env` (veja a seção Configuração).
2. Execute `iniciar_gerenciador.bat`. Ele localiza o Python e sobe o gerenciador em segundo plano.
3. Use `ver_log.bat` para acompanhar e `parar_gerenciador.bat` para encerrar.

Logs: `logs\gerenciador.log` (local) e `\\SERVIDOR2\Publico\ALLAN\Logs\gerenciador.txt` (central).

### Teste local

Com `URL_SERVIDOR` apontando para `localhost`/`127.0.0.1` ou com `MAESTRO_TESTE=1`, as bases ficam em `base_teste/`, ao lado do código, e a produção não é tocada. Use `teste\testar_local.bat`.

## Configuração

Senhas e chaves ficam só no `dados\.env` e no `dados\CONFIG.json` do servidor, nunca no código: o repositório é público. Os dois arquivos estão no `.gitignore`.

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
