# -*- coding: utf-8 -*-
"""IA (Groq/Gemini) para marca e modelo, histórico de vendas e vendedor sugerido."""
import logging
import os
import re
import json
from datetime import datetime
from google import genai
from groq import Groq

try:
    from bots.itens.recomendacao_vendedor import selecionar_alvos_comparacao_item
except ImportError:
    from recomendacao_vendedor import selecionar_alvos_comparacao_item

logger = logging.getLogger("ValeApp")


class InteligenciaMixin:
    def extrair_dados_groq(self, texto_item):
        """Motor Reserva: Usa a Groq (Llama 3) quando o Gemini atinge o limite."""
        GROQ_API_KEY = os.environ.get("GROQ_API_KEY_VALE") 
        try:
            client = Groq(api_key=GROQ_API_KEY)
            prompt = f"""
            Você é um assistente especialista em compras corporativas e automação de suprimentos.
            Sua tarefa é analisar a descrição do item abaixo e extrair 3 informações exatas:
            1. Marca (Fabricante)
            2. Modelo
            3. Part Number (PN / Referência)

            Regras:
            - Retorne ESTRITAMENTE no formato JSON.
            - Se não encontrar a informação, deixe o valor como uma string vazia "".
            - Não invente dados. Use apenas o que está no texto.
            
            Formato de Saída:
            {{"marca": "NOME_DA_MARCA", "modelo": "NOME_DO_MODELO", "part_number": "CODIGO_PN"}}
            
            Texto para analisar:
            {texto_item}
            """
            response = client.chat.completions.create(
                messages=[
                    {"role": "system", "content": "Você é uma API que retorna apenas JSON válido."},
                    {"role": "user", "content": prompt}
                ],
                model="openai/gpt-oss-120b",
                temperature=0.0,
                response_format={"type": "json_object"}
            )
            dados = json.loads(response.choices[0].message.content)
            return {
                "marca": str(dados.get("marca", "")).strip().upper(),
                "modelo": str(dados.get("modelo", "")).strip().upper(),
                "part_number": str(dados.get("part_number", "")).strip().upper()
            }
        except Exception as e:
            logger.warning(f"   ⚠️ Falha na extração reserva com GROQ: {e}")
            return {"marca": "", "modelo": "", "part_number": ""}

    def carregar_historico(self):
        if hasattr(self, 'historico_vendedores_cache') and self.historico_vendedores_cache is not None:
            return self.historico_vendedores_cache
        
        self.historico_vendedores_cache = []
        json_path = getattr(self, 'PASTA_DATABASE', getattr(self, 'config', {}).get('pasta_database', r'\\SERVIDOR2\Publico\ALLAN\database\Banco-de-dados'))
        
        if isinstance(json_path, dict) or not isinstance(json_path, str):
            json_path = r'\\SERVIDOR2\Publico\ALLAN\database\Banco-de-dados'
            
        if not json_path.endswith('Banco-de-dados'):
            json_path = os.path.join(json_path, 'Banco-de-dados')
        json_file = os.path.join(json_path, 'COTAÇÕES.json')
        
        if not os.path.exists(json_file):
            logger.warning(f"   ⚠️ Arquivo COTAÇÕES.json não encontrado em {json_file}")
            return []
            
        try:
            import json
            with open(json_file, 'r', encoding='utf-8') as f:
                dados_completos = json.load(f)
                
            if 'COTAÇÃO' in dados_completos:
                registros = dados_completos['COTAÇÃO']
            else:
                # Caso a estrutura mude ou seja um array direto
                registros = dados_completos if isinstance(dados_completos, list) else []
                
            for row in registros:
                # O JSON possui as chaves: COTAÇÃO, VENCIMENTO, ITEM, QUANTIDADE, LOCALIDADE, VENDEDOR, MODELOS, MARCAS, RESPOSTA
                # A chave 'MARCAS ' às vezes tem espaço no final
                
                data = str(row.get('COTAÇÃO', '')) + " " + str(row.get('VENCIMENTO', ''))
                desc = str(row.get('ITEM', '')).strip()
                vend = str(row.get('VENDEDOR', '')).strip()
                mod = str(row.get('MODELOS', '')).strip().upper()
                marc = str(row.get('MARCAS ', row.get('MARCAS', ''))).strip().upper()
                resposta = str(row.get('RESPOSTA', '')).strip().upper()
                
                if vend and desc and vend.lower() not in ["none", "", "nan"] and resposta == "RESPONDIDO":
                    self.historico_vendedores_cache.append({
                        'data': data.strip(),
                        'descricao': desc,
                        'vendedor': vend,
                        'modelo': mod,
                        'marca': marc
                    })
            logger.info(f"   ✅ Carregados {len(self.historico_vendedores_cache)} registros históricos do COTAÇÕES.json com vendedor atribuído.")
        except Exception as e:
            logger.error(f"   ❌ Erro ao carregar histórico do COTAÇÕES.json: {e}")
        
        return self.historico_vendedores_cache

    def encontrar_vendedor_similar(self, item_dict, dados_ia):
        historico = self.carregar_historico()
        if not historico: return None
        
        import difflib
        modelo_atual = dados_ia.get("modelo", "").strip().upper()
        marca_atual = dados_ia.get("marca", "").strip().upper()
        part_number = dados_ia.get("part_number", "").strip().upper()
        
        if not modelo_atual and part_number:
            modelo_atual = part_number
            
        texto_item_list = []
        descricao_list = []
        texto_compra_list = []
        
        if isinstance(item_dict, dict):
            if item_dict.get('texto_item') and str(item_dict['texto_item']).strip():
                texto_item_list.append(str(item_dict['texto_item']).lower())
            if item_dict.get('descricao') and str(item_dict['descricao']).strip():
                descricao_list.append(str(item_dict['descricao']).lower())
            if item_dict.get('texto_compra') and str(item_dict['texto_compra']).strip():
                texto_compra_list.append(str(item_dict['texto_compra']).lower())
        else:
            texto_item_list.append(str(item_dict).lower())
        
        logger.info(f"   ?? Buscando vendedor para Modelo: '{modelo_atual}' | Marca: '{marca_atual}'")
        
        # ETAPA 1: Modelo e Marca
        for row in reversed(historico):
            row_mod = row['modelo'].strip().upper()
            row_marc = row['marca'].strip().upper()
            
            if modelo_atual and len(modelo_atual) > 2 and row_mod and modelo_atual in row_mod:
                logger.info(f"   ? Vendedor encontrado! [Modelo exato] -> {row['vendedor']}")
                return f"Ultimo Vendedor: {row['vendedor']} ({row['data']}) - Motivo: Modelo exato"
            
            elif marca_atual and len(marca_atual) > 2 and row_marc and marca_atual in row_marc:
                logger.info(f"   ? Vendedor encontrado! [Marca exata] -> {row['vendedor']}")
                return f"Ultimo Vendedor: {row['vendedor']} ({row['data']}) - Motivo: Marca exata"

        def buscar_por_similaridade(lista_alvos, nome_etapa):
            if not lista_alvos: return None
            for row in reversed(historico):
                desc_lines = row['descricao'].split('\n')
                for line in desc_lines:
                    line_clean = re.sub(r'^\(Item \d+\)\s*', '', line).strip().lower()
                    if not line_clean or len(line_clean) < 5: continue
                    
                    for desc_alvo in lista_alvos:
                        t_maior, t_menor = (desc_alvo, line_clean) if len(desc_alvo) > len(line_clean) else (line_clean, desc_alvo)
                        
                        if len(t_maior) == 0 or (len(t_menor) / len(t_maior)) < 0.8:
                            continue
                            
                        matcher = difflib.SequenceMatcher(None, t_maior, t_menor)
                        if matcher.real_quick_ratio() < 0.85: continue
                        if matcher.quick_ratio() < 0.85: continue
                        
                        ratio = matcher.ratio()
                        if ratio >= 0.85:
                            logger.info(f"   ? Vendedor encontrado! [Similaridade alta em {nome_etapa} ({ratio*100:.1f}%)] -> {row['vendedor']}")
                            return f"Ultimo Vendedor: {row['vendedor']} ({row['data']}) - Motivo: Similaridade em {nome_etapa} ({ratio*100:.1f}%)"
            return None

        # ETAPA 2: Comparação de Itens com as regras de prioridade:
        # 1. Prioridade principal: ITEM TEXT (texto do item).
        # 2. Exceção DESCRIÇÃO: apenas se NÃO houver ITEM TEXT no evento.
        # 3. Exceção Marca/Modelo: se marca e modelo foram encontrados na DESCRIÇÃO na busca prévia, a DESCRIÇÃO tem prioridade.
        # Regra Crítica: Em todos os outros casos, comparação baseada EXCLUSIVAMENTE no ITEM TEXT.
        alvos_rotulados = selecionar_alvos_comparacao_item(item_dict, dados_ia)
        for desc_alvo, rotulo in alvos_rotulados:
            res = buscar_por_similaridade([desc_alvo], rotulo)
            if res:
                return res
        
        logger.info(f"   ? Nenhum vendedor similar encontrado no historico para este item.")
        return None

    def extrair_dados_gemini(self, texto_item):
        """Pipeline em 3 níveis: 1º Banco de Dados -> 2º Regex -> 3º Claude Structured Outputs (com fallback Gemini/Groq)."""
        try:
            from bots.itens.extrator_item import extrair_dados_item_hibrido
        except ImportError:
            from extrator_item import extrair_dados_item_hibrido
        pasta_db = getattr(self, 'PASTA_DATABASE', getattr(self, 'config', {}).get('pasta_database', None))
        if not isinstance(pasta_db, str):
            pasta_db = None
        return extrair_dados_item_hibrido(
            texto_item,
            caminho_json=pasta_db,
            fallback_ia_fn=self._extrair_dados_gemini_legado
        )

    def _extrair_dados_gemini_legado(self, texto_item):
        """Motor Principal: Usa o Gemini. Se der limite (429), joga pra Groq no resto do dia."""
        hoje = datetime.now().date()
        
        # 1. Verifica se já falhou hoje. Se sim, desvia direto para a Groq
        if self.usar_groq_hoje and self.dia_falha_gemini == hoje:
            return self.extrair_dados_groq(texto_item)
        elif self.dia_falha_gemini and self.dia_falha_gemini != hoje:
            # Virou o dia! Dá uma nova chance ao Gemini
            self.usar_groq_hoje = False
            self.dia_falha_gemini = None

        GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY") 
        client = genai.Client(
            api_key=GOOGLE_API_KEY,
            http_options={'retry_options': {'attempts': 1}}
        )
        
        try:
            prompt = f"""
            Você é um assistente especialista em compras corporativas e automação de suprimentos.
            Sua tarefa é analisar a descrição do item abaixo e extrair 3 informações exatas:
            1. Marca (Fabricante)
            2. Modelo
            3. Part Number (PN / Referência)

            Regras:
            - Retorne ESTRITAMENTE no formato JSON.
            - Se não encontrar a informação, deixe o valor como uma string vazia "".
            
            Formato de Saída:
            {{"marca": "NOME_DA_MARCA", "modelo": "NOME_DO_MODELO", "part_number": "CODIGO_PN"}}
            
            Texto para analisar:
            {texto_item}
            """
            response = client.models.generate_content(
                model="gemini-2.5-flash", 
                contents=prompt,
                config={
                    "temperature": 0.0,
                    "response_mime_type": "application/json",
                    "automatic_function_calling": {"disable": True}
                }
            )
            dados = json.loads(response.text)
            return {
                "marca": str(dados.get("marca", "")).strip().upper(),
                "modelo": str(dados.get("modelo", "")).strip().upper(),
                "part_number": str(dados.get("part_number", "")).strip().upper()
            }
            
        except Exception as e:
            erro_str = str(e).upper()
            # 2. O DESVIO: Se der o erro 429 ou RESOURCE_EXHAUSTED, ativa a Groq
            if "429" in erro_str or "RESOURCE_EXHAUSTED" in erro_str:
                logger.warning("   🔄 Limite diário do Gemini atingido (429). Alternando para GROQ pelo resto do dia!")
                self.usar_groq_hoje = True
                self.dia_falha_gemini = hoje
                return self.extrair_dados_groq(texto_item)
            else:
                logger.warning(f"   ⚠️ Falha na extração com Gemini: {e}")
                return {"marca": "", "modelo": "", "part_number": ""}
