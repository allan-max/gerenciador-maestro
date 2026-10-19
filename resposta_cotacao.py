"""Atualiza somente a resposta de uma linha identificada, sem escrever no Excel."""
import json
import os
import tempfile
from pathlib import Path

CAMPOS = ('COTAÇÃO', 'VENDEDOR', 'LOCALIDADE', 'ITEM', 'VENCIMENTO')


def marcar_respondida(manager, identidade):
    if not isinstance(identidade, dict) or not all(isinstance(identidade.get(c), str) for c in CAMPOS):
        raise ValueError('Identificação da cotação inválida.')
    if not identidade['COTAÇÃO'].strip() or not identidade['VENDEDOR'].strip():
        raise ValueError('Cotação e vendedor são obrigatórios.')
    caminho = Path(manager.caminho_cotacoes)
    import ambiente
    ambiente.exigir_trava(caminho)
    with manager.lock:
        original = caminho.read_bytes()
        banco = json.loads(original)
        linhas = banco['COTAÇÃO']
        encontradas = [r for r in linhas if isinstance(r, dict) and all(
            str(r.get(c) or '').strip() == identidade[c].strip() for c in CAMPOS)]
        if len(encontradas) != 1:
            raise ValueError('Cotação ausente, alterada ou duplicada. Atualize a tela antes de salvar.')
        encontradas[0]['RESPOSTA'] = 'RESPONDIDO'
        temporario = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=caminho.parent, delete=False) as f:
                temporario = f.name
                json.dump(banco, f, ensure_ascii=False, indent=1)
                f.flush()
                os.fsync(f.fileno())
            if caminho.read_bytes() != original:
                raise ValueError('O JSON foi alterado por outro serviço. Atualize a tela e tente novamente.')
            os.replace(temporario, caminho)
            manager.cotacoes = banco
            manager.ultima_mod_c = caminho.stat().st_mtime
            from eventos import registrar
            registrar('gerenciador', 'resposta', 'ok', f"Cotação {identidade['COTAÇÃO'].strip()} marcada como respondida no portal",
                      ref=identidade['COTAÇÃO'].strip(), detalhe={'vendedor': identidade['VENDEDOR'].strip()})
        finally:
            if temporario and os.path.exists(temporario):
                os.unlink(temporario)
