import threading
import sys
import logging
# Adiciona o caminho caso coupa_bot esteja na mesma pasta
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from bots.coupa import CoupaScraper
except ImportError:
    from bots.coupa import CoupaScraper

class SioMock:
    """Mock da classe socketio.Client para testar o bot offline e imprimir os eventos no terminal."""
    def emit(self, event, data=None):
        print(f"\n[EVENTO SIO: {event}]")
        if data:
            if 'mensagem' in data:
                print(f" > {data['mensagem']}")
            else:
                print(f" > {data}")

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S')
    
    print("==========================================")
    print("    INICIANDO TESTE LOCAL - COUPA BOT")
    print("==========================================")
    
    sio_mock = SioMock()
    evento_clique_mock = threading.Event()
    dict_coordenadas_mock = {'x': 0, 'y': 0}
    
    bot = CoupaScraper(sio_mock, evento_clique_mock, dict_coordenadas_mock)
    bot.MODO_TESTE = True 
    
    # Redireciona a planilha e banco de dados para os arquivos locais de teste
    bot.ARQUIVO_PLANILHA_CONTROLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "teste.xlsx")
    bot.ARQUIVO_JSONL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "banco_teste.jsonl")
    bot.ARQUIVO_PEDIDOS_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pedidos_teste.json")
    
    print("\n--- INICIANDO LOGIN ---")
    bot.iniciar_e_logar()
    
    while True:
        print("\n==========================================")
        print(" O QUE VOCÊ DESEJA QUE O ROBÔ FAÇA?")
        print(" 1. Extrair eventos do painel (extrair)")
        print(" 2. Responder a um evento (responder)")
        print(" 3. Verificar status (verificar)")
        print(" 4. Listar Pedidos (extrair_pedidos)")
        print(" 5. Sair do modo teste")
        print("==========================================")
        
        escolha = input("Digite o número da opção (1-5): ").strip()
        
        if escolha == '1':
            print("\n--- INICIANDO TAREFA DE EXTRAÇÃO ---")
            bot.iniciar_tarefa("extrair")
            print("--- EXTRAÇÃO FINALIZADA ---")
            
        elif escolha == '2':
            num_evento = input("Digite o número do evento que deseja responder: ").strip()
            # Como é modo teste, monta um dicionário básico para evitar erro na função responder_evento
            # Os arrays de preços/prazos ficariam vazios, ou o usuário poderia adaptar no código depois
            if num_evento:
                dados_mock = {
                    'evento': num_evento,
                    'precos': [],
                    'prazos': [],
                    'origens': [],
                    'icms': []
                }
                print(f"\n--- INICIANDO TAREFA DE RESPOSTA (Evento: {num_evento}) ---")
                bot.iniciar_tarefa("responder", dados_mock)
                print("--- TAREFA DE RESPOSTA FINALIZADA ---")
            else:
                print("Número de evento inválido.")
                
        elif escolha == '3':
            print("\n--- INICIANDO TAREFA DE VERIFICAÇÃO ---")
            bot.iniciar_tarefa("verificar")
            print("--- VERIFICAÇÃO FINALIZADA ---")
            
        elif escolha == '4':
            print("\n--- INICIANDO TAREFA DE EXTRAÇÃO DE PEDIDOS ---")
            bot.iniciar_tarefa("extrair_pedidos")
            print("--- EXTRAÇÃO DE PEDIDOS FINALIZADA ---")
            
        elif escolha == '5':
            print("\nEncerrando teste...")
            break
            
        else:
            print("Opção inválida! Tente novamente.")
            
    print("\n==========================================")
    print("    TESTE LOCAL FINALIZADO")
    print("==========================================")
    
    input("Pressione ENTER para encerrar e fechar o navegador...")
