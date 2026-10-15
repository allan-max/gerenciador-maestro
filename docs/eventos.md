# Eventos para o dashboard de Atividade

O gerenciador grava o que aconteceu em `logs/eventos.jsonl`, na pasta dele. O
portal pede esses eventos com `comando_eventos` e junta com os do serviço de
NF numa linha do tempo só.

## Formato

Uma linha JSON por evento:

```json
{"em": "2026-10-15T13:37:02-03:00", "servico": "vale", "tipo": "cotacao_nova",
 "nivel": "ok", "resumo": "3 cotação(ões) nova(s) da Vale", "ref": "4513664056",
 "detalhe": {"cotacoes": ["4513664056", "..."]}}
```

- `servico`: `gerenciador`, `coupa` ou `vale`.
- `nivel`: `ok`, `info`, `aviso` ou `erro`.
- `resumo`: até 140 caracteres.
- `detalhe`: no máximo 20 itens.

## O que vira evento

| Onde | Evento |
|---|---|
| `bots/vale_bot.py` e `bots/coupa/mixins/planilhas.py` | cotação nova incluída pelo robô (`cotacao_nova`) |
| `gerenciador.py` (`comando_registrar_cotacao_manual`) | cotação cadastrada à mão |
| `planilha_manager.atualizar_status_respostas` | status de resposta que **mudou de verdade** (só quando grava) |
| `resposta_cotacao.marcar_respondida` | cotação marcada como respondida no portal |
| `loop_automacao` | início, fim e erro do ciclo do Coupa e da Vale |
| trava de instância única | segundo gerenciador recusado (`aviso`) |
| `planilha_manager` | "Falha ao salvar … JSON anterior preservado" e "Arquivo mudou durante a leitura" (`erro`) |

## Regras

- Só o gerenciador de verdade grava. `eventos.ativar()` roda no
  `if __name__ == '__main__'`. Quem só importa (os testes, que rodam na pasta de
  produção) não grava nada.
- A gravação acrescenta uma linha (append + flush). **A falha ao gravar um
  evento nunca derruba a operação**: vira só um aviso no `gerenciador.log`.
- A limpeza (mais de 7 dias) roda na partida e todo dia às 03:00, com
  temporário + `os.replace`.
- Exceção consciente: o gerenciador recusado pela trava acrescenta **uma**
  linha no arquivo do que está rodando. Ele nunca limpa nem regrava o arquivo.
- `comando_eventos` → `retorno_eventos` (`{sucesso, resultado: {eventos, total,
  erros}, clientId}`) aceita `limite` (até 500) e `desde`. O portal só aceita
  a resposta do socket do robô, autenticado por `ROBO_SECRET`.

## Teste

```
python -m unittest test_eventos test_planilha_persistencia test_resposta_cotacao test_instancia_unica test_autenticacao_robo
```
