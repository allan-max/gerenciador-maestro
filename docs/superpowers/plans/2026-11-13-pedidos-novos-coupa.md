# Pedidos Novos do Coupa — Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Botão "Pedidos Novos" na tela do Coupa aciona o robô, que varre os pedidos da Vale no portal, guarda os que leu num JSON próprio e manda e-mail só com os pedidos novos que não estão na planilha.

**Architecture:** A lógica de arquivos e e-mail fica num módulo puro novo (`bots/coupa/pedidos_novos.py`), testado sem navegador. O `extrair_pedidos` (Selenium) passa a usar esse módulo: não baixa mais PDF e não grava no `PEDIDOS.json` nem no `pedidos.json`. O comando chega por `comando_direto` (coupa.html → server.js → gerenciador.py → `CoupaScraper.iniciar_tarefa`).

**Tech Stack:** Python 3.14 + unittest + Selenium (gerenciador); Node + socket.io + `node --test` (portaismaestro).

**Spec:** `gerenciador/docs/superpowers/specs/2026-11-13-pedidos-novos-coupa-design.md`

## Global Constraints

- Branch `feat/pedidos-novos-coupa` em cada repositório (`gerenciador`, `portaismaestro`), criado a partir do branch atual. Não mexer em `portaismaestro/dados/prints_respostas.json` (alteração do usuário já pendente).
- Commits sem `Co-Authored-By` e sem "Generated with Claude Code" (preferência do usuário).
- Arquivos em `ambiente.pasta_banco()` (produção: `\\SERVIDOR2\Publico\ALLAN\database\Banco-de-dados`):
  - `PEDIDOS.json` — planilha, formato `{"PEDIDOS": [{"PEDIDO": "4513771955", ...}]}`. **Só leitura.**
  - `PEDIDOS-LIDOS-COUPA.json` — lista de dicts de pedido lidos. Só cresce.
  - `PEDIDOS-FORA-PLANILHA.json` — lista de dicts lidos cujo `pedido` não está na planilha. Recalculado.
- Destinatário: `config.get("emails_pedidos_novos")` ou, sem valor, `vendas7@venturainformatica.com.br`.
- Sem pedido novo: não envia e-mail.
- Nunca baixar PDF; nunca chamar `atualizar_pedidos_excel` nem `salvar_pedido_database` no fluxo de pedidos.
- Testes Python: `cd gerenciador && python -m unittest teste.<modulo>`. Testes Node: `cd portaismaestro && node --test tests/<arquivo>`.

## Review Focus

- `PEDIDOS.json` sendo trocado por outro processo no meio da leitura (JSON truncado): tenta de novo até 5 vezes; se falhar, a extração aborta **sem** enviar e-mail (senão todos os pedidos viram "novos"). Teste em Task 1.
- Nº do pedido na planilha com espaço ou como número (`4513771955` int): compara como `str(...).strip()`. Teste em Task 1.
- `PEDIDOS-LIDOS-COUPA.json` inexistente na primeira execução: começa vazio, sem erro. Teste em Task 1.
- Pedido lido antes que depois entra na planilha: sai do `PEDIDOS-FORA-PLANILHA.json` na próxima execução. Teste em Task 1.
- Parada no meio (`solicitar_parada`): pedidos já lidos ficam gravados e o e-mail sai com o que foi lido até ali. Coberto por Task 2 (o fim da extração roda depois do laço, também quando para).

---

### Task 1: Módulo `pedidos_novos` (arquivos + e-mail)

**Files:**
- Create: `gerenciador/bots/coupa/pedidos_novos.py`
- Test: `gerenciador/teste/test_pedidos_novos.py`

**Interfaces:**
- Consumes: `bots.relatorio_email._conectar_e_enviar_smtp(remetente, senha, destinatarios, msg, nome_bot, log) -> bool`
- Produces:
  - `DESTINO_PADRAO = "vendas7@venturainformatica.com.br"`
  - `numeros_planilha(caminho: str) -> set[str]` — lança `ErroLeituraPlanilha` se não conseguir ler após 5 tentativas (0,5 s entre elas). Arquivo inexistente também lança.
  - `carregar_lidos(caminho: str) -> list[dict]` — `[]` se não existe.
  - `registrar_lido(caminho: str, pedido: dict) -> None` — acrescenta; se o `pedido` já existe, substitui. Grava em arquivo temporário + `os.replace`.
  - `gravar_fora_planilha(caminho: str, lidos: list[dict], planilha: set[str]) -> list[dict]`
  - `gerar_email_pedidos_novos(novos: list[dict]) -> tuple[str, str, str]` — (assunto, texto, html). Assunto: `f"Coupa: {n} pedido(s) novo(s) fora da planilha"`. Cada pedido: nº, evento (RFQ), cidade_estado, frete, total, vendedor, itens (texto, prazo) e alertas.
  - `enviar_email_pedidos_novos(novos: list[dict], config: dict, log) -> bool` — `False` sem enviar se `novos` vazio ou sem `SMTP_USER`/`SMTP_PASSWORD` (config ou env, como `enviar_email_relatorio`).

- [ ] **Step 1: Escrever os testes**

`teste/test_pedidos_novos.py` (unittest, `tempfile.TemporaryDirectory`):
- `test_numeros_planilha_normaliza`: `{"PEDIDOS": [{"PEDIDO": " 4513771955 "}, {"PEDIDO": 4513657837}, None, {"CIDADE": "x"}]}` → `{"4513771955", "4513657837"}`.
- `test_numeros_planilha_json_quebrado_tenta_de_novo`: patch `open`/leitura para falhar 2× com `json.JSONDecodeError` e depois ler certo → retorna o conjunto; com `time.sleep` patchado.
- `test_numeros_planilha_sempre_quebrado_lanca`: 5 falhas → `ErroLeituraPlanilha`.
- `test_carregar_lidos_sem_arquivo`: → `[]`.
- `test_registrar_lido_acrescenta_e_substitui`: registra `{'pedido': '1', 'total': 'a'}`, `{'pedido': '2'}`, `{'pedido': '1', 'total': 'b'}` → arquivo tem 2 itens, o `'1'` com `total == 'b'`.
- `test_fora_planilha_recalcula`: lidos `1,2,3`, planilha `{'2'}` → arquivo e retorno = pedidos `1,3`; depois planilha `{'1','2','3'}` → `[]`.
- `test_email_lista_pedidos`: `gerar_email_pedidos_novos([{'pedido': '4513771955', 'evento': '304622', 'cidade_estado': 'Barão de Cocais MG', 'frete': 'EXW', 'total': '963,00', 'vendedor': 'MICHELI', 'itens': [{'texto': 'MOCHILA', 'prazo': '27/12/2026'}], 'alertas': ['abrir chamado no 4513771955 por ser importado.']}])` → assunto contém `"1 pedido"`; texto e html contêm `4513771955`, `304622`, `MOCHILA`, `importado`.
- `test_envio_vazio_nao_envia`: patch `_conectar_e_enviar_smtp` → `enviar_email_pedidos_novos([], {...}, log)` é `False` e o mock não foi chamado.
- `test_envio_destino_padrao`: config com SMTP e sem `emails_pedidos_novos` → mock chamado com `destinatarios == ["vendas7@venturainformatica.com.br"]`; com `emails_pedidos_novos: "a@x.com, b@x.com"` → `["a@x.com", "b@x.com"]`.

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd gerenciador && python -m unittest teste.test_pedidos_novos`
Expected: `ModuleNotFoundError: No module named 'bots.coupa.pedidos_novos'`

- [ ] **Step 3: Implementar `bots/coupa/pedidos_novos.py`** com as assinaturas acima. HTML com tabela simples, valores escapados com `html.escape`.

- [ ] **Step 4: Rodar e ver passar**

Run: `cd gerenciador && python -m unittest teste.test_pedidos_novos`
Expected: `OK`

- [ ] **Step 5: Commit** (`gerenciador`, branch `feat/pedidos-novos-coupa`)

```bash
git add bots/coupa/pedidos_novos.py teste/test_pedidos_novos.py
git commit -m "feat(coupa): arquivos e e-mail dos pedidos novos fora da planilha"
```

### Task 2: `extrair_pedidos` usa o módulo; sem PDF e sem gravar na planilha

**Files:**
- Modify: `gerenciador/bots/coupa/principal.py:92` (novos caminhos)
- Modify: `gerenciador/bots/coupa/mixins/pedidos.py:52-425`
- Test: `gerenciador/teste/test_pedidos_novos.py` (nova classe)

**Interfaces:**
- Consumes: tudo de Task 1.
- Produces: atributos do `CoupaScraper`: `ARQUIVO_PLANILHA_PEDIDOS`, `ARQUIVO_PEDIDOS_LIDOS`, `ARQUIVO_PEDIDOS_FORA_PLANILHA` = `os.path.join(ambiente.pasta_banco(), "PEDIDOS.json" | "PEDIDOS-LIDOS-COUPA.json" | "PEDIDOS-FORA-PLANILHA.json")`. Evento `tarefa_concluida` `{'evento': 'Listagem de Pedidos', 'sucesso': True, 'total': <qtd novos>, 'portal': 'coupa'}`.

- [ ] **Step 1: Escrever os testes** (classe `ExtrairPedidosFonteTest`, lê `bots/coupa/mixins/pedidos.py` como texto)
- `test_nao_baixa_pdf_nem_grava_planilha`: fonte não contém `print_page`, `PEDIDOS\\`, `caminho_pdf`, `atualizar_pedidos_excel`, `salvar_pedido_database`.
- `test_usa_modulo_pedidos_novos`: fonte contém `numeros_planilha`, `registrar_lido`, `gravar_fora_planilha`, `enviar_email_pedidos_novos`.
- `test_caminhos_no_banco`: fonte de `principal.py` contém `PEDIDOS-LIDOS-COUPA.json` e `PEDIDOS-FORA-PLANILHA.json` e `pasta_banco()`.

- [ ] **Step 2: Rodar e ver falhar**

Run: `cd gerenciador && python -m unittest teste.test_pedidos_novos`
Expected: FAIL nos 3 testes novos.

- [ ] **Step 3: Implementar**
- `principal.py`: definir os 3 atributos logo após `ARQUIVO_PEDIDOS_JSON`.
- `pedidos.py`, início: `pedidos_existentes = {p['pedido'] for p in carregar_lidos(...)} | numeros_planilha(...)`. `ErroLeituraPlanilha` → log de erro, `tarefa_concluida` com `sucesso: False` e `erro`, e retorna `[]` sem varrer.
- Remover o bloco de impressão/PDF (linhas ~305-357) e a chave `caminho_pdf`.
- Trocar `atualizar_pedidos_excel` + `salvar_pedido_database` por `registrar_lido(self.ARQUIVO_PEDIDOS_LIDOS, novo_pedido)`.
- Depois dos laços (também quando parou): `gravar_fora_planilha(self.ARQUIVO_PEDIDOS_FORA_PLANILHA, carregar_lidos(...), numeros_planilha(...))`, `enviar_email_pedidos_novos(pedidos_detalhes, self.config, logger)`, e o `tarefa_concluida` com `total = len(pedidos_detalhes)` e `'portal': 'coupa'`.

- [ ] **Step 4: Rodar e ver passar**

Run: `cd gerenciador && python -m unittest teste.test_pedidos_novos teste.test_api_json_coupa teste.test_reabertura_coupa`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add bots/coupa/principal.py bots/coupa/mixins/pedidos.py teste/test_pedidos_novos.py
git commit -m "feat(coupa): pedidos do portal viram aviso por e-mail, sem PDF e sem gravar na planilha"
```

### Task 3: Gerenciador encaminha `extrair_pedidos`

**Files:**
- Modify: `gerenciador/gerenciador.py:1406`
- Test: `gerenciador/teste/test_pedidos_novos.py`

**Interfaces:**
- Produces: `comando_para_robo {modo: 'extrair_pedidos', portal: 'coupa'}` chega em `bot_coupa.iniciar_tarefa('extrair_pedidos', dados)` na mesma fila de `extrair`/`verificar`.

- [ ] **Step 1: Teste** `test_gerenciador_encaminha_extrair_pedidos`: fonte de `gerenciador.py` casa `elif modo in \[[^\]]*'extrair_pedidos'[^\]]*\]:` (lista que contém `'extrair'`, `'verificar'`, `'responder'`).
- [ ] **Step 2: Rodar e ver falhar** — `cd gerenciador && python -m unittest teste.test_pedidos_novos` → FAIL.
- [ ] **Step 3: Implementar** — `elif modo in ['extrair', 'verificar', 'responder', 'extrair_pedidos']:`.
- [ ] **Step 4: Rodar** `cd gerenciador && python -m unittest teste.test_pedidos_novos teste.test_modo_resposta` → `OK`.
- [ ] **Step 5: Commit**

```bash
git add gerenciador.py teste/test_pedidos_novos.py
git commit -m "feat(gerenciador): encaminha extrair_pedidos ao robô do Coupa"
```

### Task 4: Servidor aceita `extrair_pedidos`

**Files:**
- Modify: `portaismaestro/server.js:578` e `server.js:1068-1083`
- Test: `portaismaestro/tests/robos-permissao.test.js`

**Interfaces:**
- Produces: `estado_global.status = 'extraindo_pedidos'` ao receber o comando (Coupa); volta a `'ocioso'` no `tarefa_concluida` pelo caminho já existente.

- [ ] **Step 1: Teste** em `robos-permissao.test.js`, no teste do operador: `operador.socket.emit('comando_direto', { modo: 'extrair_pedidos', portal: 'coupa' })` → robô recebe `{ modo: 'extrair_pedidos', portal: 'coupa' }`.
- [ ] **Step 2: Rodar e ver falhar** — `cd portaismaestro && node --test tests/robos-permissao.test.js` → FAIL (timeout/descartado).
- [ ] **Step 3: Implementar** — `'extrair_pedidos'` em `MODOS_AUDITADOS`; `else if (dados.modo === 'extrair_pedidos') estado_global.status = 'extraindo_pedidos';`.
- [ ] **Step 4: Rodar** `cd portaismaestro && node --test` → todos passam.
- [ ] **Step 5: Commit** (`portaismaestro`, branch `feat/pedidos-novos-coupa`)

```bash
git add server.js tests/robos-permissao.test.js
git commit -m "feat(coupa): servidor aceita o comando extrair_pedidos"
```

### Task 5: Botão "Pedidos Novos" no coupa.html

**Files:**
- Modify: `portaismaestro/public/coupa.html` (grid ~384-387, estado ~550-660, `executarTarefa` ~732, `tarefa_concluida` ~1046)
- Test: `portaismaestro/tests/coupa-pedidos-novos.test.js` (novo, lê o HTML como texto)

**Interfaces:**
- Consumes: Task 4 (`status === 'extraindo_pedidos'`), Task 2 (`tarefa_concluida` `'Listagem de Pedidos'` com `total`).

- [ ] **Step 1: Teste** `coupa-pedidos-novos.test.js`:
  - HTML contém `id="btn-pedidos"` e `executarTarefa('extrair_pedidos')`.
  - HTML contém `'extraindo_pedidos'` e `'Listagem de Pedidos'`.
- [ ] **Step 2: Rodar e ver falhar** — `cd portaismaestro && node --test tests/coupa-pedidos-novos.test.js` → FAIL.
- [ ] **Step 3: Implementar**
  - Botão `btn-pedidos` (`fa-file-invoice`, texto "Pedidos Novos"), `grid-column: span 2`, acima de "Responder Eventos".
  - `executarTarefa('extrair_pedidos')`: `confirm("Procurar pedidos novos no portal e enviar por e-mail?")` e emite.
  - No sincronizar de estado: status `extraindo_pedidos` → texto "A PROCURAR PEDIDOS...", `btn-pedidos` vira "PARAR PEDIDOS" (`solicitarParada`), Extrair/Verificar/Responder desabilitados; nos outros estados o botão volta ao normal (e fica desabilitado quando os outros ficam).
  - `tarefa_concluida` com `evento === 'Listagem de Pedidos'`: mostra no modal/aviso existente `"N pedido(s) novo(s). E-mail enviado."` (ou `"Nenhum pedido novo."`; ou o `erro`).
- [ ] **Step 4: Rodar** `cd portaismaestro && node --test` → todos passam.
- [ ] **Step 5: Verificar no navegador** — subir o portal (preview), abrir `coupa.html`, conferir botão visível, confirm, e estado `extraindo_pedidos` (screenshot).
- [ ] **Step 6: Commit**

```bash
git add public/coupa.html tests/coupa-pedidos-novos.test.js
git commit -m "feat(coupa): botão Pedidos Novos na tela do Coupa"
```
