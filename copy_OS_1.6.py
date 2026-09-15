import os
import sys
import shutil
import time
import hashlib
from pathlib import Path
from datetime import datetime

# ============================================================
# COPY_OS // BACKUP NAS V1.6
# ============================================================
#
# Melhorias da V1.6:
# - Suporte correto a ANSI no Windows
# - Verificação de espaço livre no destino
# - Exclusões úteis ativadas por padrão
# - Melhor suporte a caminhos longos no Windows
# - Velocidade média no resumo final
# - Interface mais estável
# - Código mais limpo e organizado
#
# Mantido da V3:
# - Modo rápido sem hash ou modo seguro com SHA-256
# - Verificação de integridade antes de substituir
# - Arquivo temporário .copying + os.replace
# - fsync
# - Detecção de mudança na origem
# - Retry automático
# - Dry Run
# - Log detalhado
# - NÃO apaga arquivos do destino
# ============================================================


# ------------------------------------------------------------
# CONFIGURAÇÕES
# ------------------------------------------------------------

BAR_WIDTH = 50
BLOCO_COPIA = 8 * 1024 * 1024          # 8 MB
MAX_TENTATIVAS = 999
ESPERA_RETRY = 10
UI_LINES = 7

# ------------------------------------------------------------
# INICIALIZAÇÃO DO TERMINAL (Windows)
# ------------------------------------------------------------

def habilitar_ansi():
    """Habilita códigos ANSI no Windows."""
    if sys.platform == "win32":
        os.system("")  # Truque clássico para habilitar ANSI


# ------------------------------------------------------------
# ASCII ART
# ------------------------------------------------------------

ASCII_ART = r"""
 ██████╗ ██████╗ ██████╗ ██╗   ██╗      ██████╗ ███████╗
██╔════╝██╔═══██╗██╔══██╗╚██╗ ██╔╝      ██╔═══██╗██╔══╝
██║     ██║   ██║██████╔╝ ╚████╔╝       ██║   ██║███████╗
██║     ██║   ██║██╔═══╝   ╚██╔╝        ██║   ██║╚════██║
╚██████╗╚██████╔╝██║        ██║         ╚██████╔╝███████║
 ╚═════╝ ╚═════╝ ╚═╝        ╚═╝          ╚═════╝ ╚══════╝

        ┌───────────────────────────────────────┐
        │            SECURE FILE TRANSFER SYSTEM            │
        │               NAS BACKUP 1.6 by MOOSH              │
        └───────────────────────────────────────┘

                      [ SYSTEM INITIALIZING... ]
"""


# ------------------------------------------------------------
# UTILITÁRIOS
# ------------------------------------------------------------

def format_bytes(value: float) -> str:
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    size = float(value)

    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.2f} {unit}"
        size /= 1024

    return f"{value} B"


def format_time(seconds: float) -> str:
    if seconds < 0:
        seconds = 0

    seconds = int(seconds)
    h, remainder = divmod(seconds, 3600)
    m, s = divmod(remainder, 60)

    return f"{h:02d}:{m:02d}:{s:02d}"


def criar_log() -> Path:
    pasta_log = Path("logs")
    pasta_log.mkdir(exist_ok=True)

    agora = datetime.now()
    nome = f"backup_{agora.strftime('%Y-%m-%d_%H-%M-%S')}.log"
    return pasta_log / nome


def escrever_log(log_file: Path, mensagem: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {mensagem}\n")


def limpar_linha():
    print("\033[2K", end="")


def caminho_seguro(caminho: Path) -> Path:
    """
    No Windows, adiciona o prefixo \\?\ para suportar
    caminhos muito longos.
    """
    if sys.platform != "win32":
        return caminho

    caminho_str = str(caminho.resolve())

    if caminho_str.startswith("\\\\?\\"):
        return Path(caminho_str)

    if caminho_str.startswith("\\\\"):
        # Caminho UNC (rede)
        return Path("\\\\?\\UNC\\" + caminho_str[2:])

    return Path("\\\\?\\" + caminho_str)


def espaco_livre(destino: Path) -> int:
    """Retorna o espaço livre em bytes no destino."""
    try:
        uso = shutil.disk_usage(destino)
        return uso.free
    except Exception:
        return -1


# ------------------------------------------------------------
# SHA-256
# ------------------------------------------------------------

def calcular_sha256(arquivo: Path, callback=None):
    sha = hashlib.sha256()
    bytes_lidos = 0

    with open(arquivo, "rb") as f:
        while True:
            bloco = f.read(BLOCO_COPIA)
            if not bloco:
                break

            sha.update(bloco)
            bytes_lidos += len(bloco)

            if callback is not None:
                callback(bytes_lidos)

    return sha.hexdigest(), bytes_lidos


# ------------------------------------------------------------
# DECISÃO DE CÓPIA
# ------------------------------------------------------------

def precisa_copiar(origem: Path, destino: Path):
    if not destino.exists():
        return True, "novo"

    try:
        origem_stat = origem.stat()
        destino_stat = destino.stat()
    except OSError:
        return True, "erro ao ler metadados"

    if origem_stat.st_size != destino_stat.st_size:
        return True, "tamanho diferente"

    if origem_stat.st_mtime_ns > destino_stat.st_mtime_ns:
        return True, "origem mais recente"

    return False, "já existe e está atualizado"


# ------------------------------------------------------------
# EXCLUSÕES
# ------------------------------------------------------------

EXTENSOES_EXCLUIDAS = {
    ".tmp", ".part", ".crdownload", ".download",
    ".partial", ".!ut", ".bc!",
}

NOMES_EXCLUIDOS = {
    "thumbs.db", ".ds_store", "desktop.ini", ".appledouble",
    ".spotlight-v100", ".trashes", ".fseventsd",
    ".documentrevisions-v100", ".temporaryitems", ".volumeicon.icns",
    "@eadir", "#recycle", "$recycle.bin", "system volume information",
}

def deve_excluir(nome: str) -> bool:
    nome_normalizado = nome.casefold()
    if nome_normalizado.startswith("._"):
        return True
    if nome_normalizado in NOMES_EXCLUIDOS:
        return True
    return Path(nome).suffix.casefold() in EXTENSOES_EXCLUIDAS


# ------------------------------------------------------------
# INTERFACE
# ------------------------------------------------------------

def calcular_velocidade_eta(processados, total_bytes, inicio):
    if inicio is None or processados <= 0:
        return 0.0, 0.0

    decorrido = time.time() - inicio
    if decorrido <= 0:
        return 0.0, 0.0

    velocidade = processados / decorrido
    restante = max(total_bytes - processados, 0)
    eta = restante / velocidade if velocidade > 0 else 0

    return velocidade, eta


def atualizar_interface(
    processados,
    total_bytes,
    total_arquivos,
    arquivo_atual="",
    bytes_arquivo=0,
    tamanho_arquivo=0,
    inicio=None,
    status="",
    progresso_hash=None,
):
    if total_bytes > 0:
        percentual = (processados / total_bytes) * 100
    else:
        percentual = 100.0 if total_arquivos == 0 else 0.0

    percentual = min(max(percentual, 0), 100)
    preenchido = int(BAR_WIDTH * percentual / 100)
    barra = "█" * preenchido + "░" * (BAR_WIDTH - preenchido)

    velocidade, eta = calcular_velocidade_eta(processados, total_bytes, inicio)

    if progresso_hash is not None:
        hash_texto = f"HASH DESTINO: {progresso_hash:6.2f}%"
    else:
        hash_texto = "HASH DESTINO: --"

    if tamanho_arquivo > 0:
        perc_arquivo = min((bytes_arquivo / tamanho_arquivo) * 100, 100)
    else:
        perc_arquivo = 0.0

    linhas = [
        f"PROGRESSO GERAL  [{barra}] {percentual:6.2f}%",
        f"TOTAL: {format_bytes(processados)} / {format_bytes(total_bytes)}    |    ARQUIVOS: {total_arquivos}",
        f"VELOCIDADE: {format_bytes(velocidade)}/s    |    TEMPO RESTANTE: {format_time(eta)}",
        f"STATUS: {status or '--'}",
        f"ARQUIVO: {arquivo_atual or '--'}",
        f"ARQUIVO ATUAL: {format_bytes(bytes_arquivo)} / {format_bytes(tamanho_arquivo)} ({perc_arquivo:6.2f}%)",
        hash_texto,
    ]

    # Redesenha a tela inteira em vez de tentar subir N linhas.
    # Isso evita que caminhos/nomes longos quebrem a interface no Windows.
    # Quando uma linha ultrapassa a largura do terminal, ela ocupa linhas
    # físicas adicionais e o antigo \033[7F acabava ficando desalinhado,
    # causando exatamente o efeito de a barra se repetir para cima.
    print("\033[2J\033[H", end="")
    print(ASCII_ART)
    print("=" * 70)
    print("                 COPY_OS // BACKUP SYSTEM 1.6")
    print("=" * 70)
    print()

    for linha in linhas:
        print(linha)


# ------------------------------------------------------------
# COLETA DE ARQUIVOS
# ------------------------------------------------------------

def coletar_arquivos(origem: Path, log_file: Path):
    arquivos = []
    total_bytes = 0
    erros = []

    print("\nAnalisando arquivos da origem...")
    print("Calculando o tamanho total do backup...\n")

    def tratar_erro(erro):
        caminho = getattr(erro, "filename", "caminho desconhecido")
        mensagem = f"ERRO DE LEITURA durante a análise: {caminho} | {erro}"
        erros.append(mensagem)
        escrever_log(log_file, mensagem)

    for root, dirs, files in os.walk(origem, onerror=tratar_erro):
        # Remove pastas que devem ser excluídas
        dirs[:] = [d for d in dirs if not deve_excluir(d)]

        root_path = Path(root)

        for nome in files:
            if deve_excluir(nome):
                continue

            origem_arquivo = root_path / nome

            try:
                tamanho = origem_arquivo.stat().st_size
                relativo = origem_arquivo.relative_to(origem)

                arquivos.append((origem_arquivo, relativo, tamanho))
                total_bytes += tamanho

            except (OSError, IOError) as e:
                mensagem = f"Não foi possível ler {origem_arquivo}: {e}"
                erros.append(mensagem)
                escrever_log(log_file, mensagem)

    return arquivos, total_bytes, erros


# ------------------------------------------------------------
# CÓPIA + HASH
# ------------------------------------------------------------

def copiar_com_retry(
    origem_arquivo: Path, destino_arquivo: Path, processados: int,
    total_bytes: int, total_arquivos: int, inicio: float, relativo: Path,
    tamanho_arquivo: int, log_file: Path, dry_run: bool = False,
    modo_seguro: bool = False,
):
    tentativa = 0
    origem_arquivo = caminho_seguro(origem_arquivo)
    destino_arquivo = caminho_seguro(destino_arquivo)

    while tentativa < MAX_TENTATIVAS:
        temp = destino_arquivo.with_name(destino_arquivo.name + ".copying")
        try:
            if dry_run:
                escrever_log(log_file, f"DRY RUN - seria copiado: {relativo}")
                return True, processados + tamanho_arquivo

            destino_arquivo.parent.mkdir(parents=True, exist_ok=True)
            try:
                if temp.exists():
                    temp.unlink()
            except OSError:
                pass

            origem_stat_inicio = origem_arquivo.stat()
            bytes_arquivo = 0
            sha_origem = hashlib.sha256() if modo_seguro else None
            status_copia = ("COPIANDO + CALCULANDO SHA-256" if modo_seguro
                            else "COPIANDO — MODO RÁPIDO (SEM HASH)")

            atualizar_interface(processados, total_bytes, total_arquivos,
                                 str(relativo), 0, tamanho_arquivo, inicio,
                                 status_copia, None)

            with open(origem_arquivo, "rb") as origem, open(temp, "wb") as destino:
                while True:
                    bloco = origem.read(BLOCO_COPIA)
                    if not bloco:
                        break
                    destino.write(bloco)
                    if sha_origem is not None:
                        sha_origem.update(bloco)
                    bytes_arquivo += len(bloco)
                    atualizar_interface(processados + bytes_arquivo, total_bytes,
                                         total_arquivos, str(relativo),
                                         bytes_arquivo, tamanho_arquivo, inicio,
                                         status_copia, None)
                destino.flush()
                # No modo rápido, evitamos fsync() por arquivo. Em NAS/SMB,
                # fsync pode introduzir uma latência grande e desnecessária.
                # O modo seguro mantém fsync antes da verificação SHA-256.
                if modo_seguro:
                    os.fsync(destino.fileno())

            origem_stat_fim = origem_arquivo.stat()
            if (origem_stat_inicio.st_size != origem_stat_fim.st_size or
                    origem_stat_inicio.st_mtime_ns != origem_stat_fim.st_mtime_ns):
                raise IOError("A origem foi modificada durante a cópia. O arquivo será tentado novamente.")

            if bytes_arquivo != tamanho_arquivo:
                raise IOError(f"Tamanho copiado diferente do esperado: {bytes_arquivo} != {tamanho_arquivo}")

            shutil.copystat(origem_arquivo, temp)

            if modo_seguro:
                def progresso_hash(bytes_lidos):
                    percentual_hash = (bytes_lidos / tamanho_arquivo * 100) if tamanho_arquivo > 0 else 100.0
                    atualizar_interface(processados, total_bytes, total_arquivos, str(relativo),
                                         bytes_lidos, tamanho_arquivo, inicio,
                                         "VERIFICANDO SHA-256 DO DESTINO", percentual_hash)

                hash_destino, bytes_hash = calcular_sha256(temp, callback=progresso_hash)
                hash_origem = sha_origem.hexdigest()
                if bytes_hash != bytes_arquivo:
                    raise IOError("O tamanho do temporário mudou durante a verificação.")
                if hash_origem != hash_destino:
                    escrever_log(log_file, f"FALHA DE INTEGRIDADE: {relativo} | Origem={hash_origem} | Temporário={hash_destino}")
                    raise IOError("SHA-256 do arquivo copiado não corresponde ao SHA-256 calculado durante a cópia.")
                status_final = "CONCLUÍDO / SHA-256 OK"
                hash_log = f" | SHA-256={hash_origem}"
            else:
                status_final = "CONCLUÍDO / CÓPIA RÁPIDA"
                hash_log = ""

            os.replace(temp, destino_arquivo)
            processados += bytes_arquivo
            escrever_log(log_file, f"OK: {relativo} | {format_bytes(bytes_arquivo)}{hash_log}")
            atualizar_interface(processados, total_bytes, total_arquivos, str(relativo),
                                 tamanho_arquivo, tamanho_arquivo, inicio, status_final,
                                 100.0 if modo_seguro else None)
            return True, processados

        except KeyboardInterrupt:
            escrever_log(log_file, "BACKUP INTERROMPIDO PELO USUÁRIO.")
            raise
        except (OSError, IOError, ConnectionError, TimeoutError) as e:
            tentativa += 1
            try:
                if temp.exists():
                    temp.unlink()
            except OSError:
                pass
            escrever_log(log_file, f"TENTATIVA {tentativa} FALHOU: {relativo} | {e}")
            if tentativa >= MAX_TENTATIVAS:
                atualizar_interface(processados, total_bytes, total_arquivos, str(relativo),
                                     0, tamanho_arquivo, inicio, "FALHA DEFINITIVA", None)
                return False, processados
            atualizar_interface(processados, total_bytes, total_arquivos, str(relativo),
                                 0, tamanho_arquivo, inicio,
                                 f"ERRO — TENTATIVA {tentativa}/{MAX_TENTATIVAS} — RETRY EM {ESPERA_RETRY}s", None)
            time.sleep(ESPERA_RETRY)
        except Exception as e:
            escrever_log(log_file, f"ERRO NÃO RECUPERÁVEL: {relativo} | {e}")
            atualizar_interface(processados, total_bytes, total_arquivos, str(relativo),
                                 0, tamanho_arquivo, inicio, "ERRO NÃO RECUPERÁVEL", None)
            return False, processados

    return False, processados


# ------------------------------------------------------------
# BACKUP PRINCIPAL
# ------------------------------------------------------------

def copiar_pastas(origem: str, destino: str, dry_run: bool = False, modo_seguro: bool = False):
    origem = Path(origem)
    destino = Path(destino)

    log_file = criar_log()

    escrever_log(log_file, "=" * 60)
    escrever_log(log_file, "INÍCIO DO BACKUP COPY_OS V1.6")
    escrever_log(log_file, f"Origem: {origem}")
    escrever_log(log_file, f"Destino: {destino}")
    escrever_log(log_file, f"Dry Run: {dry_run}")
    escrever_log(log_file, f"Modo seguro (SHA-256): {modo_seguro}")

    if not origem.exists():
        mensagem = f"A pasta de origem não existe: {origem}"
        print(f"\nERRO: {mensagem}")
        escrever_log(log_file, mensagem)
        return

    # Coleta de arquivos
    arquivos, total_bytes, erros_analise = coletar_arquivos(origem, log_file)
    total_arquivos = len(arquivos)

    if erros_analise:
        print(f"\n[ATENÇÃO] {len(erros_analise)} erro(s) durante a análise. Veja o log.")

    print(f"\nArquivos encontrados : {total_arquivos}")
    print(f"Tamanho total        : {format_bytes(total_bytes)}")

    # Verificação de espaço livre
    if not dry_run:
        livre = espaco_livre(destino if destino.exists() else destino.parent)

        if livre >= 0:
            print(f"Espaço livre no destino: {format_bytes(livre)}")

            if livre < total_bytes:
                print("\n[AVISO] O espaço livre parece ser menor que o tamanho total do backup.")
                print("        O script vai continuar, mas pode falhar no meio.")
                resposta = input("Deseja continuar mesmo assim? [s/N]: ").strip().lower()
                if resposta != "s":
                    print("Operação cancelada pelo usuário.")
                    escrever_log(log_file, "Cancelado por falta de espaço.")
                    return
        else:
            print("Não foi possível verificar o espaço livre no destino.")

    print(f"\nIniciando backup de:\n  {origem}\npara:\n  {destino}\n")

    if dry_run:
        print("*** MODO SIMULAÇÃO — NENHUM ARQUIVO SERÁ COPIADO ***\n")

    print("-" * 70)

    # Reserva linhas da interface
    for _ in range(UI_LINES):
        print("")

    processados = 0
    novos = 0
    atualizados = 0
    ignorados = 0
    falhas = 0
    verificados = 0

    inicio = time.time()

    atualizar_interface(
        0, total_bytes, total_arquivos, "", 0, 0, inicio, "INICIANDO", None
    )

    for origem_arquivo, relativo, tamanho in arquivos:
        destino_arquivo = destino / relativo

        try:
            deve_copiar, motivo = precisa_copiar(origem_arquivo, destino_arquivo)

            if not deve_copiar:
                processados += tamanho
                ignorados += 1

                escrever_log(log_file, f"IGNORADO: {relativo} | {motivo}")

                atualizar_interface(
                    processados,
                    total_bytes,
                    total_arquivos,
                    str(relativo),
                    tamanho,
                    tamanho,
                    inicio,
                    "JÁ ATUALIZADO — IGNORADO",
                    None,
                )
                continue

            if motivo == "novo":
                novos += 1
                status = "NOVO"
            else:
                atualizados += 1
                status = "ATUALIZANDO"

            escrever_log(log_file, f"{status}: {relativo} | {motivo}")

            sucesso, processados = copiar_com_retry(
                origem_arquivo,
                destino_arquivo,
                processados,
                total_bytes,
                total_arquivos,
                inicio,
                relativo,
                tamanho,
                log_file,
                dry_run,
                modo_seguro,
            )

            if sucesso:
                verificados += 1
            else:
                falhas += 1
                processados += tamanho

                atualizar_interface(
                    processados,
                    total_bytes,
                    total_arquivos,
                    str(relativo),
                    0,
                    tamanho,
                    inicio,
                    "FALHA — CONTINUANDO",
                    None,
                )

        except KeyboardInterrupt:
            escrever_log(log_file, "BACKUP INTERROMPIDO PELO USUÁRIO.")
            print("\n\nBackup interrompido.")
            raise

        except (OSError, IOError, ConnectionError, TimeoutError) as e:
            falhas += 1
            processados += tamanho

            escrever_log(log_file, f"FALHA AO PROCESSAR: {relativo} | {e}")

            atualizar_interface(
                processados,
                total_bytes,
                total_arquivos,
                str(relativo),
                0,
                tamanho,
                inicio,
                "ERRO — CONTINUANDO",
                None,
            )

    duracao = time.time() - inicio
    velocidade_media = processados / duracao if duracao > 0 else 0

    atualizar_interface(
        total_bytes, total_bytes, total_arquivos, "", 0, 0, inicio, "FINALIZADO", None
    )

    print("\n" + "-" * 70)
    print("\nRESUMO DO BACKUP:")
    print(f"  Arquivos analisados          : {total_arquivos}")
    print(f"  Arquivos novos               : {novos}")
    print(f"  Arquivos atualizados         : {atualizados}")
    print(f"  Arquivos já atualizados      : {ignorados}")
    print(f"  Arquivos verificados SHA-256 : {verificados if modo_seguro else 'N/A (modo rápido)'}")
    print(f"  Falhas                       : {falhas}")
    print(f"  Erros na análise             : {len(erros_analise)}")
    print(f"  Tamanho total analisado      : {format_bytes(total_bytes)}")
    print(f"  Tempo total                  : {format_time(duracao)}")
    print(f"  Velocidade média             : {format_bytes(velocidade_media)}/s")
    print(f"\n  LOG: {log_file.resolve()}")

    escrever_log(log_file, "=" * 60)
    escrever_log(
        log_file,
        f"FINALIZADO | Novos={novos} | Atualizados={atualizados} | "
        f"Ignorados={ignorados} | Verificados={verificados} | "
        f"Falhas={falhas} | Erros análise={len(erros_analise)} | "
        f"Duração={format_time(duracao)} | "
        f"Velocidade média={format_bytes(velocidade_media)}/s",
    )


# ------------------------------------------------------------
# PROGRAMA PRINCIPAL
# ------------------------------------------------------------

if __name__ == "__main__":
    habilitar_ansi()

    print("\033[2J\033[H", end="")
    print(ASCII_ART)
    print("=" * 70)
    print("                 COPY_OS // BACKUP SYSTEM 1.6")
    print("=" * 70)
    print("\nEste programa NÃO apaga arquivos do destino.")
    print("O backup rápido usa tamanho + data; o backup seguro usa SHA-256.")

    origem = input("\nDigite o caminho da PASTA DE ORIGEM:\n> ").strip().strip('"')
    destino = input("\nDigite o caminho da PASTA DE DESTINO:\n> ").strip().strip('"')

    print("\nModo de execução:")
    print("  [1] Backup normal")
    print("  [2] Dry Run (simulação, não copia nada)")

    modo = input("\nEscolha [1/2] (padrão: 1): ").strip()
    dry_run = modo == "2"

    modo_seguro = False
    if not dry_run:
        print("\nTipo de backup:")
        print("  [1] Cópia rápida (tamanho + data, sem SHA-256)")
        print("  [2] Cópia segura (tamanho + data + SHA-256)")
        tipo = input("\nEscolha [1/2] (padrão: 1): ").strip()
        modo_seguro = tipo == "2"

    if not origem or not destino:
        print("\nErro: Você precisa informar origem e destino.")
    else:
        try:
            copiar_pastas(origem, destino, dry_run, modo_seguro)
        except KeyboardInterrupt:
            print("\n\nOperação interrompida pelo usuário.")

    input("\nPressione Enter para sair...")