# Pedidos novos do Coupa por e-mail

## Por quê
Saber quais pedidos da Vale apareceram no portal Coupa e ainda não estão na planilha,
sem o robô mexer na planilha e sem repetir pedidos já avisados.

## Objetivo
- Modo `extrair_pedidos` varre o portal (Vale e Vale Base Metals), como hoje.
- Avisa por e-mail `vendas7@venturainformatica.com.br` só os pedidos novos desta execução.

## Fora do escopo / proibido
- Não grava no `PEDIDOS.json` (planilha) nem no `database/pedidos.json` antigo.
- Não baixa PDF da impressão do pedido.

## Desenho
Arquivos em `Banco-de-dados\` (mesma pasta do `PEDIDOS.json`):
- `PEDIDOS-LIDOS-COUPA.json`: todo pedido que o robô já leu no portal, com detalhes
  (cidade, frete, total, evento, vendedor, itens, alertas). Só cresce.
- `PEDIDOS-FORA-PLANILHA.json`: lidos que não estão no `PEDIDOS.json`, recalculado a cada execução.

Fluxo:
1. Carrega números do `PEDIDOS-LIDOS-COUPA.json` e do `PEDIDOS.json` (campo `PEDIDO`). Só leitura.
2. Varre o portal; pula pedido presente em qualquer um dos dois. Abre só os novos e extrai os detalhes
   como hoje, sem o bloco de PDF.
3. Cada novo vai para `PEDIDOS-LIDOS-COUPA.json`.
4. No fim, recalcula `PEDIDOS-FORA-PLANILHA.json`.
5. Se houve pedido novo, envia um e-mail (SMTP de `bots/relatorio_email.py`) com nº, RFQ, cidade,
   frete, valor, vendedor, itens e alertas. Sem pedido novo, não envia.

## Botão no portal
Hoje o modo `extrair_pedidos` existe no robô, mas nada o aciona.
- `portaismaestro/public/coupa.html`: botão **Pedidos Novos** no grid de Extrair/Verificar. Ele pede confirmação
  e emite `comando_direto {modo: 'extrair_pedidos', portal: 'coupa'}`. Enquanto roda, vira **PARAR PEDIDOS**
  (`solicitar_parada`, já suportado). Volta ao normal no `tarefa_concluida` de `'Listagem de Pedidos'`, mostrando
  quantos pedidos novos achou.
- `portaismaestro/server.js`: aceita `extrair_pedidos` em `MODOS_AUDITADOS`; estado global `extraindo_pedidos`
  trava os outros botões enquanto roda.
- `gerenciador/gerenciador.py`: inclui `extrair_pedidos` na lista de tarefas normais (`extrair`, `verificar`,
  `responder`), na mesma fila de uma tarefa por vez. O robô precisa estar ligado, como no Extrair.

## Abordagens
- Escolhida: arquivo próprio de lidos + leitura da planilha, e-mail só dos novos.
- Descartadas: e-mail cumulativo de todos fora da planilha (repetitivo); usar `pedidos.json` antigo
  (mistura com o que já foi gravado na planilha).

## Decisões do construtor
- Nomes exatos dos arquivos e layout HTML do e-mail.
- Destinatário configurável por `config`, padrão `vendas7@venturainformatica.com.br`.
