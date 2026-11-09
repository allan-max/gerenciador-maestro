"""Lê todos os eventos do painel do Coupa (número + vencimento) sem abrir nenhum evento e acrescenta no
eventos.jsonl os que ainda não estão lá.

Uso: python scripts/listar_eventos_coupa.py
     python scripts/listar_eventos_coupa.py --banco "C:\\outro\\eventos.jsonl"
     python scripts/listar_eventos_coupa.py --sim          (grava sem perguntar)

- Abre um Chrome com perfil próprio (perfis_chrome/extrator_eventos): não mexe no Chrome do robô.
- Na primeira vez, faça o login no Coupa na janela que abrir e aperte ENTER no console.
  O perfil guarda a sessão, então nas próximas vezes o login costuma já estar feito.
- Só lê a tabela de cada página (?page=1, 2, 3...). Não entra nos eventos.
- Mostra os que faltam no banco e pergunta antes de gravar. Só acrescenta linhas no fim, no formato do robô
  (respondido false); nada é apagado. Antes, copia o banco para eventos.jsonl.antes-listar-<data_hora>.
- ATENÇÃO: o robô pula todo evento que está no eventos.jsonl. Evento gravado aqui não é extraído pelo robô.
"""
import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime

from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, StaleElementReferenceException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGIN_URL = "https://supplier.coupahost.com/sessions/new"
EVENTS_URL = "https://vale.coupahost.com/quote_supplier_land"
PERFIL = os.path.join(RAIZ, 'perfis_chrome', 'extrator_eventos')
BANCO_PADRAO = r"\\SERVIDOR2\Publico\ALLAN\database\eventos.jsonl"


def abrir_chrome():
    ops = Options()
    ops.add_argument(f"--user-data-dir={PERFIL}")
    ops.add_argument("--start-maximized")
    ops.add_argument("--no-sandbox")
    ops.add_argument("--disable-dev-shm-usage")
    driver = webdriver.Chrome(options=ops)
    driver.set_page_load_timeout(60)
    return driver


def tabela_carregou(driver):
    try:
        WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.CSS_SELECTOR, "table tbody tr")))
        return True
    except Exception:
        return False


def esta_na_lista(driver):
    url = driver.current_url or ""
    return "sessions/new" not in url and "quote_supplier_land" in url and tabela_carregou(driver)


def garantir_login(driver):
    driver.get(EVENTS_URL)
    if esta_na_lista(driver):
        print("Sessão já ativa.")
        return
    try:
        driver.get(LOGIN_URL)
    except Exception:
        pass
    while True:
        input("\n>>> Faça o login no Coupa na janela do Chrome e aperte ENTER aqui... ")
        driver.get(EVENTS_URL)
        if esta_na_lista(driver):
            print("Login confirmado.")
            return
        print("Ainda não estou vendo a lista de eventos. Confira o login e tente de novo.")


def ler_pagina(driver):
    """[(número, vencimento)] das linhas da tabela na tela."""
    eventos = []
    for linha in driver.find_elements(By.CSS_SELECTOR, "table tbody tr"):
        try:
            num = linha.find_element(By.CSS_SELECTOR, "a span.dt_open_link").text.strip()
        except (NoSuchElementException, StaleElementReferenceException):
            continue
        if not num:
            continue
        try:
            venc = linha.find_element(By.CLASS_NAME, "s-datatable-cell-end_time").text.strip()
        except (NoSuchElementException, StaleElementReferenceException):
            venc = ""
        eventos.append((num, venc))
    return eventos


def tem_proxima(driver):
    try:
        return bool(driver.find_element(By.CSS_SELECTOR, "a.next_page:not(.disabled)"))
    except NoSuchElementException:
        return False


def varrer(driver, max_paginas, todos, banco):
    """Preenche todos: número -> (vencimento, página). Número repetido fica com a primeira aparição.
    O dicionário vem de fora para o Ctrl+C não perder o que já foi lido."""
    anterior = None
    pagina = 1
    while pagina <= max_paginas:
        driver.get(f"{EVENTS_URL}?page={pagina}")   # sem ?page= o Coupa reabre a última página visitada
        if not tabela_carregou(driver):
            print(f"  Página {pagina}: tabela não carregou; tentando de novo...")
            time.sleep(5)
            driver.get(f"{EVENTS_URL}?page={pagina}")
            if not tabela_carregou(driver):
                if "sessions/new" in (driver.current_url or ""):
                    print("  Sessão caiu.")
                    garantir_login(driver)
                    continue   # repete a mesma página
                print(f"  Página {pagina}: tabela não carregou de novo; encerrando.")
                break
        eventos = ler_pagina(driver)
        if not eventos:
            print(f"  Página {pagina}: vazia; fim da lista.")
            break
        numeros = tuple(n for n, _ in eventos)
        if numeros == anterior:
            print(f"  Página {pagina}: igual à anterior; fim da lista.")
            break
        anterior = numeros
        novos = 0
        print(f"\n  Página {pagina}:")
        for num, venc in eventos:
            if num in todos:
                print(f"    {num:<10} (repetido, já lido na página {todos[num][1]})")
                continue
            todos[num] = (venc, pagina)
            novos += 1
            situacao = "já no banco" if num in banco else "FALTA NO BANCO"
            print(f"    {num:<10} vencimento: {venc or '(sem data)':<20} {situacao}")
        print(f"  Página {pagina}: {len(eventos)} linhas, {novos} novos (total {len(todos)}).")
        if not tem_proxima(driver):
            print("  Sem próxima página; fim da lista.")
            break
        pagina += 1
    return todos


def ler_banco(caminho):
    numeros = set()
    with open(caminho, encoding='utf-8') as f:
        for linha in f:
            try:
                titulo = json.loads(linha).get('titulo')
            except (ValueError, AttributeError):
                continue
            if titulo:
                numeros.add(str(titulo).strip())
    return numeros


def faltantes(todos, banco):
    """[(número, vencimento)] lidos no site que não estão no banco, na ordem em que apareceram."""
    return [(num, venc) for num, (venc, _) in todos.items() if num not in banco]


def registro(num, venc):
    """Mesmo formato do robô (banco_dados.salvar_evento_jsonl)."""
    return {"titulo": num, "data": str(datetime.now().date()), "respondido": False, "vencimento_tabela": venc}


def mostrar_previa(todos, novos, caminho):
    print("\n" + "=" * 60)
    print(f"LIDOS NO SITE: {len(todos)} | JÁ NO BANCO: {len(todos) - len(novos)} | FALTANDO: {len(novos)}")
    print("=" * 60)
    if not novos:
        print("Nenhum evento faltando: o eventos.jsonl não será alterado.")
        return
    print(f"{'EVENTO':<10}  VENCIMENTO")
    print(f"{'-' * 10}  {'-' * 10}")
    for num, venc in novos:
        print(f"{num:<10}  {venc or '(sem data)'}")
    print("\n" + "=" * 60)
    print("COMO VAI SALVAR")
    print("=" * 60)
    print(f"  Arquivo: {caminho}")
    print(f"  Acrescenta {len(novos)} linha(s) no fim; nenhuma linha existente é apagada ou alterada.")
    print(f"  Backup antes de gravar: {os.path.basename(caminho)}.antes-listar-<data_hora>")
    print(f"  Formato de cada linha: {json.dumps(registro(*novos[0]), ensure_ascii=False)}")
    print("=" * 60)


def termina_sem_quebra(caminho):
    with open(caminho, 'rb') as f:
        f.seek(0, os.SEEK_END)
        if f.tell() == 0:
            return False
        f.seek(-1, os.SEEK_END)
        return f.read(1) != b'\n'


def acrescentar(caminho, eventos):
    with open(caminho, 'a', encoding='utf-8') as f:
        if termina_sem_quebra(caminho):
            f.write('\n')   # última linha sem quebra: a primeira nova grudaria nela
        for num, venc in eventos:
            f.write(json.dumps(registro(num, venc)) + '\n')


def gravar(caminho, novos):
    """Faz backup, relê o banco (o robô pode ter gravado enquanto o site era lido) e acrescenta só o que ainda falta.
    Confere no fim e tenta de novo uma vez o que sumiu. Devolve quantos ficaram gravados."""
    shutil.copy2(caminho, caminho + datetime.now().strftime('.antes-listar-%Y%m%d-%H%M%S'))
    banco = ler_banco(caminho)
    ainda_faltam = [(n, v) for n, v in novos if n not in banco]
    if not ainda_faltam:
        return 0
    acrescentar(caminho, ainda_faltam)
    # o robô regrava o arquivo inteiro ao marcar 'respondido': se fez isso agora, as linhas novas somem
    banco = ler_banco(caminho)
    perdidos = [(n, v) for n, v in ainda_faltam if n not in banco]
    if perdidos:
        acrescentar(caminho, perdidos)
        banco = ler_banco(caminho)
        perdidos = [n for n, _ in perdidos if n not in banco]
        if perdidos:
            print(f"ATENÇÃO: não ficaram no arquivo: {', '.join(perdidos)}")
    return len(ainda_faltam) - len(perdidos)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--banco', default=BANCO_PADRAO, help=f'eventos.jsonl do Coupa (padrão {BANCO_PADRAO})')
    p.add_argument('--max-paginas', type=int, default=500, help='teto de páginas (padrão 500)')
    p.add_argument('--sim', action='store_true', help='grava sem pedir confirmação')
    args = p.parse_args()

    if not os.path.exists(args.banco):
        sys.exit(f"Banco não encontrado: {args.banco}")
    banco = ler_banco(args.banco)
    print(f"Banco: {args.banco} ({len(banco)} eventos)")

    driver = abrir_chrome()
    todos = {}
    try:
        garantir_login(driver)
        print("\nLendo a lista de eventos...")
        varrer(driver, args.max_paginas, todos, banco)
    except KeyboardInterrupt:
        print("\nInterrompido: segue com o que já foi lido.")
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    if not todos:
        print("\nNenhum evento lido.")
        return
    novos = faltantes(todos, ler_banco(args.banco))
    mostrar_previa(todos, novos, args.banco)
    if not novos:
        return
    if not args.sim and input(f"\nGravar {len(novos)} evento(s) no eventos.jsonl? (S/N) ").strip().upper() != 'S':
        print("Nada gravado.")
        return
    print(f"\n{gravar(args.banco, novos)} evento(s) gravado(s) em {args.banco}.")


if __name__ == '__main__':
    main()
