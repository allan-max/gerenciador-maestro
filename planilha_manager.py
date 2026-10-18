import os
import re
import shutil
import threading
import json
import logging
import tempfile
import time
from datetime import date, datetime

import ambiente

logger = logging.getLogger(__name__)

RESPONDIDO, NAO_RESPONDIDO, RASCUNHO = 'RESPONDIDO', 'NÃO RESPONDIDO', 'RASCUNHO'
HORA_CORTE = (14, 30)        # no dia do vencimento, a partir daqui já conta como vencida
BACKUP_A_PARTIR_DE = 50      # verificação que muda muitos status de uma vez guarda cópia antes


def status_atual(valor):
    """RESPONDIDO, NÃO RESPONDIDO, RASCUNHO ou '' (sem status). Aceita o "NÃƒO" com acento quebrado."""
    texto = str(valor or '').strip().upper()
    if texto in ('RESPONDIDO', 'RESPONDIDA'):
        return RESPONDIDO
    if texto == RASCUNHO:
        return RASCUNHO
    if texto.startswith('N') and texto.endswith(('RESPONDIDO', 'RESPONDIDA')):
        return NAO_RESPONDIDO
    return ''


def data_vencimento(texto):
    m = re.search(r'(\d{1,2})/(\d{1,2})/(\d{4})', str(texto or ''))
    if not m:
        return None
    p1, p2, ano = map(int, m.groups())
    dia, mes = (p2, p1) if p2 > 12 else (p1, p2)   # DD/MM; MM/DD só quando o segundo não cabe em mês
    try:
        return date(ano, mes, dia)
    except ValueError:
        return None


def vencida(vencimento, agora):
    d = data_vencimento(vencimento)
    return d is not None and (d < agora.date() or (d == agora.date() and (agora.hour, agora.minute) >= HORA_CORTE))


def novo_status(atual, respostas, vencimento, agora):
    """Regras D3 (docs/melhorias-bots/plano.md). `respostas`: enviadas segundo o portal; None = não deu para ler.
    RESPONDIDO assim que houver resposta; NÃO RESPONDIDO só depois do vencimento; RESPONDIDO nunca volta."""
    if respostas is None or status_atual(atual) == RESPONDIDO:
        return atual
    if respostas > 0:
        return RESPONDIDO
    if vencida(vencimento, agora):
        return NAO_RESPONDIDO
    return atual


def _quantidade(valor):
    """Compatível com quem ainda manda bool/texto (Vale): True/RESPONDIDO = 1, False/NÃO RESPONDIDO = 0."""
    if valor is None or isinstance(valor, int):
        return int(valor) if isinstance(valor, bool) else valor
    return {RESPONDIDO: 1, NAO_RESPONDIDO: 0}.get(status_atual(valor))


class PlanilhaManager:
    def __init__(self):
        self.lock = threading.RLock()
        self.cotacoes = {"COTAÇÃO": []}
        self.pedidos = {"PEDIDOS": []}
        
        # Mapping column indexes to keys for Cotações
        self.map_cota = {
            1: "COTAÇÃO",
            2: "VENCIMENTO",
            3: "ITEM",
            4: "QUANTIDADE",
            5: "LOCALIDADE",
            6: "VENDEDOR",
            7: "MODELOS",
            8: "MARCAS ",
            9: "RESPOSTA"
        }
        
        dir_db = ambiente.pasta_banco()
        self.caminho_cotacoes = os.path.join(dir_db, "COTAÇÕES.json")
        self.caminho_pedidos = os.path.join(dir_db, "PEDIDOS.json")
        if ambiente.modo_teste():   # a base de teste começa vazia; a de produção nunca é criada nem copiada daqui
            os.makedirs(dir_db, exist_ok=True)
            for caminho, chave in ((self.caminho_cotacoes, "COTAÇÃO"), (self.caminho_pedidos, "PEDIDOS")):
                if not os.path.exists(caminho):
                    with open(caminho, 'w', encoding='utf-8') as f:
                        json.dump({chave: []}, f)

        self.iniciado = False

    def configurar_caminhos(self, dir_db):
        import os
        if not os.path.exists(dir_db):
            try: os.makedirs(dir_db, exist_ok=True)
            except: pass
        self.caminho_cotacoes = os.path.join(dir_db, "COTAÇÕES.json")
        self.caminho_pedidos = os.path.join(dir_db, "PEDIDOS.json")
        self.iniciado = False

    def iniciar(self, caminho_arquivo=None):
        with self.lock:
            self._originais = getattr(self, '_originais', {})
            self._assinaturas = getattr(self, '_assinaturas', {})
            self._carregado = getattr(self, '_carregado', {})
            try:
                for caminho, atributo, chave in (
                    (self.caminho_cotacoes, 'cotacoes', 'COTAÇÃO'),
                    (self.caminho_pedidos, 'pedidos', 'PEDIDOS'),
                ):
                    for tentativa in range(5):   # outro processo pode trocar o arquivo no meio da leitura
                        stat = os.stat(caminho)
                        assinatura = (stat.st_mtime_ns, stat.st_size)
                        if self.iniciado and self._assinaturas.get(caminho) == assinatura:
                            break
                        with open(caminho, 'rb') as f:
                            original = f.read()
                        if (os.stat(caminho).st_mtime_ns, os.stat(caminho).st_size) == assinatura:
                            break
                        time.sleep(1)
                    else:
                        from eventos import registrar
                        registrar('gerenciador', 'base', 'erro', f'Arquivo mudou durante a leitura: {os.path.basename(caminho)}')
                        raise RuntimeError(f'Arquivo mudou durante a leitura: {caminho}')
                    if self.iniciado and self._assinaturas.get(caminho) == assinatura:
                        continue
                    texto = original.decode('utf-8-sig')
                    if chave == 'PEDIDOS':
                        import re
                        texto = re.sub(r',\s*\}', '}', texto)
                        texto = re.sub(r',\s*\]', ']', texto)
                        if not texto.strip().startswith('{'):
                            texto = '{' + texto + '}'
                    banco = json.loads(texto)
                    if not isinstance(banco, dict) or not isinstance(banco.get(chave), list):
                        raise ValueError(f'Estrutura inválida: {caminho}')
                    if any(r is not None and not isinstance(r, dict) for r in banco[chave]):
                        raise ValueError(f'Linha inválida: {caminho}')
                    setattr(self, atributo, banco)
                    self._originais[caminho] = original
                    self._carregado[caminho] = self._serializar(banco)
                    self._assinaturas[caminho] = assinatura
            except Exception:
                self.iniciado = False
                logger.exception('Leitura da base falhou; gravações bloqueadas. Verifique os JSONs e reinicie.')
                raise
            self.iniciado = True

    def _salvar_arquivo(self, caminho, banco):
        ambiente.exigir_trava(caminho)
        if not self.iniciado or caminho not in self._originais:
            raise RuntimeError('Base não carregada; gravação bloqueada.')
        original = self._originais[caminho]
        novo = self._serializar(banco)
        if novo == self._carregado.get(caminho):
            return   # nada mudou na memória: não regrava (nem pisa no que outro serviço gravou)
        temporario = None
        try:
            with tempfile.NamedTemporaryFile(dir=os.path.dirname(caminho), prefix='maestro-', suffix='.tmp', delete=False) as f:
                temporario = f.name
                f.write(novo)
                f.flush()
                os.fsync(f.fileno())
            with open(caminho, 'rb') as f:
                if f.read() != original:
                    raise RuntimeError('Outro serviço alterou a base; gravação cancelada. Tente novamente.')
            os.replace(temporario, caminho)
            self._originais[caminho] = novo
            self._carregado[caminho] = novo
            stat = os.stat(caminho)
            self._assinaturas[caminho] = (stat.st_mtime_ns, stat.st_size)
            logger.info('JSON salvo: %s (%s bytes)', caminho, len(novo))
        finally:
            if temporario and os.path.exists(temporario):
                os.unlink(temporario)

    @staticmethod
    def _serializar(banco):
        return json.dumps(banco, indent=1, ensure_ascii=False).encode('utf-8')

    def salvar(self):
        with self.lock:
            try:
                if "COTAÇÃO" in self.cotacoes:
                    self.cotacoes["COTAÇÃO"] = [x for x in self.cotacoes["COTAÇÃO"] if x is not None]
                if "PEDIDOS" in self.pedidos:
                    self.pedidos["PEDIDOS"] = [x for x in self.pedidos["PEDIDOS"] if x is not None]
                self._salvar_arquivo(self.caminho_cotacoes, self.cotacoes)
                self._salvar_arquivo(self.caminho_pedidos, self.pedidos)
                return True
            except Exception as e:
                self.iniciado = False
                logger.exception('Erro ao salvar JSONs; não continuar com dados em memória.')
                from eventos import registrar
                registrar('gerenciador', 'base', 'erro', f'Falha ao salvar a base; JSON anterior preservado ({e})')
                return False

    def adicionar_linhas(self, aba_nome_aproximado, lista_linhas, com_estilo=False):
        """
        aba_nome_aproximado: ignored, we just append to Cotações
        lista_linhas: list of lists / tuples representing row values
        """
        self.iniciar()
        with self.lock:
            if "COTAÇÃO" not in self.cotacoes:
                self.cotacoes["COTAÇÃO"] = []
                
            for linha in lista_linhas:
                nova_cota = {}
                for idx, val in enumerate(linha):
                    col = idx + 1
                    if col in self.map_cota and val is not None:
                        nova_cota[self.map_cota[col]] = val
                
                # Deixa o status em branco por padrao
                if "RESPOSTA" not in nova_cota:
                    nova_cota["RESPOSTA"] = ""
                    
                self.cotacoes["COTAÇÃO"].append(nova_cota)
                
            return self.salvar()

    def atualizar_coluna_por_chave(self, aba_nome_aproximado, dict_chaves_valores, col_chave, col_alvo):
        self.iniciar()
        with self.lock:
            salvou = False
            lista = self.cotacoes.get("COTAÇÃO", [])
            chave_str = self.map_cota.get(col_chave)
            alvo_str = self.map_cota.get(col_alvo)
            
            if not chave_str or not alvo_str:
                return False
                
            for row in lista:
                val = row.get(chave_str)
                if val is not None:
                    val_s = str(val).strip()
                    if val_s in dict_chaves_valores and row.get(alvo_str) != dict_chaves_valores[val_s]:
                        row[alvo_str] = dict_chaves_valores[val_s]
                        salvou = True
                        
            if salvou:
                return self.salvar()
            return False

    def atualizar_status_respostas(self, aba_nome_aproximado, mapa_cotacao_status, col_chave, col_status, agora=None):
        """mapa: {número: respostas enviadas (int), None se não deu para ler}. Aplica novo_status em todas as
        linhas do número (duplicadas incluídas) e grava uma vez. Devolve as mudanças ('123: RESPONDIDO')."""
        self.iniciar()
        agora = agora or datetime.now()

        with self.lock:
            chave_str = self.map_cota.get(col_chave)
            alvo_str = self.map_cota.get(col_status)
            venc_str = self.map_cota.get(2)
            if not chave_str or not alvo_str:
                return []
            mapa = {str(k).strip(): _quantidade(v) for k, v in mapa_cotacao_status.items()}

            mudadas, transicoes = [], {}
            for row in self.cotacoes.get("COTAÇÃO", []):
                num = str(row.get(chave_str) or '').strip() if isinstance(row, dict) else ''
                if num not in mapa:
                    continue
                antes = row.get(alvo_str)
                depois = novo_status(antes, mapa[num], row.get(venc_str), agora)
                if depois != antes:
                    row[alvo_str] = depois
                    mudadas.append(f'{num}: {depois}')
                    chave = f"{status_atual(antes) or 'vazio'} → {depois}"
                    transicoes[chave] = transicoes.get(chave, 0) + 1

            if not mudadas:
                return []
            from eventos import registrar
            try:
                if len(mudadas) >= BACKUP_A_PARTIR_DE:
                    backup = self.caminho_cotacoes[:-5] + agora.strftime(' antes-verificacao %Y%m%d-%H%M%S.json')
                    shutil.copy2(self.caminho_cotacoes, backup)
                    logger.info('Backup antes de mudar %s status: %s', len(mudadas), backup)
                self._salvar_arquivo(self.caminho_cotacoes, self.cotacoes)
                logger.info('Status de resposta: %s', ', '.join(f'{k}: {v}' for k, v in sorted(transicoes.items())))
                registrar('gerenciador', 'resposta', 'info', f'Status de resposta mudou em {len(mudadas)} cotação(ões)',
                          ref=mudadas[0].split(':')[0], detalhe={'mudancas': mudadas[:20]})
                return mudadas
            except Exception as e:
                self.iniciado = False
                logger.exception('Falha ao salvar respostas; JSON anterior preservado.')
                registrar('gerenciador', 'base', 'erro', f'Falha ao salvar respostas; JSON anterior preservado ({e})')
                raise

    def marcar_rascunho(self, numero):
        """O robô salvou o rascunho no portal (ainda não enviado): RASCUNHO, a não ser que já esteja RESPONDIDO."""
        self.iniciar()
        numero = str(numero).strip()
        with self.lock:
            linhas = [r for r in self.cotacoes.get("COTAÇÃO", []) if isinstance(r, dict)
                      and str(r.get("COTAÇÃO") or '').strip() == numero and status_atual(r.get("RESPOSTA")) != RESPONDIDO]
            if not any(r.get("RESPOSTA") != RASCUNHO for r in linhas):
                return 0
            for r in linhas:
                r["RESPOSTA"] = RASCUNHO
            try:
                self._salvar_arquivo(self.caminho_cotacoes, self.cotacoes)
            except Exception:
                self.iniciado = False   # memória ficou diferente do arquivo: recarrega no próximo uso
                raise
            from eventos import registrar
            registrar('gerenciador', 'resposta', 'info', f'Cotação {numero} com rascunho salvo pelo robô', ref=numero)
            return len(linhas)

    def adicionar_linhas_pedidos(self, aba_nome_aproximado, lista_pedidos):
        self.iniciar()
        with self.lock:
            if "PEDIDOS" not in self.pedidos:
                self.pedidos["PEDIDOS"] = []
                
            for p in lista_pedidos:
                d_agg, pra_agg, em_agg = [], [], []
                pref = len(p['itens']) > 1
                for i, it in enumerate(p['itens']):
                    px = f"(Item {i+1}) " if pref else ""
                    d_agg.append(f"{px}{it['texto']}")
                    pra_agg.append(f"{px}{it['prazo']}")
                    em_agg.append(it['email'])
                
                em_f = sorted(list(set(em_agg)))
                if len(em_f) == 0: em_str = ""
                elif len(em_f) == 1: em_str = em_f[0]
                else: em_str = "\n".join(em_f)
                
                if d_agg:
                    hoje = datetime.now().strftime("%d/%m/%Y")
                    d_agg[-1] = d_agg[-1] + f"\n-ENTREGUE DIA {hoje}"
                
                novo_pedido = {
                    "CIDADE": p.get('cidade_estado', ''),
                    "FRETE": p.get('frete', ''),
                    "PEDIDO": p.get('pedido', ''),
                    "VALOR ": p.get('total', ''),
                    "PRODUTO": "\n\n".join(d_agg),
                    "DATA DE ENTREGA": "\n".join(pra_agg),
                    "NMR DA RFQ": p.get('evento', ''),
                    "REQUISITANTE ": p.get('vendedor', ''),
                    "EMAIL REQUSITAN": em_str
                }
                
                if p.get('caminho_pdf'):
                    novo_pedido['PDF'] = p['caminho_pdf']
                    
                self.pedidos["PEDIDOS"].append(novo_pedido)
                
            return self.salvar()

planilha_manager = PlanilhaManager()
