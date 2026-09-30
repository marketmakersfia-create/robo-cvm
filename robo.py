"""
Robô CVM → Telegram — Market Makers FIA
=======================================

O que ele faz, a cada execução:
  1. Lê a lista de empresas da carteira numa planilha do Google.
  2. Descobre o código CVM de cada empresa (ou usa o que estiver na planilha).
  3. Consulta no site da CVM (RAD/ENET) os documentos entregues nos últimos 2 dias.
  4. Envia no Telegram só os documentos que ainda não foram enviados.
  5. Guarda a lista do que já foi enviado em  estado/enviados.json.

Como rodar no seu computador (opcional, para testar):
    python robo.py --simular     -> mostra na tela o que enviaria, sem enviar nada
    python robo.py --teste       -> manda uma mensagem de teste no Telegram
    python robo.py               -> execução normal

Variáveis de ambiente (no GitHub ficam em Settings > Secrets):
    TELEGRAM_TOKEN      token do bot (vem do @BotFather)
    TELEGRAM_CHAT_ID    id do canal/grupo (ex.: -1001234567890)
    PLANILHA_URL        link da planilha do Google com a carteira
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import io
import json
import os
import re
import sys
import time
import zipfile
from base64 import b64encode
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

FUSO = ZoneInfo("America/Sao_Paulo")
ARQUIVO_ESTADO = Path(os.getenv("ARQUIVO_ESTADO", "estado/enviados.json"))
DIAS_DE_BUSCA = 2          # olha documentos entregues hoje e nos 2 dias anteriores
DIAS_DE_MEMORIA = 30       # esquece documentos enviados há mais de 30 dias
FALHAS_PARA_ALERTAR = 6    # 6 execuções seguidas falhando (~1h) -> avisa no Telegram

RAD = "https://www.rad.cvm.gov.br/ENET/"
URL_RAD_PAGINA = RAD + "frmConsultaExternaCVM.aspx"
URL_RAD_LISTAR = RAD + "frmConsultaExternaCVM.aspx/ListarDocumentos"
URL_RAD_DOWNLOAD = (
    RAD + "frmDownloadDocumento.aspx?Tela=ext&numSequencia={seq}&numVersao={ver}"
    "&numProtocolo={prot}&descTipo={tipo}&CodigoInstituicao=1"
)
URL_B3_EMPRESAS = (
    "https://sistemaswebb3-listados.b3.com.br/listedCompaniesProxy/"
    "CompanyCall/GetInitialCompanies/{param}"
)
URL_IPE_CSV = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/IPE/DADOS/ipe_cia_aberta_{ano}.zip"

NAVEGADOR = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9",
}


# --------------------------------------------------------------------------- #
# Utilidades
# --------------------------------------------------------------------------- #
def agora() -> dt.datetime:
    return dt.datetime.now(FUSO)


def log(msg: str) -> None:
    # Os logs do GitHub ficam visíveis se o repositório for público:
    # por isso o robô NUNCA escreve nomes de empresas ou tickers aqui, só contagens.
    print(f"[{agora():%d/%m %H:%M}] {msg}", flush=True)


def limpar_html(texto: str) -> str:
    texto = re.sub(r"<spanOrder>.*?</spanOrder>", "", texto or "", flags=re.S)
    texto = re.sub(r"<[^>]+>", " ", texto)
    return re.sub(r"\s+", " ", html.unescape(texto)).strip()


def so_digitos(texto) -> str:
    return re.sub(r"\D", "", str(texto or ""))


# --------------------------------------------------------------------------- #
# 1. Carteira (Google Planilhas)
# --------------------------------------------------------------------------- #
def link_csv_da_planilha(url: str) -> str:
    """Aceita o link normal de compartilhamento e transforma em link de CSV."""
    url = url.strip()
    if "output=csv" in url or "format=csv" in url:
        return url
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url)
    if not m:
        raise ValueError("PLANILHA_URL não parece ser um link do Google Planilhas.")
    gid = re.search(r"[#&?]gid=(\d+)", url)
    return (
        f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv"
        + (f"&gid={gid.group(1)}" if gid else "")
    )


def interpretar_carteira(texto_csv: str) -> list[dict]:
    """Colunas aceitas (maiúsc./minúsc. e acentos tanto faz):
    Ticker (obrigatória) | Codigo_CVM (opcional) | Ativo (opcional: SIM/NÃO)"""
    linhas = list(csv.reader(io.StringIO(texto_csv)))
    if not linhas:
        return []

    def norm(s):
        s = s.strip().lower()
        for a, b in (("ó", "o"), ("ô", "o"), ("í", "i"), ("ã", "a"), (" ", "_")):
            s = s.replace(a, b)
        return s

    cab = [norm(c) for c in linhas[0]]
    try:
        i_tk = cab.index("ticker")
    except ValueError:
        raise ValueError("A planilha precisa ter uma coluna chamada 'Ticker' na primeira linha.")
    i_cvm = next((i for i, c in enumerate(cab) if c in ("codigo_cvm", "cod_cvm", "cvm")), None)
    i_at = next((i for i, c in enumerate(cab) if c == "ativo"), None)

    carteira, vistos = [], set()
    for ln in linhas[1:]:
        if len(ln) <= i_tk:
            continue
        tk = ln[i_tk].strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{4}\d{1,2}", tk) or tk in vistos:
            continue
        if i_at is not None and len(ln) > i_at and norm(ln[i_at]) in ("nao", "n", "false", "0"):
            continue
        vistos.add(tk)
        cod = so_digitos(ln[i_cvm]) if i_cvm is not None and len(ln) > i_cvm else ""
        carteira.append({"ticker": tk, "codigo_cvm": cod.lstrip("0")})
    return carteira


def ler_carteira(sessao: requests.Session, url: str) -> list[dict]:
    r = sessao.get(link_csv_da_planilha(url), timeout=30)
    r.raise_for_status()
    if "<html" in r.text[:500].lower():
        raise RuntimeError(
            "O Google devolveu uma página em vez da planilha. Confira se a planilha está "
            "compartilhada como 'Qualquer pessoa com o link: Leitor'."
        )
    return interpretar_carteira(r.content.decode("utf-8-sig"))


# --------------------------------------------------------------------------- #
# 2. Ticker -> código CVM (via B3)
# --------------------------------------------------------------------------- #
def codigo_cvm_pela_b3(sessao: requests.Session, ticker: str) -> tuple[str, str]:
    raiz = re.sub(r"\d+$", "", ticker)
    param = b64encode(
        json.dumps(
            {"language": "pt-br", "pageNumber": 1, "pageSize": 20, "company": raiz}
        ).encode()
    ).decode()
    r = sessao.get(URL_B3_EMPRESAS.format(param=param), timeout=30)
    r.raise_for_status()
    for emp in r.json().get("results") or []:
        if str(emp.get("issuingCompany", "")).upper() == raiz:
            cod = so_digitos(emp.get("codeCVM")).lstrip("0")
            nome = emp.get("tradingName") or emp.get("companyName") or ""
            if cod:
                return cod, nome.strip()
    return "", ""


def completar_codigos(sessao, carteira, cache: dict) -> None:
    for emp in carteira:
        if emp["codigo_cvm"]:
            continue
        if emp["ticker"] in cache:
            emp["codigo_cvm"] = cache[emp["ticker"]]
            continue
        try:
            cod, _ = codigo_cvm_pela_b3(sessao, emp["ticker"])
        except Exception:
            cod = ""
        emp["codigo_cvm"] = cod
        if cod:
            cache[emp["ticker"]] = cod
        time.sleep(0.4)


# --------------------------------------------------------------------------- #
# 3. Documentos na CVM
# --------------------------------------------------------------------------- #
def interpretar_resposta_rad(dados: str) -> list[dict]:
    """A CVM devolve um texto: documentos separados por '$&&*', campos por '$&'.
    Campos: 0 cód CVM | 1 empresa | 2 categoria | 3 tipo | 4 espécie | 5 data ref.
            6 data entrega | 7 status | 8 versão | 9 modalidade | 10 ações (links)"""
    docs = []
    for bruto in (dados or "").split("$&&*"):
        c = bruto.split("$&")
        if len(c) < 11:
            continue
        acoes = c[10]
        m = re.search(r"OpenDownloadDocumentos\(([^)]*)\)", acoes)
        if not m:
            continue
        partes = [p.strip().strip("'\"") for p in m.group(1).split(",")]
        if len(partes) < 4:
            continue
        seq, ver, prot, tipo = partes[:4]
        link = URL_RAD_DOWNLOAD.format(seq=seq, ver=ver, prot=prot, tipo=tipo)
        ver_online = re.search(r"OpenPopUpVer\('([^']+)'\)", acoes)
        if ver_online and tipo.upper() != "IPE":
            # ITR, DFP, FRE etc.: a tela de visualização é melhor que o arquivo .zip
            link = RAD + ver_online.group(1)
        docs.append(
            {
                "chave": f"{prot or seq}-{ver}",
                "codigo_cvm": so_digitos(c[0]).lstrip("0"),
                "empresa": limpar_html(c[1]),
                "categoria": limpar_html(c[2]),
                "tipo": limpar_html(c[3]),
                "especie": limpar_html(c[4]),
                "assunto": "",
                "data_referencia": limpar_html(c[5]),
                "data_entrega": limpar_html(c[6]),
                "status": limpar_html(c[7]),
                "versao": ver,
                "link": link,
            }
        )
    return docs


def abrir_sessao_rad(sessao: requests.Session) -> None:
    # Abre a página de consulta uma vez para pegar os cookies, como um navegador faria.
    sessao.get(URL_RAD_PAGINA, timeout=30)


def buscar_rad(sessao: requests.Session, codigo_cvm: str, de: dt.date, ate: dt.date) -> list[dict]:
    corpo = {
        "dataDe": de.strftime("%d/%m/%Y"),
        "dataAte": ate.strftime("%d/%m/%Y"),
        "empresa": codigo_cvm.zfill(6),
        "setorAtividade": "-1",
        "categoriaEmissor": "-1",
        "situacaoEmissor": "-1",
        "tipoParticipante": "-1",
        "dataReferencia": "",
        "categoria": "EST_-1",      # todas as categorias
        "periodo": "2",             # 2 = intervalo de datas
        "horaIni": "",
        "horaFim": "",
        "palavraChave": "",
        "ultimaDtRef": "false",
        "tipoEmpresa": "0",
        "token": "",
        "versaoCaptcha": "",
    }
    r = sessao.post(
        URL_RAD_LISTAR,
        json=corpo,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": "https://www.rad.cvm.gov.br",
            "Referer": URL_RAD_PAGINA,
        },
        timeout=40,
    )
    r.raise_for_status()
    d = r.json().get("d") or {}
    if d.get("TemErro"):
        raise RuntimeError("CVM respondeu com erro")
    return interpretar_resposta_rad(d.get("dados") or "")


def interpretar_ipe_csv(texto: str, codigos: set[str], desde: dt.date) -> list[dict]:
    docs = []
    for ln in csv.DictReader(io.StringIO(texto), delimiter=";"):
        cod = so_digitos(ln.get("Codigo_CVM")).lstrip("0")
        if cod not in codigos:
            continue
        try:
            entrega = dt.date.fromisoformat((ln.get("Data_Entrega") or "")[:10])
        except ValueError:
            continue
        if entrega < desde:
            continue
        docs.append(
            {
                "chave": f"{ln.get('Protocolo_Entrega')}-{ln.get('Versao')}",
                "codigo_cvm": cod,
                "empresa": ln.get("Nome_Companhia", ""),
                "categoria": ln.get("Categoria", ""),
                "tipo": ln.get("Tipo", ""),
                "especie": ln.get("Especie", ""),
                "assunto": ln.get("Assunto", ""),
                "data_referencia": ln.get("Data_Referencia", ""),
                "data_entrega": entrega.strftime("%d/%m/%Y"),
                "status": "",
                "versao": ln.get("Versao", ""),
                "link": ln.get("Link_Download", ""),
            }
        )
    return docs


def buscar_ipe_dados_abertos(sessao, codigos: set[str], desde: dt.date) -> list[dict]:
    """Plano B: base de dados abertos da CVM (atualizada 1x por dia, só documentos IPE)."""
    r = sessao.get(URL_IPE_CSV.format(ano=agora().year), timeout=90)
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        nome = next(n for n in z.namelist() if n.endswith(".csv"))
        texto = z.read(nome).decode("latin-1")
    return interpretar_ipe_csv(texto, codigos, desde)


# --------------------------------------------------------------------------- #
# 4. Telegram
# --------------------------------------------------------------------------- #
def icone(doc: dict) -> str:
    t = f"{doc['categoria']} {doc['tipo']} {doc['especie']}".lower()
    if "fato relevante" in t:
        return "🚨"
    if "aviso aos acionistas" in t or "provento" in t or "dividend" in t or "juros sobre" in t:
        return "💰"
    if "recompra" in t or "valores mobili" in t or "negocia" in t:
        return "🔁"
    if "assembleia" in t:
        return "🗳️"
    if t.strip().startswith(("itr", "dfp")) or "resultado" in t or "release" in t:
        return "📊"
    if "comunicado" in t:
        return "📢"
    return "📄"


def montar_mensagem(doc: dict, ticker: str) -> str:
    e = html.escape
    titulo = " · ".join(
        dict.fromkeys(x for x in (doc["categoria"], doc["tipo"], doc["especie"]) if x)
    )
    linhas = [
        f"{icone(doc)} <b>{e(ticker)}</b> — {e(doc['empresa'])}",
        f"<b>{e(titulo or 'Documento')}</b>",
    ]
    if doc.get("assunto"):
        linhas.append(f"📝 {e(doc['assunto'])}")
    detalhes = [f"Entregue: {e(doc['data_entrega'])}"]
    if doc.get("data_referencia"):
        detalhes.append(f"Ref.: {e(doc['data_referencia'])}")
    if doc.get("versao") and doc["versao"] not in ("1", ""):
        detalhes.append(f"Versão {e(doc['versao'])} (reapresentação)")
    if doc.get("status") and doc["status"].lower() not in ("ativo", ""):
        detalhes.append(e(doc["status"]))
    linhas.append("🗓️ " + " | ".join(detalhes))
    if doc.get("link"):
        linhas.append(f'🔗 <a href="{e(doc["link"], quote=True)}">Abrir documento na CVM</a>')
    return "\n".join(linhas)


def enviar_telegram(sessao, texto: str, simular: bool = False) -> None:
    if simular:
        print("-" * 60 + "\n" + texto)
        return
    token, chat = os.environ["TELEGRAM_TOKEN"], os.environ["TELEGRAM_CHAT_ID"]
    for _ in range(5):
        r = sessao.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat,
                "text": texto,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=30,
        )
        if r.status_code == 429:  # Telegram pediu para esperar
            time.sleep(int(r.json().get("parameters", {}).get("retry_after", 5)) + 1)
            continue
        if not r.ok:
            raise RuntimeError(f"Telegram recusou a mensagem: {r.text[:200]}")
        time.sleep(3.1)  # limite do Telegram: ~20 mensagens por minuto num canal/grupo
        return
    raise RuntimeError("Telegram continuou pedindo para esperar.")


# --------------------------------------------------------------------------- #
# 5. Memória do que já foi enviado
# --------------------------------------------------------------------------- #
def carregar_estado() -> dict | None:
    if not ARQUIVO_ESTADO.exists():
        return None
    try:
        return json.loads(ARQUIVO_ESTADO.read_text(encoding="utf-8"))
    except Exception:
        return None


def salvar_estado(estado: dict) -> None:
    limite = (agora().date() - dt.timedelta(days=DIAS_DE_MEMORIA)).isoformat()
    estado["vistos"] = {k: v for k, v in estado["vistos"].items() if v >= limite}
    ARQUIVO_ESTADO.parent.mkdir(parents=True, exist_ok=True)
    ARQUIVO_ESTADO.write_text(json.dumps(estado, ensure_ascii=False, indent=1), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Programa principal
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--teste", action="store_true", help="manda mensagem de teste com a carteira lida")
    ap.add_argument("--simular", action="store_true", help="mostra na tela, não envia nada")
    args = ap.parse_args()

    sessao = requests.Session()
    sessao.headers.update(NAVEGADOR)

    estado = carregar_estado()
    primeira_vez = estado is None
    estado = estado or {"vistos": {}, "codigos": {}, "falhas_seguidas": 0, "alerta_enviado": False}
    estado.setdefault("codigos", {})

    carteira = ler_carteira(sessao, os.environ["PLANILHA_URL"])
    completar_codigos(sessao, carteira, estado["codigos"])
    monitoradas = [c for c in carteira if c["codigo_cvm"]]
    sem_codigo = [c["ticker"] for c in carteira if not c["codigo_cvm"]]
    log(f"Carteira: {len(carteira)} papéis, {len(monitoradas)} com código CVM.")

    if args.teste:
        lista = "\n".join(f"• {c['ticker']} → cód. CVM {c['codigo_cvm']}" for c in monitoradas)
        aviso = ""
        if sem_codigo:
            aviso = (
                "\n\n⚠️ Sem código CVM (não serão monitorados): "
                + ", ".join(sem_codigo)
                + "\nSe for empresa brasileira, preencha a coluna Codigo_CVM na planilha. "
                "BDRs normalmente não publicam na CVM."
            )
        enviar_telegram(
            sessao,
            f"✅ <b>Teste do robô CVM</b>\nEmpresas monitoradas:\n{html.escape(lista)}{html.escape(aviso)}",
            args.simular,
        )
        salvar_estado(estado)
        return 0

    hoje = agora().date()
    desde = hoje - dt.timedelta(days=DIAS_DE_BUSCA)
    ticker_por_codigo = {}
    for c in monitoradas:
        ticker_por_codigo.setdefault(c["codigo_cvm"], []).append(c["ticker"])

    documentos, falhas, plano_b_ok = [], [], False
    try:
        abrir_sessao_rad(sessao)
    except Exception:
        pass
    for cod in ticker_por_codigo:
        try:
            documentos += buscar_rad(sessao, cod, desde, hoje)
        except Exception:
            falhas.append(cod)
        time.sleep(0.6)

    if falhas:
        log(f"CVM (RAD) falhou para {len(falhas)} empresa(s); tentando a base de dados abertos.")
        try:
            documentos += buscar_ipe_dados_abertos(sessao, set(falhas), desde)
            plano_b_ok = True
        except Exception:
            plano_b_ok = False
    rad_fora = len(falhas) == len(ticker_por_codigo) and len(falhas) > 0

    # Alerta se a CVM ficar inacessível por muito tempo (e aviso quando voltar)
    if rad_fora:
        estado["falhas_seguidas"] = estado.get("falhas_seguidas", 0) + 1
        if estado["falhas_seguidas"] >= FALHAS_PARA_ALERTAR and not estado.get("alerta_enviado"):
            enviar_telegram(
                sessao,
                "⚠️ <b>Robô CVM</b>: há ~1 hora não consigo consultar o site da CVM. "
                "Sigo tentando e aviso quando normalizar."
                + ("" if not falhas or not plano_b_ok else "\n(Enquanto isso, uso a base diária de dados abertos.)"),
                args.simular,
            )
            estado["alerta_enviado"] = True
    else:
        if estado.get("alerta_enviado"):
            enviar_telegram(sessao, "✅ <b>Robô CVM</b>: acesso à CVM normalizado.", args.simular)
        estado["falhas_seguidas"], estado["alerta_enviado"] = 0, False

    # Tira duplicados e documentos já enviados
    novos, chaves = [], set()
    for d in documentos:
        if d["codigo_cvm"] not in ticker_por_codigo or d["chave"] in chaves:
            continue
        chaves.add(d["chave"])
        if d["chave"] not in estado["vistos"]:
            novos.append(d)

    def ordem(d):
        try:
            return dt.datetime.strptime(d["data_entrega"][:16], "%d/%m/%Y %H:%M")
        except ValueError:
            try:
                return dt.datetime.strptime(d["data_entrega"][:10], "%d/%m/%Y")
            except ValueError:
                return dt.datetime.min

    novos.sort(key=ordem)

    if primeira_vez:
        # Na primeira execução não despeja o histórico: só memoriza e avisa que ligou.
        for d in novos:
            estado["vistos"][d["chave"]] = hoje.isoformat()
        enviar_telegram(
            sessao,
            f"✅ <b>Robô CVM ligado.</b> Monitorando {len(monitoradas)} empresas da carteira. "
            "A partir de agora, todo documento novo aparece aqui.",
            args.simular,
        )
        log(f"Primeira execução: {len(novos)} documentos recentes memorizados sem enviar.")
    else:
        enviados = 0
        for d in novos:
            tickers = "/".join(ticker_por_codigo[d["codigo_cvm"]])
            enviar_telegram(sessao, montar_mensagem(d, tickers), args.simular)
            estado["vistos"][d["chave"]] = hoje.isoformat()
            salvar_estado(estado)  # salva a cada envio: se cair no meio, não repete
            enviados += 1
        log(f"{enviados} documento(s) novo(s) enviado(s).")

    salvar_estado(estado)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as erro:  # mensagem curta, sem dados da carteira
        texto = str(erro)
        if os.getenv("TELEGRAM_TOKEN"):
            texto = texto.replace(os.environ["TELEGRAM_TOKEN"], "***")
        log(f"ERRO: {type(erro).__name__}: {texto[:200]}")
        sys.exit(1)
