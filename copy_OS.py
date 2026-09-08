import os
import sys
import shutil
import time
import hashlib
from pathlib import Path
from datetime import datetime

# ============================================================
# COPY_OS // BACKUP NAS 1.5
# ============================================================
#
# Melhorias da 1.5:
# - Modo de cópia rápida (SHA-256 pode ser desativado)
# - Modo rápido compara tamanho + data via metadados
# - Verificação de tamanho também no modo rápido (mínimo de segurança)
# - Menu invertido: modo rápido é a opção padrão/recomendada
# - Correção: modo rápido agora realmente desativa o SHA-256
# - Checkpoints e flush de tela (evita parecer travado em rede/NAS)
# - Contador de arquivos na barra de progresso
# - Erros inesperados são exibidos em vez de travar em silêncio
# - Correção do AttributeError no modo sem hash
# - Log condicional: registra hash apenas quando ativo
# - Verificação de espaço livre considerando arquivos sobrescritos
# - Contagem correta de arquivos verificados com SHA-256
# - Limpeza de código morto
#
# Mantido da V4:
# - Suporte correto a ANSI no Windows
# - Verificação de espaço livre no destino
# - Exclusões úteis ativadas por padrão
# - Melhor suporte a caminhos longos no Windows
# - Velocidade média no resumo final
# - Interface mais estável
# - Código mais limpo e organizado
#
# Mantido da V3:
# - SHA-256 calculado durante a cópia
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

# Extensões ignoradas
EXTENSOES_EXCLUIDAS = {
    ".tmp",
    ".part",
    ".crdownload",
    ".download",
    ".partial",
    ".!ut",
    ".bc!",
}

# Nomes de arquivos/pastas ignorados
NOMES_EXCLUIDOS = {
    "Thumbs.db",
    ".DS_Store",
    "desktop.ini",
    ".AppleDouble",
    ".Spotlight-V100",
    ".Trashes",
    "@eaDir",
    "#recycle",
    "$RECYCLE.BIN",
    "System Volume Information",
}


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
        │     SECURE FILE TRANSFER SYSTEM       │
        │        NAS BACKUP 1.5 by MOOSH        │
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
    r"""
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

def deve_excluir(nome: str) -> bool:
    if nome in NOMES_EXCLUIDOS:
        return True

    extensao = Path(nome).suffix.lower()
    return extensao in EXTENSOES_EXCLUIDAS


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
    processados_arquivos=None,
):
    """
    Interface fixa.
    A tela inteira é redesenhada a cada atualização:
    ASCII art no topo + painel de progresso abaixo.
    Isso evita depender de \033[7F, que pode falhar no CMD/Windows Terminal.
    """

    if total_bytes > 0:
        percentual = (processados / total_bytes) * 100
    else:
        percentual = 100.0 if total_arquivos == 0 else 0.0

    percentual = min(max(percentual, 0), 100)

    preenchido = int(BAR_WIDTH * percentual / 100)
    barra = "█" * preenchido + "░" * (BAR_WIDTH - preenchido)

    velocidade, eta = calcular_velocidade_eta(
        processados,
        total_bytes,
        inicio,
    )

    if progresso_hash is not None:
        hash_texto = f"HASH DESTINO: {progresso_hash:6.2f}%"
    else:
        hash_texto = "HASH DESTINO: --"

    if tamanho_arquivo > 0:
        perc_arquivo = min((bytes_arquivo / tamanho_arquivo) * 100, 100)
    else:
        perc_arquivo = 0.0

    # Limpa a tela e volta ao canto superior esquerdo.
    print("\033[2J\033[H", end="")

    # Cabeçalho FIXO.
    print(ASCII_ART, end="")
    print("COPY_OS // BACKUP SYSTEM 1.5")
    print("=" * 70)

    # Painel dinâmico.
    print(f"PROGRESSO GERAL  [{barra}] {percentual:6.2f}%")
    if processados_arquivos is not None:
        arquivos_texto = f"ARQUIVOS: {processados_arquivos}/{total_arquivos}"
    else:
        arquivos_texto = f"ARQUIVOS: {total_arquivos}"
    print(
        f"TOTAL: {format_bytes(processados)} / {format_bytes(total_bytes)}"
        f"    |    {arquivos_texto}"
    )
    print(
        f"VELOCIDADE: {format_bytes(velocidade)}/s"
        f"    |    TEMPO RESTANTE: {format_time(eta)}"
    )
    print(f"STATUS: {status or '--'}")
    print(f"ARQUIVO: {arquivo_atual or '--'}")
    print(
        f"ARQUIVO ATUAL: {format_bytes(bytes_arquivo)} / "
        f"{format_bytes(tamanho_arquivo)} ({perc_arquivo:6.2f}%)"
    )
    print(hash_texto)

    # Mantém o cursor oculto enquanto a tela é atualizada.
    print("\033[?25l", end="", flush=True)


# ------------------------------------------------------------
# COLETA DE ARQUIVOS
# ------------------------------------------------------------

def coletar_arquivos(origem: Path, log_file: Path):
    arquivos = []
    total_bytes = 0
    erros = []
    contador = 0

    print("\n[1/3] Analisando arquivos da origem...", flush=True)
    print("      Calculando o tamanho total do backup...\n", flush=True)

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
                contador += 1

                # Feedback periódico durante o scan (evita parecer travado)
                if contador % 5000 == 0:
                    print(
                        f"      ... {contador} arquivos "
                        f"({format_bytes(total_bytes)})",
                        flush=True,
                    )

            except (OSError, IOError) as e:
                mensagem = f"Não foi possível ler {origem_arquivo}: {e}"
                erros.append(mensagem)
                escrever_log(log_file, mensagem)

    print(
        f"      Análise concluída: {contador} arquivos "
        f"({format_bytes(total_bytes)}).\n",
        flush=True,
    )

    return arquivos, total_bytes, erros


# ------------------------------------------------------------
# CÓPIA + HASH
# ------------------------------------------------------------

def copiar_com_retry(
    origem_arquivo: Path,
    destino_arquivo: Path,
    processados: int,
    total_bytes: int,
    total_arquivos: int,
    inicio: float,
    relativo: Path,
    tamanho_arquivo: int,
    log_file: Path,
    dry_run: bool = False,
    copia_segura: bool = True,
):
    tentativa = 0
    origem_arquivo = caminho_seguro(origem_arquivo)
    destino_arquivo = caminho_seguro(destino_arquivo)

    while tentativa < MAX_TENTATIVAS:
        temp = destino_arquivo.with_name(destino_arquivo.name + ".copying")

        try:
            if dry_run:
                escrever_log(log_file, f"DRY RUN - seria copiado: {relativo}")
                return True, processados

            destino_arquivo.parent.mkdir(parents=True, exist_ok=True)

            try:
                if temp.exists():
                    temp.unlink()
            except OSError:
                pass

            # Estado da origem antes da cópia
            origem_stat_inicio = origem_arquivo.stat()

            bytes_arquivo = 0
            sha_origem = hashlib.sha256() if copia_segura else None

            atualizar_interface(
                processados,
                total_bytes,
                total_arquivos,
                str(relativo),
                0,
                tamanho_arquivo,
                inicio,
                "COPIANDO + CALCULANDO SHA-256" if copia_segura else "COPIANDO — SEM HASH",
                None,
            )

            with open(origem_arquivo, "rb") as origem, open(temp, "wb") as destino:
                while True:
                    bloco = origem.read(BLOCO_COPIA)
                    if not bloco:
                        break

                    destino.write(bloco)
                    if copia_segura:
                        sha_origem.update(bloco)
                    bytes_arquivo += len(bloco)

                    atualizar_interface(
                        processados + bytes_arquivo,
                        total_bytes,
                        total_arquivos,
                        str(relativo),
                        bytes_arquivo,
                        tamanho_arquivo,
                        inicio,
                        "COPIANDO + CALCULANDO SHA-256" if copia_segura else "COPIANDO — SEM HASH",
                        None,
                    )

                destino.flush()
                os.fsync(destino.fileno())

            # Verifica se a origem mudou durante a cópia
            origem_stat_fim = origem_arquivo.stat()

            origem_mudou = (
                origem_stat_inicio.st_size != origem_stat_fim.st_size
                or origem_stat_inicio.st_mtime_ns != origem_stat_fim.st_mtime_ns
            )

            if origem_mudou:
                raise IOError(
                    "A origem foi modificada durante a cópia. "
                    "O arquivo será tentado novamente."
                )

            # Preserva metadados
            shutil.copystat(origem_arquivo, temp)

            if copia_segura:
                hash_origem = sha_origem.hexdigest()

                # Hash do temporário com progresso
                def progresso_hash(bytes_lidos):
                    percentual_hash = (
                        bytes_lidos / tamanho_arquivo * 100
                        if tamanho_arquivo > 0
                        else 100.0
                    )
                    atualizar_interface(
                        processados,
                        total_bytes,
                        total_arquivos,
                        str(relativo),
                        bytes_lidos,
                        tamanho_arquivo,
                        inicio,
                        "VERIFICANDO SHA-256 DO DESTINO",
                        percentual_hash,
                    )

                hash_destino, bytes_hash = calcular_sha256(
                    temp,
                    callback=progresso_hash
                )

                if bytes_hash != bytes_arquivo:
                    raise IOError(
                        "O tamanho do temporário mudou durante a verificação."
                    )

                if hash_origem != hash_destino:
                    escrever_log(
                        log_file,
                        f"FALHA DE INTEGRIDADE: {relativo} | "
                        f"Origem={hash_origem} | Temporário={hash_destino}",
                    )
                    raise IOError(
                        "SHA-256 do arquivo copiado não corresponde "
                        "ao SHA-256 calculado durante a cópia."
                    )

                status_final = "CONCLUÍDO / SHA-256 OK"
                hash_log = f" | SHA-256={hash_origem}"
            else:
                # Mínimo de segurança sem ler o conteúdo:
                # confere o tamanho final do temporário via metadado.
                if temp.stat().st_size != bytes_arquivo:
                    raise IOError(
                        "O tamanho do arquivo copiado não corresponde ao esperado."
                    )
                hash_origem = "DESATIVADO"
                status_final = "CONCLUÍDO / TAMANHO OK — SEM HASH"
                hash_log = " | SHA-256=DESATIVADO | TAMANHO OK"

            # Substituição atômica
            os.replace(temp, destino_arquivo)

            processados += bytes_arquivo

            escrever_log(
                log_file,
                f"OK: {relativo} | {format_bytes(bytes_arquivo)}{hash_log}",
            )

            atualizar_interface(
                processados,
                total_bytes,
                total_arquivos,
                str(relativo),
                tamanho_arquivo,
                tamanho_arquivo,
                inicio,
                status_final,
                100.0 if copia_segura else None,
            )

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
                atualizar_interface(
                    processados,
                    total_bytes,
                    total_arquivos,
                    str(relativo),
                    0,
                    tamanho_arquivo,
                    inicio,
                    "FALHA DEFINITIVA",
                    None,
                )
                return False, processados

            atualizar_interface(
                processados,
                total_bytes,
                total_arquivos,
                str(relativo),
                0,
                tamanho_arquivo,
                inicio,
                f"ERRO — TENTATIVA {tentativa}/{MAX_TENTATIVAS} — RETRY EM {ESPERA_RETRY}s",
                None,
            )
            time.sleep(ESPERA_RETRY)

        except Exception as e:
            escrever_log(log_file, f"ERRO NÃO RECUPERÁVEL: {relativo} | {e}")
            atualizar_interface(
                processados,
                total_bytes,
                total_arquivos,
                str(relativo),
                0,
                tamanho_arquivo,
                inicio,
                "ERRO NÃO RECUPERÁVEL",
                None,
            )
            return False, processados

    return False, processados


# ------------------------------------------------------------
# BACKUP PRINCIPAL
# ------------------------------------------------------------

def copiar_pastas(origem: str, destino: str, dry_run: bool = False, copia_segura: bool = True):
    origem = Path(origem)
    destino = Path(destino)

    log_file = criar_log()

    escrever_log(log_file, "=" * 60)
    escrever_log(log_file, "INÍCIO DO BACKUP COPY_OS 1.5")
    escrever_log(log_file, f"Origem: {origem}")
    escrever_log(log_file, f"Destino: {destino}")
    escrever_log(log_file, f"Dry Run: {dry_run}")
    escrever_log(log_file, f"Cópia segura / SHA-256: {copia_segura}")

    if not origem.exists():
        mensagem = f"A pasta de origem não existe: {origem}"
        print(f"\nERRO: {mensagem}", flush=True)
        escrever_log(log_file, mensagem)
        return

    # Coleta de arquivos
    arquivos, total_bytes, erros_analise = coletar_arquivos(origem, log_file)
    total_arquivos = len(arquivos)

    if erros_analise:
        print(
            f"\n[ATENÇÃO] {len(erros_analise)} erro(s) durante a análise. Veja o log.",
            flush=True,
        )

    print(f"\nArquivos encontrados : {total_arquivos}", flush=True)
    print(f"Tamanho total        : {format_bytes(total_bytes)}", flush=True)

    # Verificação de espaço livre
    if not dry_run:
        print("\n[2/3] Verificando espaço livre no destino...", flush=True)
        print("      (se o destino for uma rede/NAS, isso pode demorar)", flush=True)

        livre = espaco_livre(destino if destino.exists() else destino.parent)

        if livre >= 0:
            print(f"Espaço livre no destino: {format_bytes(livre)}", flush=True)

            # Apenas arquivos que serão copiados exigem espaço extra
            # (os já existentes são sobrescritos e não somam).
            bytes_a_copiar = 0
            for origem_arquivo, relativo, tamanho in arquivos:
                destino_arquivo = destino / relativo
                deve_copiar, _ = precisa_copiar(origem_arquivo, destino_arquivo)
                if deve_copiar:
                    bytes_a_copiar += tamanho

            print(f"Espaço adicional necessário: {format_bytes(bytes_a_copiar)}", flush=True)

            if livre < bytes_a_copiar:
                print("\n[AVISO] O espaço livre parece ser menor que o necessário.")
                print("        O script vai continuar, mas pode falhar no meio.")
                resposta = input("Deseja continuar mesmo assim? [s/N]: ").strip().lower()
                if resposta != "s":
                    print("Operação cancelada pelo usuário.")
                    escrever_log(log_file, "Cancelado por falta de espaço.")
                    return
        else:
            print("Não foi possível verificar o espaço livre no destino.", flush=True)
    else:
        print("\n[2/3] Verificação de espaço livre ignorada (modo simulação).", flush=True)

    modo_seguranca = (
        "CÓPIA RÁPIDA — COMPARA TAMANHO + DATA (SEM SHA-256)"
        if not copia_segura
        else "CÓPIA SEGURA — COMPARA TAMANHO + DATA + SHA-256"
    )
    print("\n[3/3] Iniciando o backup.", flush=True)
    print(f"Iniciando backup de:\n  {origem}\npara:\n  {destino}", flush=True)
    print(f"\nModo: {modo_seguranca}\n", flush=True)

    if dry_run:
        print("*** MODO SIMULAÇÃO — NENHUM ARQUIVO SERÁ COPIADO ***\n", flush=True)

    print("-" * 70, flush=True)

    processados = 0
    novos = 0
    atualizados = 0
    ignorados = 0
    falhas = 0
    verificados = 0

    inicio = time.time()
    arquivos_processados = 0

    atualizar_interface(
        0, total_bytes, total_arquivos, "", 0, 0, inicio, "INICIANDO", None
    )

    for origem_arquivo, relativo, tamanho in arquivos:
        destino_arquivo = destino / relativo
        arquivos_processados += 1

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
                    arquivos_processados,
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
                copia_segura,
            )

            if sucesso:
                if copia_segura:
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
                    arquivos_processados,
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
                arquivos_processados,
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
    if copia_segura:
        print(f"  Arquivos verificados SHA-256 : {verificados}")
    print(f"  Falhas                       : {falhas}")
    print(f"  Erros na análise             : {len(erros_analise)}")
    print(f"  Tamanho total analisado      : {format_bytes(total_bytes)}")
    print(f"  Tempo total                  : {format_time(duracao)}")
    print(f"  Velocidade média             : {format_bytes(velocidade_media)}/s")
    print(f"\n  LOG: {log_file.resolve()}")

    escrever_log(log_file, "=" * 60)
    log_final = (
        f"FINALIZADO | Novos={novos} | Atualizados={atualizados} | "
        f"Ignorados={ignorados} | "
    )
    if copia_segura:
        log_final += f"Verificados={verificados} | "
    log_final += (
        f"Falhas={falhas} | Erros análise={len(erros_analise)} | "
        f"Duração={format_time(duracao)} | "
        f"Velocidade média={format_bytes(velocidade_media)}/s"
    )
    escrever_log(log_file, log_final)


# ------------------------------------------------------------
# PROGRAMA PRINCIPAL
# ------------------------------------------------------------

if __name__ == "__main__":
    habilitar_ansi()

    print("\033[2J\033[H", end="")
    print(ASCII_ART)
    print("=" * 70)
    print("                 COPY_OS // BACKUP SYSTEM 1.5")
    print("=" * 70)
    print("\nEste programa NÃO apaga arquivos do destino.")
    print("SHA-256 pode ser ativado ou desativado conforme o modo escolhido.")

    origem = input("\nDigite o caminho da PASTA DE ORIGEM:\n> ").strip().strip('"')
    destino = input("\nDigite o caminho da PASTA DE DESTINO:\n> ").strip().strip('"')

    print("\nModo de execução:")
    print("  [1] Backup normal")
    print("  [2] Dry Run (simulação, não copia nada)")

    modo = input("\nEscolha [1/2]: ").strip()
    dry_run = modo == "2"

    print("\nModo de verificação:")
    print("  [1] Rápido (Recomendado)")
    print("      Compara tamanho + data — SEM SHA-256")
    print("      Muito mais rápido, mínimo de segurança")
    print("  [2] Seguro")
    print("      Compara tamanho + data + SHA-256")
    print("      Máxima integridade, copia mais devagar")
    modo_hash = input("\nEscolha [1/2]: ").strip()
    copia_segura = modo_hash == "2"

    if not origem or not destino:
        print("\nErro: Você precisa informar origem e destino.")
    else:
        try:
            copiar_pastas(origem, destino, dry_run, copia_segura)
        except KeyboardInterrupt:
            print("\n\nOperação interrompida pelo usuário.")
        except Exception as e:
            import traceback
            print("\n\nERRO INESPERADO:")
            traceback.print_exc()
            print("\nSe o erro envolver rede/NAS, verifique se o destino está acessível.")

    print("\033[?25h", end="", flush=True)
    input("\nPressione Enter para sair...")