import os
import sys
import shutil
import time
import hashlib
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from datetime import datetime

# ============================================================
# COPY_OS // BACKUP NAS V1.7
# ============================================================
#
# Melhorias da V1.7:
# - Estatísticas separadas de cópia, simulação, exclusão e falha
# - Velocidade baseada em bytes transferidos e tempo ativo de cópia
# - ETA apenas da cópia pendente, sem estimar hash ou espera de retry
# - Contador processados/total e resumo detalhado no terminal e log
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
MAX_TENTATIVAS = 5
ESPERA_RETRY = 10


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

ASCII_ART = """
   ██████╗ ██████╗ ██████╗ ██╗   ██╗     ██████╗ ███████╗   
  ██╔════╝██╔═══██╗██╔══██╗╚██╗ ██╔╝    ██╔═══██╗██╔════╝   
  ██║     ██║   ██║██████╔╝ ╚████╔╝     ██║   ██║███████╗   
  ██║     ██║   ██║██╔═══╝   ╚██╔╝      ██║   ██║╚════██║   
  ╚██████╗╚██████╔╝██║        ██║       ╚██████╔╝███████║   
   ╚═════╝ ╚═════╝ ╚═╝        ╚═╝        ╚═════╝ ╚══════╝   

┌──────────────────────────────────────────────────────────┐
│               SECURE FILE TRANSFER SYSTEM                │
│                 NAS BACKUP 1.7 by MOOSH                  │
└──────────────────────────────────────────────────────────┘
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
    No Windows, adiciona um prefixo especial para suportar
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

@dataclass
class Estatisticas:
    total_arquivos: int = 0
    total_bytes: int = 0
    bytes_planejados: int = 0
    processados: int = 0
    copiados: int = 0
    ignorados: int = 0
    falhas: int = 0
    simulados: int = 0
    verificados: int = 0
    tentativas_falhas: int = 0
    bytes_copiados: int = 0
    bytes_ignorados: int = 0
    bytes_simulados: int = 0
    bytes_falhos: int = 0
    bytes_transferidos: int = 0
    bytes_verificados: int = 0
    bytes_atuais: int = 0
    tempo_copia: float = 0.0
    tempo_hash: float = 0.0
    dry_run: bool = False

    @property
    def velocidade(self):
        return self.bytes_transferidos / self.tempo_copia if self.tempo_copia > 0 else 0.0

    @property
    def eta_copia(self):
        restante = max(0, self.bytes_planejados - self.bytes_copiados - self.bytes_falhos - self.bytes_atuais)
        return restante / self.velocidade if self.velocidade > 0 else None


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
    estatisticas=None,
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

    if estatisticas is not None:
        e = estatisticas
        linhas[1] = f"ANALISADO: {format_bytes(total_bytes)} | ARQUIVOS: {e.processados} / {e.total_arquivos}"
        eta = e.eta_copia
        linhas[2] = ("SIMULAÇÃO — SEM TRANSFERÊNCIA" if e.dry_run else
                     f"CÓPIA: {format_bytes(e.velocidade)}/s | ETA CÓPIA: {format_time(eta) if eta is not None else '--'} (sem hash/retry)")
        linhas += [
            f"COPIADOS: {e.copiados} | IGNORADOS: {e.ignorados} | FALHAS: {e.falhas} | SIMULADOS: {e.simulados}",
            f"TRANSFERIDOS: {format_bytes(e.bytes_transferidos)} (inclui novas tentativas) | SHA-256 OK: {e.verificados}",
        ]

    # Redesenha a tela inteira em vez de tentar subir N linhas.
    # Isso evita que caminhos/nomes longos quebrem a interface no Windows.
    # Quando uma linha ultrapassa a largura do terminal, ela ocupa linhas
    # físicas adicionais e o antigo \033[7F acabava ficando desalinhado,
    # causando exatamente o efeito de a barra se repetir para cima.
    print("\033[2J\033[H", end="")
    print(ASCII_ART)
    print()

    for linha in linhas:
        print(linha)
    sys.stdout.flush()


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

            except (OSError, IOError) as erro:
                mensagem = f"Não foi possível ler {origem_arquivo}: {erro}"
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
    estatisticas=None,
):
    e = estatisticas if estatisticas is not None else Estatisticas()
    atualizar_interface = partial(globals()['atualizar_interface'], estatisticas=e)
    tentativa = 0
    tamanho_analisado = tamanho_arquivo
    origem_arquivo = caminho_seguro(origem_arquivo)
    destino_arquivo = caminho_seguro(destino_arquivo)

    while tentativa < MAX_TENTATIVAS:
        temp = destino_arquivo.with_name(destino_arquivo.name + ".copying")
        etapa = "preparar destino"
        try:
            if dry_run:
                escrever_log(log_file, f"DRY RUN - seria copiado: {relativo}")
                e.simulados += 1
                e.bytes_simulados += tamanho_arquivo
                return True, processados + tamanho_arquivo

            destino_arquivo.parent.mkdir(parents=True, exist_ok=True)
            try:
                if temp.exists():
                    temp.unlink()
            except OSError:
                pass

            etapa = "ler metadados da origem"
            origem_stat_inicio = origem_arquivo.stat()
            tamanho_arquivo = origem_stat_inicio.st_size
            bytes_arquivo = 0
            e.bytes_atuais = 0
            sha_origem = hashlib.sha256() if modo_seguro else None
            status_copia = ("COPIANDO + CALCULANDO SHA-256" if modo_seguro
                            else "COPIANDO — MODO RÁPIDO (SEM HASH)")

            atualizar_interface(processados, total_bytes, total_arquivos,
                                 str(relativo), 0, tamanho_arquivo, inicio,
                                 status_copia, None)

            etapa = "abrir origem/temporário e transferir dados"
            with open(origem_arquivo, "rb") as origem, open(temp, "wb") as destino:
                while True:
                    inicio_bloco = time.perf_counter()
                    bloco = origem.read(BLOCO_COPIA)
                    if not bloco:
                        break
                    destino.write(bloco)
                    if sha_origem is not None:
                        sha_origem.update(bloco)
                    e.tempo_copia += time.perf_counter() - inicio_bloco
                    e.bytes_transferidos += len(bloco)
                    bytes_arquivo += len(bloco)
                    e.bytes_atuais = bytes_arquivo
                    atualizar_interface(processados + bytes_arquivo, total_bytes,
                                         total_arquivos, str(relativo),
                                         bytes_arquivo, tamanho_arquivo, inicio,
                                         status_copia, None)
                etapa = "flush do temporário"
                inicio_flush = time.perf_counter()
                destino.flush()
                # No modo rápido, evitamos fsync() por arquivo. Em NAS/SMB,
                # fsync pode introduzir uma latência grande e desnecessária.
                # O modo seguro mantém fsync antes da verificação SHA-256.
                if modo_seguro:
                    os.fsync(destino.fileno())
                e.tempo_copia += time.perf_counter() - inicio_flush

            etapa = "confirmar estabilidade e tamanho da origem"
            origem_stat_fim = origem_arquivo.stat()
            if (origem_stat_inicio.st_size != origem_stat_fim.st_size or
                    origem_stat_inicio.st_mtime_ns != origem_stat_fim.st_mtime_ns):
                raise IOError("A origem foi modificada durante a cópia. O arquivo será tentado novamente.")

            if bytes_arquivo != tamanho_arquivo:
                raise IOError(f"Tamanho copiado diferente do esperado: {bytes_arquivo} != {tamanho_arquivo}")

            etapa = "preservar metadados no temporário"
            shutil.copystat(origem_arquivo, temp)

            if modo_seguro:
                def progresso_hash(bytes_lidos):
                    percentual_hash = (bytes_lidos / tamanho_arquivo * 100) if tamanho_arquivo > 0 else 100.0
                    atualizar_interface(processados, total_bytes, total_arquivos, str(relativo),
                                         bytes_lidos, tamanho_arquivo, inicio,
                                         "VERIFICANDO SHA-256 DO DESTINO", percentual_hash)

                etapa = "verificar SHA-256 do temporário"
                inicio_hash = time.perf_counter()
                try:
                    hash_destino, bytes_hash = calcular_sha256(temp, callback=progresso_hash)
                finally:
                    e.tempo_hash += time.perf_counter() - inicio_hash
                e.bytes_verificados += bytes_hash
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

            etapa = "substituir arquivo de destino"
            os.replace(temp, destino_arquivo)
            diferenca = bytes_arquivo - tamanho_analisado
            e.total_bytes += diferenca
            e.bytes_planejados += diferenca
            total_bytes += diferenca
            e.copiados += 1
            e.bytes_copiados += bytes_arquivo
            e.bytes_atuais = 0
            if modo_seguro:
                e.verificados += 1
            processados += bytes_arquivo
            escrever_log(log_file, f"OK: {relativo} | {format_bytes(bytes_arquivo)}{hash_log}")
            atualizar_interface(processados, total_bytes, total_arquivos, str(relativo),
                                 tamanho_arquivo, tamanho_arquivo, inicio, status_final,
                                 100.0 if modo_seguro else None)
            return True, processados

        except KeyboardInterrupt:
            escrever_log(log_file, "BACKUP INTERROMPIDO PELO USUÁRIO.")
            raise
        except (OSError, IOError, ConnectionError, TimeoutError) as erro:
            tentativa += 1
            e.tentativas_falhas += 1
            e.bytes_atuais = 0
            try:
                if temp.exists():
                    temp.unlink()
            except OSError:
                pass
            detalhe = f"{etapa}: {type(erro).__name__}: {erro} | errno={getattr(erro, 'errno', None)} | winerror={getattr(erro, 'winerror', None)}"
            escrever_log(log_file, f"TENTATIVA {tentativa} FALHOU: {relativo} | {detalhe}")
            if tentativa >= MAX_TENTATIVAS:
                atualizar_interface(processados, total_bytes, total_arquivos, str(relativo),
                                     0, tamanho_arquivo, inicio, f"FALHA APÓS {tentativa} TENTATIVAS — {detalhe}", None)
                return False, processados
            atualizar_interface(processados, total_bytes, total_arquivos, str(relativo),
                                 0, tamanho_arquivo, inicio,
                                 f"ERRO — TENTATIVA {tentativa}/{MAX_TENTATIVAS} — RETRY EM {ESPERA_RETRY}s | {detalhe}", None)
            time.sleep(ESPERA_RETRY)
        except Exception as erro:
            escrever_log(log_file, f"ERRO NÃO RECUPERÁVEL: {relativo} | {erro}")
            atualizar_interface(processados, total_bytes, total_arquivos, str(relativo),
                                 0, tamanho_arquivo, inicio, "ERRO NÃO RECUPERÁVEL", None)
            return False, processados

    return False, processados


# ------------------------------------------------------------
# BACKUP PRINCIPAL
# ------------------------------------------------------------

def copiar_pastas(origem: str, destino: str, dry_run: bool = False, modo_seguro: bool = False):
    e = Estatisticas(dry_run=dry_run)
    atualizar_interface = partial(globals()['atualizar_interface'], estatisticas=e)
    origem = Path(origem)
    destino = Path(destino)

    log_file = criar_log()

    escrever_log(log_file, "=" * 60)
    escrever_log(log_file, "INÍCIO DO BACKUP COPY_OS V1.7")
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
    e.total_arquivos = total_arquivos
    e.total_bytes = total_bytes
    # Planejamento inicial para estimar apenas o volume a transferir.
    # A decisão é reavaliada antes de cada arquivo.
    plano = {relativo: precisa_copiar(arquivo, destino / relativo)[0]
             for arquivo, relativo, tamanho in arquivos}
    e.bytes_planejados = sum(tamanho for _, relativo, tamanho in arquivos if plano[relativo])

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

            if deve_copiar != plano[relativo]:
                e.bytes_planejados += tamanho if deve_copiar else -tamanho
            if not deve_copiar:
                processados += tamanho
                ignorados += 1
                e.ignorados += 1
                e.bytes_ignorados += tamanho
                e.processados += 1

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
                e,
            )

            total_bytes = e.total_bytes
            e.processados += 1
            if sucesso:
                verificados += int(modo_seguro and not dry_run)
                atualizar_interface(processados, total_bytes, total_arquivos, str(relativo),
                                     tamanho, tamanho, inicio,
                                     "SERIA COPIADO" if dry_run else "CONCLUÍDO", None)
            else:
                falhas += 1
                e.falhas += 1
                e.bytes_falhos += tamanho
                e.bytes_atuais = 0
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

        except (OSError, IOError, ConnectionError, TimeoutError) as erro:
            falhas += 1
            e.falhas += 1
            e.processados += 1
            e.bytes_falhos += tamanho
            e.bytes_atuais = 0
            processados += tamanho

            escrever_log(log_file, f"FALHA AO PROCESSAR: {relativo} | {erro}")

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

    atualizar_interface(
        total_bytes, total_bytes, total_arquivos, "", 0, 0, inicio, "FINALIZADO", None
    )

    print("\n" + "-" * 70)
    resumo = [
        "RESUMO DA SIMULAÇÃO:" if dry_run else "RESUMO DO BACKUP:",
        f"Arquivos processados: {e.processados} / {e.total_arquivos}",
        f"Analisados: {format_bytes(e.total_bytes)}",
        f"Copiados com sucesso: {e.copiados} | {format_bytes(e.bytes_copiados)}",
        f"Ignorados (já atualizados): {e.ignorados} | {format_bytes(e.bytes_ignorados)}",
        f"Seriam copiados: {e.simulados} | {format_bytes(e.bytes_simulados)}",
        f"Falhas: {e.falhas} | {format_bytes(e.bytes_falhos)}",
        f"Erros na análise: {len(erros_analise)}",
        f"Tentativas com falha: {e.tentativas_falhas}",
        f"Bytes transferidos (inclui novas tentativas): {format_bytes(e.bytes_transferidos)}",
        f"Arquivos verificados SHA-256: {e.verificados}",
        f"Bytes lidos na verificação do temporário: {format_bytes(e.bytes_verificados)}",
        f"Tempo de cópia (inclui hash da origem e flush): {e.tempo_copia:.2f}s",
        f"Tempo de verificação do temporário: {e.tempo_hash:.2f}s",
        f"Velocidade média durante a cópia: {format_bytes(e.velocidade)}/s",
        f"Tempo total após análise: {format_time(duracao)}",
    ]
    for linha in resumo:
        print("  " + linha)
        escrever_log(log_file, linha)
    print(f"\n  LOG: {log_file.resolve()}")
    return e


# ------------------------------------------------------------
# PROGRAMA PRINCIPAL
# ------------------------------------------------------------

if __name__ == "__main__":
    habilitar_ansi()

    print("\033[2J\033[H", end="")
    print(ASCII_ART)
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