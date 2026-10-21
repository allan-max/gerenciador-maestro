# Melhorias dos robôs: instalação em produção e testes

Fases 0 a 5 (PRs #8 a #13 do `gerenciador-maestro` e #13 do `portaismaestro`) e as correções seguintes (#15 em diante). O plano está em `plano.md`.

A produção roda em `C:\Users\Administrator\Desktop\gerenciador-maestro`, que é um clone git. A pasta `\\SERVIDOR2\Publico\ENZO\gerenciador-maestro` é só uma cópia para leitura.

## 1. Como uma mudança chega à produção

Desde a #21, o **atualizador automático** (`scripts\atualizador.py`) roda a cada 5 minutos pelo Agendador de Tarefas ("Maestro Atualizador"). Não é preciso entrar no servidor para atualizar.

**Gerenciador:**
1. Só age com **nenhum robô trabalhando**. Isso quer dizer nenhuma tarefa do Coupa ou da Vale, nem na fila, e nenhum ME, Ariba ou Findes aberto. Ele lê o `gerenciador.estado`, gravado pelo gerenciador a cada 20 s.
2. Guarda uma cópia de `CONFIG.json`, `.env`, `banco_usuarios.json` e `banco_ips_bloqueados.json`. Faz `git merge --ff-only` com o `main` e devolve esses arquivos.
3. Roda os testes. **Se falharem, volta para a versão anterior** e avisa na Atividade e por e-mail.
4. Reinicia o gerenciador numa janela nova ("Maestro Gerenciador"). Se o gerenciador estiver fechado, ele não é aberto.

**Portal (Site-Maestro no pm2):**
1. Espera os robôs pararem, porque reiniciar o portal derruba a conexão do gerenciador.
2. Faz `git merge --ff-only`. Roda `npm ci` só quando `package*.json` mudou, e depois `node --check server.js` e `pm2 restart Site-Maestro`.
3. Se o site não ficar de pé, **volta para a versão anterior** e reinicia de novo.

Vários merges dentro dos mesmos 5 minutos são aplicados juntos, numa única rodada.

**Merge no GitHub:** use **"Create a merge commit"**. O "Rebase and merge" recria os commits, o que bagunça PRs empilhadas.

**Commits direto no `main`** também vão para a produção em até 5 minutos, desde que passem nos testes.

## 2. Ligar, desligar e acompanhar

- **Liga/desliga:** a chave `atualizacao_automatica` (`sim`/`não`) fica em **Configurações** no dashboard2. Com `não`, nada é baixado.
- **Log:** `logs\atualizador\atualizador.log`. Uma rodada sem novidade não escreve nada. "atualização pendente; robôs ocupados" quer dizer que o atualizador vai tentar de novo em 5 min.
- **Atividade:** os eventos aparecem como serviço **gerenciador**, tipo `atualizacao`. Por exemplo, "Gerenciador atualizado: abc1234 -> def5678 (…OK)", "Gerenciador reiniciado na versão …" e "Portal atualizado …".
- **Qual versão está rodando:** na pasta do gerenciador,
  ```powershell
  $e = Get-Content gerenciador.estado | ConvertFrom-Json
  "rodando: $($e.versao.Substring(0,7))  pid $($e.pid)  ocupados: $($e.ocupados)"
  "pasta:   $(git rev-parse --short HEAD)"
  ```
  As duas versões devem ser iguais. Uma janela parada que sobra é só a casca do `cmd` do gerenciador anterior.

## 3. Instalação do zero (só se a tarefa agendada não existir)

1. Na pasta do gerenciador: `git pull` e `python -m unittest discover -p "test_*.py"`. Todos os testes devem passar, com `OK`.
2. Ligue o gerenciador (`python gerenciador.py`) e confira que o arquivo `gerenciador.estado` aparece.
3. No PowerShell **como Administrador**, na pasta do gerenciador: `.\scripts\instalar_atualizador.ps1`. Para conferir: `schtasks /Query /TN "Maestro Atualizador"`.
4. O pm2 precisa estar na mesma conta Windows da tarefa (Administrator).

Avisos:
- Não sobrescreva nem apague `CONFIG.json`, `.env`, `banco_usuarios.json`, `banco_ips_bloqueados.json`, a pasta `logs\` ou `gerenciador.instancia`.
- No `.env` de produção, o `URL_SERVIDOR` **não** pode apontar para `localhost` ou `127.0.0.1`, e o `MAESTRO_TESTE` **não** pode estar definido. Nesses casos o gerenciador entra em modo teste e grava em `base_teste\`.

## 4. Configurações (dashboard2 → Configurações, gravadas no `CONFIG.json`)

As chaves abaixo aparecem sozinhas, com o valor padrão:

| Chave | Padrão | O que faz |
|---|---|---|
| `coupa_verificacao_max_paginas` | 60 | Teto de páginas da verificação do Coupa. A chave antiga `verificacao_max_paginas` ainda é aceita. |
| `vale_verificacao_max_paginas` | 60 | Teto de páginas da verificação da Vale. |
| `atualizacao_automatica` | sim | Liga ou desliga o atualizador. |

Os tetos aceitam de 1 a 500. Um valor inválido volta para 60. As duas verificações **param antes do teto** quando todas as pendentes foram encontradas ou quando a lista acaba.

Opcional: `"vale_url_verificacao": "..."`, só se o evento usado para pedir o OTP da Vale mudar.

## 5. Correções de dados (scripts de uma vez só, com o gerenciador parado)

Os dois scripts só **mostram** o que fariam. Para aplicar, rode de novo com `--gravar`, que faz backup ao lado antes de gravar.

1. Corrigir as linhas com "NÃO" de acento quebrado:
   ```
   python scripts\normalizar_respostas.py
   python scripts\normalizar_respostas.py --gravar
   ```
2. Recuperar as cotações que sumiram em 13 e 14/10:
   ```
   python scripts\recuperar_perdidas.py --de 2026-10-13 --ate 2026-10-14
   python scripts\recuperar_perdidas.py --de 2026-10-13 --ate 2026-10-14 --gravar
   ```
   - As **abertas** saem do JSONL, e o robô as extrai de novo no próximo ciclo.
   - As **vencidas** vão para `relatorios\recuperacao_2026-10-13_2026-10-14.csv`, para conferência manual.

## 6. O que testar

| # | Teste | Resultado esperado |
|---|---|---|
| 1 | Ciclo do Coupa (piloto ou "Extrair" + "Verificar") | A Atividade mostra que o ciclo terminou e "Verificação concluída" com encontradas/total. As não encontradas vão para `relatorios\verificacao_coupa_<data>.csv`. Se todas forem encontradas, o CSV do dia é apagado. |
| 2 | Cotação já respondida no Coupa, ainda no prazo | Vira **RESPONDIDO** na próxima verificação. |
| 3 | Responder uma cotação pelo portal | Fica como **RASCUNHO** até alguém enviar no Coupa. Depois disso, a próxima verificação troca para RESPONDIDO. |
| 4 | Responder com um item sem preço que dê erro | A tarefa termina com **erro**, mostrando o item que falhou. |
| 5 | Marcar uma cotação RESPONDIDO à mão e esperar uma verificação | Continua RESPONDIDO. |
| 6 | Verificação da Vale | O OTP é **lido sozinho do e-mail** (`vendas@`), procurado a cada 10 s. Também dá para digitá-lo no painel. |
| 7 | OTP não chega nem é digitado | Depois de 3 min, a Atividade mostra "Verificação da Vale pulada", e o ciclo segue. |
| 8 | "Ligar" com o robô já ligado | Aparece "já está ligado", e nenhum Chrome novo abre. |
| 9 | Clicar duas vezes em "Verificar" | Roda uma vez só. O painel mostra "pedido repetido ignorado". |
| 10 | Pedidos diferentes seguidos (Extrair e depois Verificar) | O segundo espera o primeiro terminar, e os dois rodam no mesmo Chrome, sem atrapalhar um ao outro. |
| 11 | Robô religado | O Chrome anterior daquele robô é fechado antes de abrir outro (perfil em `perfis_chrome\<robô>`). |
| 12 | Página da lista que não carrega na verificação do Coupa | O robô recarrega a página uma vez antes de desistir. |
| 13 | Evento que já aparece como "já terminou" | Vai para o JSONL e não volta mais a cada ciclo, a menos que a data mude (reaberto). |
| 14 | Robô sem extrair por mais de 6 h | O evento "Robô X sem extração concluída há N h" aparece na Atividade, e chega um e-mail, **uma vez só**. |
| 15 | Logs dos robôs | As linhas aparecem como `AAAA-MM-DD HH:MM:SS [MAQUINA] [NIVEL] ...`. Em 10 MB o arquivo roda para `.1`, e o painel preto continua mostrando as linhas. |
| 16 | Portal fora do ar quando o gerenciador abre | O gerenciador tenta conectar a cada 10 s, em vez de fechar. |
| 17 | `python testar_coupa.py` ou `testar_local.bat` | Roda em modo teste (`base_teste\`). A base do `\\SERVIDOR2` não muda. |

## 7. Se algo der errado (voltar)

1. No GitHub, abra a PR que causou o problema e clique em **"Revert"**. Depois faça o merge da PR de reversão.
2. O atualizador volta a versão sozinho em até 5 minutos, quando os robôs estiverem parados.
3. Para segurar as atualizações enquanto investiga, use `atualizacao_automatica = não`.
4. Só se a base estiver errada: os backups da verificação ficam ao lado da base (`COTAÇÕES antes-verificacao <data>.json`), e os do atualizador em `logs\atualizador\backup-*`.

## 8. Outros serviços do servidor

O `C:\webhook\deploy.py` atualiza os outros repositórios (`bot`, `datasheetget`, `apigateway` e `hse-cadastros`). Ele **ignora** o `gerenciador-maestro` e o `portaismaestro`. Ao iniciar, ele não pode matar todos os `node.exe`, porque isso derruba o pm2 e o Site-Maestro.
