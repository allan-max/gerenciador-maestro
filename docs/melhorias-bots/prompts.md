# Prompts de implementação: melhorias dos robôs

Cole um prompt por sessão, na ordem das fases, cada um numa branch nova a partir do `main`. Todos partem de `docs/melhorias-bots/plano.md`: leia o plano antes. Regras para todas as fases:
- Coupa (`bots/coupa/`) e Vale (`bots/vale_bot.py`) continuam com código separado (D16). Toda correção comum é feita nos dois.
- Use `logger.exception` em vez de `except: pass` nos trechos alterados.
- Testes com `unittest`, como `test_planilha_persistencia.py`, sem Selenium real: simule o driver e use bases temporárias.
- Nunca grave na base de produção (`\\SERVIDOR2\...`) durante os testes.
- Uma PR por fase, com o plano de testes.

---

## Fase 0: correções rápidas (gerenciador-maestro)

Leia `docs/melhorias-bots/plano.md` (seção "Erros vistos nos logs") e corrija, nos dois robôs quando se aplicar:

1. **Captcha:** inicialize `inicio_captcha = None` antes do laço de login em `bots/coupa/mixins/autenticacao.py` (~linha 128) e em `bots/vale_bot.py` (~linha 258). Troque o `except: pass` do laço (`autenticacao.py:256`, `vale_bot.py:389`) por `logger.exception` e limite o laço a 10 min no total, depois desiste com erro.
2. **`re` da Vale:** remova os `import re` locais de `vale_bot.py` (linhas 1678 e 1845). O `re` já é importado na linha 10.
3. **Contagem do dashboard** (`gerenciador.py:1286-1287`): compare o status normalizado sem acento (`unicodedata`), exatamente `NAO RESPONDIDO` e `RESPONDIDO`, sem substring `"NO"`. Revise outras strings com acento perdido no mesmo arquivo.
4. **104 linhas com `NÃƒO RESPONDIDO`:** crie `scripts/normalizar_respostas.py`, que usa `planilha_manager` (gravação atômica). Ele troca a forma quebrada por `NÃO RESPONDIDO`, faz backup antes e roda em `--dry-run` por padrão, mostrando a quantidade.
5. **Reabertos em loop** (D10): em `extracao.py:614-624` / `vale_bot.py:552-560`, quando a reextração terminar `SKIPPED` (encerrado, 0 itens, Oops), atualize no JSONL `vencimento_tabela` com a data da tela e `status` com o motivo. Assim o evento não volta no próximo ciclo. Use a função de gravação do JSONL que o robô já tem, sem criar outra.
6. **Limpeza:**
   - remova `forcar_troca` do gerenciador e dos robôs (ninguém trata);
   - remova `processar_fila_extracao`/`executar_fluxo` vazios ou ligue o `extrair_pedidos` ao `comando_para_robo`. Pergunte qual antes de decidir;
   - remova a definição duplicada de `admin_alterar_acesso_dashboard` (mantenha a de ~1055, que é a que vale hoje);
   - remova o código morto depois do `return` em `planilha_manager.py:276-290`;
   - corrija `planilha_manager.lock.release()` sem import em `bots/coupa/mixins/planilhas.py:160, 189, 407`.

**Testes:**
- captcha com `inicio_captcha` (driver falso que mostra captcha e expira);
- `gerar_word` da Vale sem histórico não quebra;
- contagem com os 4 valores de RESPOSTA;
- script de normalização (dry-run e gravação);
- reaberto encerrado não volta na próxima varredura.

---

## Fase 1: proteção da base (gerenciador-maestro)

Contexto: em 13/10, 7 cotações gravadas com sucesso sumiram do `COTAÇÕES.json`. Houve rajadas de `WinError 5/32` e sessões com "Banco interno carregado: 0" rodando junto com a produção (plano, D8 e D9).

1. **Modo teste com base própria:** `planilha_manager.py:31-36` tem caminhos fixos e `iniciar(caminho)` ignora o argumento. Faça o modo teste (`URL_SERVIDOR=http://localhost:8000`, `bots/coupa/principal.py:75-79`, e o equivalente na Vale, `vale_bot.py:87`) usar `COTAÇÕES_teste.json`, `PEDIDOS_teste.json` e JSONLs de teste ao lado do código. Use uma única função que decide os caminhos.
2. **Trava antes de gravar:** `planilha_manager` e a gravação dos JSONL (`gerenciador.py:1421-1426` e `banco_dados.py:139-147`) só gravam na base de produção se este processo tiver a trava de `instancia_unica.py`. Sem ela, recusam e registram. O `save_db` dos JSONL deve usar temporário único + `os.replace`, como o `planilha_manager`.
3. **`db_eventos` desatualizado:** recarregue o JSONL antes de cada gravação completa (`banco_dados.py:139-147`), para não ressuscitar eventos removidos pelo gerenciador.
4. **Recuperação de 13/10** (D9): crie `scripts/recuperar_13_10.py`, que lê o `log-coupa.txt`/`log-vale.txt`, procura `JSON salvo com sucesso` e confere quais números não estão no `COTAÇÕES.json`. Para os que ainda não venceram, tira a linha do JSONL (para reextrair). Para os vencidos, gera `recuperacao_13_10.csv`. Roda em `--dry-run` por padrão.

**Testes:**
- modo teste nunca abre o caminho de produção;
- gravação sem trava é recusada;
- dois processos simulados não perdem linhas;
- script de recuperação com log de exemplo.

---

## Fase 2: status das respondidas no Coupa (gerenciador-maestro)

Contexto: plano, D3 a D6. Antes de ligar a regra (1) da D3, **confira a D4**: salve um rascunho real num evento de teste e veja se `num_responses` muda. Se mudar, pare e avise.

1. **Regras em `planilha_manager`:** troque a regra de tempo de `atualizar_status_respostas` (`planilha_manager.py:203-251`) por uma função pura `novo_status(atual, respostas, vencimento, agora)`:
   - `respostas > 0` → RESPONDIDO, a qualquer hora;
   - `respostas == 0` e vencida → NÃO RESPONDIDO, mas RESPONDIDO nunca volta;
   - `respostas is None` (não leu) → mantém o atual;
   - RASCUNHO vira RESPONDIDO ou, depois do vencimento, NÃO RESPONDIDO.
2. **RASCUNHO:** em `bots/coupa/mixins/resposta.py`, quando o salvar realmente der certo, grave RASCUNHO na cotação (se o status atual não for RESPONDIDO). Corrija o sucesso falso: hoje as exceções de preço, prazo e salvar são engolidas e o resultado ainda é sucesso. Corrija também o `driver.close()` da linha 412 e o nome de arquivo sem sanitização (`resposta.py:62, 69`, use `os.path.basename`) e apague os temporários.
3. **Verificação a partir das pendências** (`bots/coupa/mixins/verificacao.py`):
   - os alvos são as cotações do Coupa com RESPOSTA vazio ou RASCUNHO;
   - percorra a lista até achar todos ou passar do número mais antigo, sem o limite fixo de 40 páginas, mas com um teto de segurança configurável;
   - célula que não deu para ler = `None`, nunca 0;
   - no fim, registre `encontradas/total` e grave as não encontradas em `relatorios/verificacao_coupa_AAAA-MM-DD.csv`.
4. **Duplicadas:** quando o mesmo número tiver várias linhas, aplique o status em todas, seguindo a regra.
5. A primeira execução resolve as 344 antigas (D6). Faça backup do dia antes de gravar e mostre no log quantas mudaram, de qual status para qual.

**Testes:**
- `novo_status` (tabela com todos os casos, incluindo manual RESPONDIDO e `None`);
- RASCUNHO no responder;
- verificação com lista falsa de várias páginas, com célula ilegível e alvos ausentes.

---

## Fase 3: status das respondidas na Vale (gerenciador-maestro)

Contexto: plano, D3 a D7. A Vale nunca verificou em produção. Use o `novo_status` da Fase 2 (via `planilha_manager`, que é comum) e repita, no `bots/vale_bot.py`, o RASCUNHO no `responder_evento` e a verificação a partir das pendências.

1. **`verificar_eventos`** (`vale_bot.py:2202-2286`):
   - a URL de um evento fixo (`quote_request_id=6213`, linha 2209) vai para o `CONFIG.json`;
   - o seletor `coupa_datatable_row` é da tela antiga: confira com a extração, que usa `coupaTable__row`;
   - célula vazia (`"" != "0"` vira RESPONDIDO hoje, linha 2307) passa a valer `None`;
   - o `_colorir_excel_verificacao` com retry infinito (2312-2320) e o Excel, que não é mais usado, saem.
2. **OTP** (D7): pedir pelo portal; esperar até 3 min; sem código, registrar `verificacao_vale_pulada` (via `eventos.registrar`) e seguir.
3. **Piloto:** chamar `verificar` da Vale uma vez por dia, no primeiro ciclo depois das 09:00 (`gerenciador.py:1828-1863`).
4. **JSONL da Vale:** pare de gravar `respondido: True` só por ter extraído (`vale_bot.py:1779`). Use um campo `extraido` e deixe `respondido` para a verificação, como no Coupa.
5. Primeira execução: resolve as 638 antigas, com backup e relatório, como na Fase 2.

**Testes:**
- OTP ausente pula sem travar;
- célula vazia não vira RESPONDIDO;
- piloto chama `verificar` só uma vez por dia.

---

## Fase 4: piloto e estabilidade (gerenciador-maestro)

Contexto: plano, D11 e D12. Hoje o piloto (`gerenciador.py:1776-1863`) manda `comando_direto` para o Node, que devolve como `comando_para_robo`. Se o socket cai, o emit falha calado e o ciclo vira "ok".

1. **Piloto local:** extraia a lógica de `comando_para_robo` (ligar, tarefa, desligar) para funções, chamadas direto pelo piloto e pelo handler do socket. O status para o portal continua sendo emitido.
2. **Um ciclo por vez:** uma trava indica "ciclo em andamento". `ligar_robo` manual durante o ciclo responde "ocupado" e não cria um segundo `CoupaScraper`/`ValeScraper` (hoje sobrescreve sem fechar o driver, `gerenciador.py:816-822`). O mesmo vale para duas tarefas ao mesmo tempo no mesmo driver.
3. **Ciclo "ok" de verdade:** só se a tarefa rodou e terminou sem erro (o `iniciar_tarefa` devolve o resultado). Se não, registre `ciclo_falhou` com o motivo.
4. **Inatividade:** se um robô ficar mais de 6 h sem `extrair` concluído, registre `robo_inativo` (`eventos.registrar`) e mande o e-mail de erro uma vez.
5. **Oops** (D11): em `extracao.py:94-127` e no equivalente da Vale, depois de 3 Oops seguidos, pause 10 min (respeitando `solicitacao_parada`). Depois de 3 pausas, abandone o ciclo com motivo. Conte os logins por ciclo e registre.
6. **Threads no import:** mova o início do piloto, dos monitores e do agendamento (`gerenciador.py:1865-1876`) para o `if __name__ == "__main__"`.
7. **Parada perdida:** não zere `solicitacao_parada` no início da varredura (`vale_bot.py:487` e o equivalente no Coupa), e o `responder` passa a checar a parada.

**Testes:**
- piloto com robôs falsos: ciclo ok, ciclo com falha, socket caído;
- `ligar_robo` concorrente recusado;
- inatividade gera um único alerta;
- importar `gerenciador` não inicia threads;
- pausa de Oops.

---

## Fase 5: logs e portal (gerenciador-maestro + portaismaestro)

**gerenciador-maestro** (D13):
1. `log-coupa.txt` e `log-vale.txt` (`bots/coupa/mixins/utilitarios.py:80-98`, `vale_bot.py:48-60`) passam a usar o formato `AAAA-MM-DD HH:MM:SS [MAQUINA] [NIVEL] msg` e `RotatingFileHandler` de 10 MB com 5 arquivos.
2. Se o servidor de rede estiver fora, cai só para o stdout e registra um aviso, sem quebrar o import (`vale_bot.py:48-60` faz `makedirs` no UNC durante o import).
3. Confira que o `monitorar_log_coupa`/`vale` (`gerenciador.py:427-473`) continua acompanhando o arquivo depois da rotação (detectar a troca de arquivo, ou seja, inode/tamanho menor).
4. Emita no `eventos.py`: `verificacao_concluida {robo, encontradas, total, mudancas}` e `robo_inativo`.

**portaismaestro** (D14), tela Atividade do `dashboard2`:
1. Card com a quantidade de cotações em RASCUNHO e um filtro para elas na carteira.
2. "Última verificação" por robô (hora, encontradas/total) e o alerta `robo_inativo`.
3. Confira que o dashboard de cotações trata `RASCUNHO` como pendente (nem respondida nem não respondida).

**Testes:**
- formato e rotação do log;
- o monitor lê depois da rotação;
- no portal, um teste das funções puras novas, no padrão `tests/*.test.js`.
