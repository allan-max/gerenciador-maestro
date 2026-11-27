# -*- coding: utf-8 -*-
"""Resposta de cotação: o código é o do Coupa (responde Vale e Vale Base Metals);
aqui ficam só os métodos que a Vale troca."""
import logging
import os
import re
import base64
from bots.vale.mixins.resposta import RespostaMixin as RespostaCoupaMixin

logger = logging.getLogger("ValeApp")


class RespostaMixin(RespostaCoupaMixin):
    # 5. RESPONDER EVENTO: herdado de RespostaMixin (bots/vale/mixins/resposta.py)
    # Código único que responde tanto Vale quanto Vale Base Metals.

    @staticmethod
    def _gravar_anexo(pasta, f_data):
        # basename: o nome vem do portal e não pode apontar para fora da pasta temporária
        nome = os.path.basename(str(f_data.get('name') or '').replace('\\', '/'))
        nome = re.sub(r'[<>:"|?*\x00-\x1f]', '_', nome).strip(' .')   # caracteres que o Windows recusa
        caminho = os.path.join(pasta, nome or 'anexo.pdf')
        n = 1
        while os.path.exists(caminho):   # datasheet e DAV com o mesmo nome não se sobrescrevem
            caminho = os.path.join(pasta, f"{n}_{nome or 'anexo.pdf'}")
            n += 1
        with open(caminho, 'wb') as out:
            out.write(base64.b64decode(f_data['data']))
        return caminho

    def _marcar_rascunho(self, evento):
        try:
            from nucleo.planilha_manager import planilha_manager
            if planilha_manager.marcar_rascunho(evento):
                logger.info(f"   📝 Cotação {evento} marcada como RASCUNHO no banco.")
        except Exception:
            logger.exception(f"Rascunho salvo no portal, mas não deu para marcar {evento} como RASCUNHO no banco")
