# Melhorias dos robôs Coupa e Vale: plano

Decisões tomadas por grilling em 17/10/2026, com base no código do `main` (`deb9cdd`) e em 14 dias de logs de produção (03 a 17/10, `\\SERVIDOR2\Publico\ALLAN\Logs`). Coupa e Vale **continuam com código separado**: toda correção comum é feita nos dois arquivos. Cada fase é uma PR própria, com testes. Os prompts de cada fase estão em `prompts.md`.

## Diagnóstico

### Como o status RESPOSTA é atualizado hoje
- Só a etapa `verificar` atualiza. Ela lê `s-datatable-cell-num_responses` na lista (`bots/coupa/mixins/verificacao.py:87`) e chama `planilha_manager.atualizar_status_respostas` (`planilha_manager.py:203`).
- `atualizar_status_respostas` só grava **depois do vencimento**, ou no próprio dia a partir das 14h30 (`planilha_manager.py:209, 247`). Ele grava RESPONDIDO ou NÃO RESPONDIDO e pode sobrescrever um RESPONDIDO marcado à mão no portal.
- O Coupa verifica no máximo 40 páginas e o `except: pass` em `verificacao.py:88` transforma uma célula que não leu em 0 respostas. A última verificação viu 2.721 números de ~3.600 possíveis.
- A Vale **nunca verifica**: o piloto não chama `verificar` (`gerenciador.py:1828-1863`) e o `verificar` exige OTP. Não aparece nenhuma vez no `log-vale.txt`.
- O `responder` só **salva um rascunho** (`button.s-save`); quem envia ao cliente é uma pessoa. Ele não atualiza o banco.

### Números em 17/10 (`COTAÇÕES.json`: 13.009 linhas)
- RESPOSTA: vazio/None 5.198 · NÃO RESPONDIDO 4.681 · RESPONDIDO 3.023 · **`NÃƒO RESPONDIDO` com codificação quebrada: 104**.
- Já vencidas e em branco: **344 do Coupa** e **638 da Vale**.
- 278 números de cotação duplicados (640 linhas). Em 85 números, a mesma cotação está como RESPONDIDO numa linha e NÃO RESPONDIDO em outra.

### Erros vistos nos logs
| Problema | Evidência | Causa no código |
|---|---|---|
| Cotações gravadas que sumiram | 7 gravadas com sucesso em 13/10 (ex.: Vale 54328, Coupa 292832) não estão no banco. Em 13/10 houve 575× `WinError 5` e 209× `WinError 32` no `COTAÇÕES.json`. Em 14 e 16/10 houve 20 sessões com "Banco interno carregado: 0" rodando junto com a produção. | O modo teste (`bots/coupa/principal.py:75-79`) não redireciona o `COTAÇÕES.json` (`planilha_manager.py:31`). Processos fora da trava de instância gravam na base de produção. |
| Ciclo da Vale abortado | 34× `cannot access local variable 're'` (08 a 13/10). O e-mail de relatório saía normalmente. | `import re` dentro de `gerar_word` (`bots/vale_bot.py:1678`) torna `re` local, e a linha 1709 falha. |
| Eventos "reabertos" em loop | Coupa: 20 eventos reextraídos 527× (287782 com 72×). Total de 3.135× "Evento encerrado". Vale: 725×. | Quando a reextração devolve `SKIPPED` (encerrado), a `vencimento_tabela` do JSONL não é atualizada (`extracao.py:614-624`, `vale_bot.py:552-560`). A data continua diferente e o evento volta em todo ciclo. |
| Rajadas de "Oops" e excesso de logins | 426× Oops (147 numa hora em 08/10). Uma sessão de 14/10 fez 80 logins. | Novo login imediato a cada Oops (`extracao.py:94-127`), sem pausa. |
| Captcha nunca tratado | Ainda não apareceu nos logs, mas trava quando aparecer. | `inicio_captcha` é usado sem ter sido criado (`autenticacao.py:128`, `vale_bot.py:258`). O erro é engolido e o login entra em loop infinito. |
| Dashboard conta errado | — | `gerenciador.py:1286` testa `"NO"` (era "NÃO", perdeu o acento). "NÃO RESPONDIDO" conta como respondida. |
| Vale parada sem erro | De 03 a 05/10: 17 sessões ficaram "prontas" e nunca extraíram. | O piloto não confere se a extração rodou (`gerenciador.py:1797-1809`). |
| Logs sem data | As linhas só têm `HH:MM:SS`. `log-coupa.txt` está com 41 MB, sem rotação. | `utilitarios.py:80-98`, `vale_bot.py:48-60`. |

## Decisões

| # | Decisão |
|---|---|
| D1 | Ordem: (a) status das respondidas, (b) estabilidade, (c) velocidade, (d) organização. |
| D2 | Pode mexer no `gerenciador-maestro` e no `portaismaestro`; cada fase diz em qual. |
| D3 | **Regras do RESPOSTA:** (1) RESPONDIDO assim que a coluna de respostas passar de 0, sem esperar o vencimento; (2) NÃO RESPONDIDO só depois do vencimento; (3) RESPONDIDO nunca volta para NÃO RESPONDIDO, seja manual ou do robô; (4) o `responder` do robô grava **RASCUNHO** ao salvar com sucesso; (5) RASCUNHO vira RESPONDIDO na verificação, ou NÃO RESPONDIDO depois do vencimento. |
| D4 | Premissa (Q14): a coluna `num_responses` conta só respostas **enviadas**. A Fase 2 confere isso com um rascunho real antes de ativar a regra. |
| D5 | **Verificação a partir das pendências:** a lista de alvos são as cotações do robô com RESPOSTA vazio ou RASCUNHO. A lista do portal é percorrida até achar todas ou passar da mais antiga. O fim do ciclo registra encontradas e não encontradas. Uma célula que não deu para ler fica sem status, nunca vira 0. |
| D6 | Pendências antigas (344 + 638) são resolvidas na primeira rodada. As que não estiverem mais no portal ficam em branco e entram num relatório; nenhum status é inventado. |
| D7 | **Vale:** `verificar` uma vez por dia (09:00) no ciclo do piloto. O portal avisa que o OTP chegou; sem OTP em 3 min, a verificação é pulada e o motivo vai para o log, sem travar. |
| D8 | **Proteção da base:** o modo teste usa uma base própria (`COTAÇÕES.json` e JSONLs). Nenhuma gravação na base sem a trava de instância única. |
| D9 | **Recuperação de 13/10:** as que ainda não venceram são reextraídas (tiradas do JSONL); as vencidas vão para uma lista de conferência. |
| D10 | Evento encerrado ou reextraído grava `vencimento_tabela` e `status` no JSONL, para não voltar no próximo ciclo. |
| D11 | Oops repetido: pausa de 10 min antes de tentar de novo. Depois de 3 pausas, o ciclo é abandonado e o motivo é registrado. |
| D12 | **Piloto local:** chama os robôs direto, sem ida e volta pelo Node. Recusa `ligar_robo` manual no meio de um ciclo. Ciclo "ok" só se a extração rodou. Alerta quando um robô fica mais de 6 h sem extrair. |
| D13 | Logs: cada linha com data e máquina, rotação a cada 10 MB com 5 arquivos (só Coupa e Vale). |
| D14 | Portal (`dashboard2` → Atividade): quantidade de RASCUNHO, última verificação por robô e o alerta da D12. |
| D15 | Senhas: já trocadas, fora do escopo. Não versionar a senha nova no `CONFIG.json` (repositório público). |
| D16 | Sem unificar Coupa e Vale. |

## Fases

| Fase | Repositório | Conteúdo | Decisões |
|---|---|---|---|
| 0, correções rápidas | gerenciador | captcha, `re` da Vale, contagem do dashboard, normalizar as 104 linhas com `NÃƒO`, loop de reabertos, limpeza (`forcar_troca` sem tratamento, `extrair_pedidos` que nada chama, `executar_fluxo` vazio, `admin_alterar_acesso_dashboard` duplicado, código morto em `planilha_manager.py:276-290`) | D10, D16 |
| 1, proteção da base | gerenciador | base de teste separada, trava antes de gravar, recuperação de 13/10 | D8, D9 |
| 2, status no Coupa | gerenciador | novas regras do RESPOSTA, RASCUNHO no `responder`, verificação a partir das pendências e relatório das antigas | D3–D6 |
| 3, status na Vale | gerenciador | as mesmas regras e verificação diária com OTP | D3–D7 |
| 4, piloto e estabilidade | gerenciador | piloto local, proteções de ciclo, pausa em Oops, alerta de inatividade | D11, D12 |
| 5, logs e portal | gerenciador + portaismaestro | formato e rotação dos logs, avisos na tela Atividade | D13, D14 |

As fases 2 e 3 dependem da 1, porque mudam muitos status de uma vez e precisam da base protegida. As outras são independentes.

## Riscos

- **D4 errada:** se `num_responses` contar rascunhos, a regra (1) da D3 marca RESPONDIDO cedo demais. Por isso a Fase 2 confere antes.
- **Primeira rodada da Fase 2:** a mudança de muitos status de uma vez exige backup do dia antes e um relatório de diferenças.
- **Seletores em português** (XPaths com texto) quebram se o portal mudar de idioma ou de layout. Fica fora deste plano.
- O dashboard novo do `portaismaestro` calcula os status por conta própria. A Fase 0 corrige só o `gerenciador.py:1286`; a Fase 5 confere se o portal trata RASCUNHO.
