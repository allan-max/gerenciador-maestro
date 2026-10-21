# Melhorias dos robôs: instalação em produção e testes

Vale para as fases 0 a 5 (PRs #8 a #13 do `gerenciador-maestro` e #13 do `portaismaestro`). O plano está em `plano.md` (PR #7).

## 1. Merge (GitHub)

1. As PRs do gerenciador estão **empilhadas**. Faça o merge na ordem: **#8 → #9 → #10 → #11 → #12 → #13**. A #7, de documentação, pode entrar a qualquer momento.
   - Depois de cada merge, confira se a PR seguinte passou a apontar para o `main`. O GitHub faz isso sozinho quando a branch anterior é apagada no merge. Se não fizer, use "Edit" na PR, troque a base para `main` e faça o merge.
2. Portal: a #13 do `portaismaestro` pode entrar antes ou depois. Se a #12 (auditoria) entrar antes, pode ser preciso um rebase simples, porque as duas mexem em `app.js` e `style.css`.

## 2. Antes de atualizar (máquina de produção)

1. **Pare o gerenciador.** Use Ctrl+C na janela e espere fechar. Confira no Gerenciador de Tarefas que não sobrou nenhum `python.exe` do gerenciador.
2. **Faça backup da base**, copiando para uma pasta com a data (por exemplo `\\SERVIDOR2\Publico\ALLAN\database\backup-2026-10-20\`):
   - `Banco-de-dados\COTAÇÕES.json` e `Banco-de-dados\PEDIDOS.json`;
   - `eventos.jsonl` e `eventos_nimbi.jsonl`.
3. **Faça backup da pasta do gerenciador:** copie `C:\Users\Administrator\Desktop\gerenciador-maestro` inteira para `gerenciador-maestro-antes-melhorias`.

## 3. Atualizar o código

- Se a pasta de produção for um clone git: `git fetch origin`, `git checkout main` e `git pull`.
- Se não for, copie do `main` atualizado para `C:\Users\Administrator\Desktop\gerenciador-maestro`:
  - **novos:** `ambiente.py`, `log_robos.py` e a pasta `scripts\`;
  - **alterados:** `gerenciador.py`, `planilha_manager.py`, `resposta_cotacao.py`, `instancia_unica.py`, `testar_coupa.py`, `.gitignore` e **a pasta `bots\` inteira**;
  - **testes (opcional):** `test_*.py`.
  - **Não sobrescreva:** `CONFIG.json`, `.env`, `banco_usuarios.json`, `banco_ips_bloqueados.json`, a pasta `logs\` e `gerenciador.instancia`.
- Nenhuma dependência nova, então não é preciso rodar `pip install`.
- No `.env` de produção, o `URL_SERVIDOR` **não** pode apontar para `localhost` ou `127.0.0.1`, e o `MAESTRO_TESTE` **não** pode estar definido. Nesses dois casos o gerenciador entra em modo teste e passa a gravar em `base_teste\`.
- Opcional, no `CONFIG.json`:
  - `"verificacao_max_paginas": 60`, o teto de páginas da verificação;
  - `"vale_url_verificacao": "..."`, só se o evento usado para pedir o OTP da Vale mudar.

## 4. Conferir antes de ligar

Na pasta do gerenciador:

```
python -m unittest discover -p "test_*.py"
```

Devem passar todos os 90 testes, com `OK`. Eles não tocam o `\\SERVIDOR2`.

## 5. Correções de dados (uma vez, com o gerenciador parado)

Os dois scripts só **mostram** o que fariam. Para aplicar, rode de novo com `--gravar`, que faz backup ao lado antes de gravar.

1. Corrigir as linhas com "NÃO" de acento quebrado. Devem ser cerca de 104:
   ```
   python scripts\normalizar_respostas.py
   python scripts\normalizar_respostas.py --gravar
   ```
2. Recuperar as cotações que sumiram em 13 e 14/10:
   ```
   python scripts\recuperar_perdidas.py --de 2026-10-13 --ate 2026-10-14
   python scripts\recuperar_perdidas.py --de 2026-10-13 --ate 2026-10-14 --gravar
   ```
   - As **abertas** saem do JSONL, e o robô as extrai de novo no próximo ciclo. As bloqueadas voltam a ser bloqueadas.
   - As **vencidas** vão para `relatorios\recuperacao_2026-10-13_2026-10-14.csv`, para conferência manual.

## 6. Ligar e acompanhar o primeiro ciclo

1. Inicie o gerenciador como de costume (`python gerenciador.py`).
2. Faça o deploy do portal (#13) e reinicie o `Site-Maestro` no PM2.
3. No primeiro minuto, confira:
   - `logs\gerenciador.log` sem erros;
   - em `\\SERVIDOR2\Publico\ALLAN\Logs\log-coupa.txt`, as linhas novas no formato `2026-10-20 10:15:02 [MAQUINA] [INFO] ...`. O arquivo antigo, de 41 MB, vira `log-coupa.txt.1` na primeira gravação. **Isso é esperado.**

## 7. O que testar

| # | Teste | Resultado esperado |
|---|---|---|
| 1 | Primeiro ciclo do Coupa (piloto ou "Extrair" + "Verificar" no painel) | A Atividade mostra "Ciclo automático do Coupa terminou" e, no painel **Robôs**, "Última verificação … N/M pendentes encontradas · X status alterados". No log aparece a linha "Status de resposta: vazio → RESPONDIDO: …". Com 50 mudanças ou mais, aparece `COTAÇÕES antes-verificacao <data>.json` ao lado da base. As não encontradas vão para `relatorios\verificacao_coupa_<data>.csv`. |
| 2 | Cotação já respondida no Coupa, ainda no prazo | Vira **RESPONDIDO** na próxima verificação, sem esperar o vencimento. |
| 3 | Responder uma cotação pelo portal | O Coupa fica com o rascunho salvo. A cotação passa a **RASCUNHO**, e o dashboard mostra o cartão "Rascunhos a enviar". Quando alguém **envia** no Coupa, a próxima verificação troca para RESPONDIDO. Se nunca for enviada, vira NÃO RESPONDIDO depois do vencimento. |
| 4 | Responder com um item sem preço que dê erro | A tarefa termina com **erro**, mostrando o item que falhou ("Rascunho salvo, mas com falha em: item N (preço)"), e não com "sucesso". |
| 5 | Marcar uma cotação RESPONDIDO à mão e esperar uma verificação | Continua RESPONDIDO, porque a regra nunca rebaixa uma cotação respondida. |
| 6 | Primeiro ciclo da Vale depois das 09:00 | O painel da Vale pede o OTP. Depois de digitado, aparece "Verificação Vale: …" e as cotações da Vale começam a ser atualizadas. As 638 que estão em branco devem cair bastante. |
| 7 | No mesmo ciclo, **não** digitar o OTP | Depois de 3 min, a Atividade mostra "Verificação da Vale pulada", e o ciclo segue normalmente. |
| 8 | Clicar em "Ligar" com o robô já ligado | A mensagem "já está ligado" aparece e não abre um segundo Chrome. |
| 9 | Botão "Assumir controle" (com a Vale rodando, entrar no Coupa) | A Vale desliga e o Coupa liga. |
| 10 | Painel admin: ligar ou desligar o acesso à **planilha** de um usuário | Muda o acesso à planilha. Antes, mudava o acesso ao dashboard. |
| 11 | Nos logs dos dias seguintes | O evento 287782 (e os outros reabertos encerrados) não aparecem mais em todo ciclo. Somem os "cannot access local variable 're'". As rajadas de "Oops" viram "pausa de 10 min". |
| 12 | `testar_local.bat` numa máquina de desenvolvimento | Cria `base_teste\` ao lado do código. A data de modificação do `COTAÇÕES.json` no `\\SERVIDOR2` **não muda**. |
| 13 | `python testar_coupa.py` ou o robô fora do gerenciador, na produção | O robô roda em modo teste (`base_teste`). Na produção, sem o gerenciador, ele se recusa a iniciar e explica o motivo. |
| 14 | Robô sem extrair por mais de 6 h (por exemplo, com o piloto travado) | O evento "Robô X sem extração concluída há N h" aparece na Atividade (painel Robôs, em vermelho) e chega um e-mail, **uma vez só**. |
| 15 | Log chegando a 10 MB | Roda para `.1` e o painel preto continua mostrando as linhas. |

## 8. Se algo der errado (voltar)

1. Pare o gerenciador.
2. Restaure a pasta `gerenciador-maestro-antes-melhorias`.
3. Só se a base estiver errada, restaure os JSONs do backup do passo 2. As cotações que estão como RASCUNHO continuam funcionando com o código antigo: ele as trata como sem status.
4. Ligue o gerenciador.

O portal (#13) pode ficar. Com o gerenciador antigo, ele só mostra "Nenhuma verificação" no painel Robôs.
