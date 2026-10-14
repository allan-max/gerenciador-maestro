import os
import threading
import json
import logging
import tempfile
import time
from datetime import datetime

logger = logging.getLogger(__name__)

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
        
        import os
        dir_db = r"\\SERVIDOR2\Publico\ALLAN\database\Banco-de-dados"
        if not os.path.exists(dir_db):
            try: os.makedirs(dir_db, exist_ok=True)
            except: pass
        self.caminho_cotacoes = os.path.join(dir_db, "COTAÇÕES.json")
        self.caminho_pedidos = os.path.join(dir_db, "PEDIDOS.json")
        
        # Mover arquivos existentes se eles estiverem na pasta raiz e não no servidor
        try:
            import shutil
            if os.path.exists("COTAÇÕES.json") and not os.path.exists(self.caminho_cotacoes):
                shutil.copy2("COTAÇÕES.json", self.caminho_cotacoes)
            if os.path.exists("PEDIDOS.json") and not os.path.exists(self.caminho_pedidos):
                shutil.copy2("PEDIDOS.json", self.caminho_pedidos)
        except Exception as e:
            print("Erro ao copiar base para servidor:", e)

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

    def atualizar_status_respostas(self, aba_nome_aproximado, mapa_cotacao_status, col_chave, col_status):
        self.iniciar()
        
        from datetime import datetime
        agora = datetime.now()
        hoje = agora.date()
        passou_das_14h30 = agora.hour > 14 or (agora.hour == 14 and agora.minute >= 30)

        with self.lock:
            salvou = False
            lista = self.cotacoes.get("COTAÇÃO", [])
            chave_str = self.map_cota.get(col_chave)
            alvo_str = self.map_cota.get(col_status)
            venc_str = self.map_cota.get(2)
            
            if not chave_str or not alvo_str:
                return False
                
            import re
            
            for row in lista:
                val = row.get(chave_str)
                if val is not None:
                    val_s = str(val).strip()
                    if val_s in mapa_cotacao_status:
                        # ===== REGRA DE TEMPO =====
                        pode_atualizar = False
                        venc = row.get(venc_str, "")
                        if venc and venc != "-":
                            match = re.search(r'(\d{1,2})/(\d{1,2})/(\d{4})', venc)
                            if match:
                                p1 = int(match.group(1))
                                p2 = int(match.group(2))
                                yr = int(match.group(3))
                                d = p2 if p2 > 12 else p1
                                m = p1 if p2 > 12 else p2
                                if p1 > 12: 
                                    d, m = p1, p2
                                
                                try:
                                    data_venc = datetime(yr, m, d).date()
                                    if data_venc < hoje:
                                        pode_atualizar = True
                                    elif data_venc == hoje and passou_das_14h30:
                                        pode_atualizar = True
                                except: pass
                        
                        if pode_atualizar:
                            status_booleano = mapa_cotacao_status[val_s]
                            if isinstance(status_booleano, bool):
                                novo_status = "RESPONDIDO" if status_booleano else "NÃO RESPONDIDO"
                            else:
                                novo_status = status_booleano
                            if row.get(alvo_str) != novo_status:
                                row[alvo_str] = novo_status
                                salvou = True
            
            if salvou:
                try:
                    self._salvar_arquivo(self.caminho_cotacoes, self.cotacoes)
                    return True
                except Exception:
                    self.iniciado = False
                    logger.exception('Falha ao salvar respostas; JSON anterior preservado.')
                    raise
            return False
                
            for row in lista:
                val = row.get(chave_str)
                if val is not None:
                    val_s = str(val).strip()
                    if val_s in mapa_cotacao_status:
                        status_booleano = mapa_cotacao_status[val_s]
                        if isinstance(status_booleano, bool):
                            row[alvo_str] = "RESPONDIDO" if status_booleano else "NÃO RESPONDIDO"
                        else:
                            row[alvo_str] = status_booleano
                        salvou = True
                        
            if salvou:
                return self.salvar()
            return False

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
