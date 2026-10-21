import time

import psutil

try:
    import pynvml
    pynvml.nvmlInit()
    HAS_GPU = True
except Exception as e:
    print(f"GPU não detectada ou erro no pynvml: {e}")
    HAS_GPU = False

GB = 1024 ** 3


def coletar_metricas():
    """Lê CPU, RAM, GPU e VRAM da máquina. Usado pelo gerenciador (painel do site) e pela API abaixo."""
    cpu = psutil.cpu_percent(interval=0.5)
    memoria = psutil.virtual_memory()

    dados = {
        "cpu": round(cpu, 1),
        "ram": round(memoria.percent, 1),
        "ram_usada_gb": round(memoria.used / GB, 2),
        "ram_total_gb": round(memoria.total / GB, 2),
        "gpu": 0,
        "vram": 0,
        "vram_usada_gb": 0,
        "vram_total_gb": 0,
        "gpu_nome": None,
        "gpu_temp": None,
        "gpu_disponivel": HAS_GPU,
    }

    if HAS_GPU:
        try:
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)

            utilizacao = pynvml.nvmlDeviceGetUtilizationRates(handle)
            dados["gpu"] = round(utilizacao.gpu, 1)

            info_memoria = pynvml.nvmlDeviceGetMemoryInfo(handle)
            dados["vram"] = round((info_memoria.used / info_memoria.total) * 100, 1)
            dados["vram_usada_gb"] = round(info_memoria.used / GB, 2)
            dados["vram_total_gb"] = round(info_memoria.total / GB, 2)

            nome = pynvml.nvmlDeviceGetName(handle)
            dados["gpu_nome"] = nome.decode() if isinstance(nome, bytes) else str(nome)
            try:
                dados["gpu_temp"] = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
            except Exception:
                pass
        except Exception as e:
            print(f"Erro ao ler os dados da GPU: {e}")

    return dados


def criar_app():
    """API HTTP opcional (python winserver.py). O painel do site NÃO depende dela: o gerenciador chama coletar_metricas()."""
    from flask import Flask, jsonify
    from flask_cors import CORS

    app = Flask(__name__)
    CORS(app)

    @app.route('/api/metricas')
    def obter_metricas():
        return jsonify(coletar_metricas())

    return app


if __name__ == '__main__':
    psutil.cpu_percent(interval=None)
    criar_app().run(host='0.0.0.0', port=5000)
