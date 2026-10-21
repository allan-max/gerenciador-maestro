import platform
import subprocess
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

_cache = {}
_rede_anterior = None


def _cacheado(chave, ttl, funcao):
    """Evita refazer leituras pesadas (discos, sensores, processos) a cada amostra de 5 s."""
    agora = time.time()
    item = _cache.get(chave)
    if item and agora - item[0] < ttl:
        return item[1]
    try:
        valor = funcao()
    except Exception:
        valor = item[1] if item else None
    _cache[chave] = (agora, valor)
    return valor


def _temperaturas_cpu():
    """Temperatura da CPU (°C). Tenta psutil (Linux), LibreHardwareMonitor/OpenHardwareMonitor (WMI) e a zona térmica ACPI.
    No Windows o mais confiável é deixar o LibreHardwareMonitor aberto (ele expõe os sensores via WMI)."""
    if hasattr(psutil, 'sensors_temperatures'):
        try:
            for leituras in psutil.sensors_temperatures().values():
                valores = [l.current for l in leituras if l.current]
                if valores:
                    return round(max(valores), 1), 'psutil'
        except Exception:
            pass
    consultas = [
        ('LibreHardwareMonitor', "Get-CimInstance -Namespace root/LibreHardwareMonitor -ClassName Sensor | Where-Object { $_.SensorType -eq 'Temperature' -and $_.Name -match 'CPU' } | Select-Object -ExpandProperty Value"),
        ('OpenHardwareMonitor', "Get-CimInstance -Namespace root/OpenHardwareMonitor -ClassName Sensor | Where-Object { $_.SensorType -eq 'Temperature' -and $_.Name -match 'CPU' } | Select-Object -ExpandProperty Value"),
        ('ACPI', "Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature | ForEach-Object { [math]::Round($_.CurrentTemperature / 10 - 273.15, 1) }"),
    ]
    for origem, comando in consultas:
        try:
            saida = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', comando],
                                   capture_output=True, text=True, timeout=8,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout
            valores = [float(v.replace(',', '.')) for v in saida.split() if v.strip()]
            valores = [v for v in valores if 0 < v < 120]
            if valores:
                return round(max(valores), 1), origem
        except Exception:
            continue
    return None, None


def _discos():
    lista = []
    for part in psutil.disk_partitions(all=False):
        if 'cdrom' in part.opts or not part.fstype:
            continue
        try:
            uso = psutil.disk_usage(part.mountpoint)
        except Exception:
            continue
        lista.append({'ponto': part.mountpoint, 'percentual': round(uso.percent, 1),
                      'usado_gb': round(uso.used / GB, 1), 'total_gb': round(uso.total / GB, 1)})
    return lista


def _processos_top():
    amostra = []
    for p in psutil.process_iter(['name', 'memory_info']):
        try:
            if p.info['name'] == 'System Idle Process':
                continue
            p.cpu_percent(interval=None)
            amostra.append(p)
        except Exception:
            continue
    time.sleep(0.3)
    itens = []
    for p in amostra:
        try:
            itens.append({'nome': p.info['name'] or '?',
                          'cpu': round(p.cpu_percent(interval=None) / (psutil.cpu_count() or 1), 1),
                          'ram_mb': round(p.info['memory_info'].rss / (1024 ** 2))})
        except Exception:
            continue
    return {'cpu': sorted(itens, key=lambda i: i['cpu'], reverse=True)[:5],
            'ram': sorted(itens, key=lambda i: i['ram_mb'], reverse=True)[:5]}


def _rede():
    """Velocidade de rede (KB/s) desde a leitura anterior."""
    global _rede_anterior
    io = psutil.net_io_counters()
    agora = time.time()
    resultado = {'env_kbs': 0, 'rec_kbs': 0}
    if _rede_anterior and agora > _rede_anterior[0]:
        dt = agora - _rede_anterior[0]
        resultado = {'env_kbs': round((io.bytes_sent - _rede_anterior[1]) / dt / 1024, 1),
                     'rec_kbs': round((io.bytes_recv - _rede_anterior[2]) / dt / 1024, 1)}
    _rede_anterior = (agora, io.bytes_sent, io.bytes_recv)
    return resultado


def _diagnostico(d):
    """Lista de avisos {nivel, texto} com base nas leituras. nivel: critico | aviso."""
    avisos = []

    def add(nivel, texto):
        avisos.append({'nivel': nivel, 'texto': texto})

    if d['cpu'] >= 90: add('critico', f"CPU em {d['cpu']}%")
    elif d['cpu'] >= 75: add('aviso', f"CPU em {d['cpu']}%")
    if d['ram'] >= 90: add('critico', f"RAM em {d['ram']}%")
    elif d['ram'] >= 80: add('aviso', f"RAM em {d['ram']}%")
    if d.get('cpu_temp') is not None:
        if d['cpu_temp'] >= 90: add('critico', f"CPU a {d['cpu_temp']}°C")
        elif d['cpu_temp'] >= 80: add('aviso', f"CPU a {d['cpu_temp']}°C")
    if d.get('gpu_temp') is not None:
        if d['gpu_temp'] >= 85: add('critico', f"GPU a {d['gpu_temp']}°C")
        elif d['gpu_temp'] >= 75: add('aviso', f"GPU a {d['gpu_temp']}°C")
    if d.get('swap_percentual', 0) >= 80: add('aviso', f"Memória virtual em {d['swap_percentual']}%")
    for disco in d.get('discos') or []:
        if disco['percentual'] >= 95: add('critico', f"Disco {disco['ponto']} com {disco['percentual']}% usado")
        elif disco['percentual'] >= 85: add('aviso', f"Disco {disco['ponto']} com {disco['percentual']}% usado")
    return avisos


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

    temp, origem = _cacheado('temp_cpu', 20, _temperaturas_cpu) or (None, None)
    swap = psutil.swap_memory()
    freq = psutil.cpu_freq()
    dados.update({
        "cpu_temp": temp,
        "cpu_temp_origem": origem,
        "cpu_nucleos": psutil.cpu_count(logical=False),
        "cpu_threads": psutil.cpu_count(logical=True),
        "cpu_freq_mhz": round(freq.current) if freq else None,
        "swap_percentual": round(swap.percent, 1),
        "swap_usada_gb": round(swap.used / GB, 2),
        "swap_total_gb": round(swap.total / GB, 2),
        "discos": _cacheado('discos', 30, _discos) or [],
        "rede": _rede(),
        "processos": _cacheado('processos', 15, _processos_top) or {"cpu": [], "ram": []},
        "uptime_s": int(time.time() - psutil.boot_time()),
        "sistema": f"{platform.system()} {platform.release()}",
        "host": platform.node(),
    })

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

    dados["diagnostico"] = _diagnostico(dados)
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
