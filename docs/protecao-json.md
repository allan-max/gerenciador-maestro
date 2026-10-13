# Proteção da base JSON

## Evidências do incidente de 13/10/2026

- O JSON incompleto tinha 82.205 bytes, exatamente o prefixo do backup válido de 5.857.848 bytes.
- Depois apareceu um JSON válido de 794 bytes, contendo uma única cotação.
- A cópia anterior à atualização e a versão publicada tinham o mesmo `planilha_manager.py`.
- Foi reproduzida em arquivos temporários a sequência: gravação direta interrompida, leitura inválida ignorada no reinício, base vazia aceita e nova inclusão sobrescrevendo a base.
- O operador informou reinício fechando a janela. Esse procedimento pode interromper a gravação; sem log do momento, não é possível atribuir conclusivamente a primeira interrupção a ele.
- O logger anterior usava somente o console. Fechar a janela perde o histórico, salvo redirecionamento externo.

## Correção

- Gravações passam por temporário no mesmo diretório, flush/fsync e substituição do arquivo completo.
- Leitura de base ausente, inválida ou com estrutura inesperada interrompe a operação; não se aceita uma base vazia como substituta silenciosa.
- Alterações externas detectadas antes da troca cancelam a gravação e exigem recarregar a base.
- Falhas de gravação invalidam o cache. Os comandos de edição e os bots não informam sucesso quando a persistência retorna erro.
- A rotina de respostas também usa a gravação protegida.
- Logs persistentes ficam em `logs/gerenciador.log`, ao lado do `gerenciador.py`, com rotação, data, PID e thread. A configuração ocorre antes da primeira leitura dos JSONs.

## Instalação e teste

1. Manter os processos que escrevem na base parados durante a atualização. Conferir a cópia de segurança e os JSONs recuperados.
2. A instalação ativa identificada pelo diagnóstico é `C:\Users\Administrator\Desktop\gerenciador-maestro`. Atualizar `planilha_manager.py`, `gerenciador.py`, `bots/vale_bot.py` e `bots/coupa/mixins/planilhas.py` da mesma versão nessa instalação.
3. Executar `python -m unittest test_planilha_persistencia.py test_resposta_cotacao.py`.
4. Iniciar uma única instância do gerenciador. Conferir `logs/gerenciador.log` e a quantidade de cotações no portal antes de religar os demais serviços.
5. Se o gerenciador não iniciar por erro de JSON ou permissão, corrigir a base/permissão indicada no log; não criar JSON vazio para contornar o erro.
6. Preferir Ctrl+C e aguardar o encerramento ao reiniciar. O teste de término forçado valida a proteção antes da substituição em arquivos temporários locais.

## Auditoria do servidor

O relatório de 13/10/2026 às 16h45 confirmou uma instância do gerenciador no caminho acima, o portal `Site-Maestro` online no PM2 e o serviço de NF em execução. Os JSONs de cotações e pedidos estavam válidos. A versão em execução ainda tinha os dois pontos de gravação direta em `planilha_manager.py`.

`VenturaLogger` estava falhando repetidamente no NSSM e apontava para `C:\Users\Administrator\Desktop\site\logger_service.py`; esse arquivo não apareceu no inventário da pasta. Isso sugere configuração desatualizada, sem comprovar relação com a perda de cotações. Os eventos coletados não identificaram o processo que interrompeu a primeira gravação.

Os 14 testes passaram localmente e também usando arquivos descartáveis no compartilhamento SMB. Nenhum JSON de produção foi alterado pelos testes.

## Limites

Outros serviços que gravam diretamente no mesmo JSON ainda podem danificá-lo. A verificação anterior à troca não é um bloqueio distribuído e não elimina todas as corridas entre processos. Cotações e pedidos são arquivos independentes, sem transação conjunta. Não há teste de desligamento do servidor, queda de energia ou perda do SMB no momento exato da substituição.
