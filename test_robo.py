"""Testes offline (não acessam internet). Rodar:  python -m pytest testes -q"""
import json
import os
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import robo  # noqa: E402

LINHA_RAD = (
    "019100$&VALID SOLUÇÕES S.A.$&Fato Relevante$&$&$&<spanOrder>20260924</spanOrder>24/09/2026"
    "$&<spanOrder>202609241832</spanOrder>24/09/2026 18:32$&Ativo$&1$&AP - Apresentação"
    "$&<i class='fi-download' onclick=\"OpenDownloadDocumentos('1234567','1','987654','IPE')\"></i>"
    "<i onclick=\"OpenPopUpVer('frmExibirArquivoIPEExterno.aspx?NumeroProtocoloEntrega=987654')\"></i>"
)
LINHA_ITR = (
    "006211$&TUPY S.A.$&ITR$&$&$&30/06/2026$&13/08/2026 19:01$&Ativo$&2$&RE$&"
    "OpenDownloadDocumentos('555','2','444','ITR') OpenPopUpVer('frmGerenciaPaginaFRE.aspx?NumeroSequencialDocumento=555&CodigoTipoInstituicao=1')"
)


def test_interpreta_rad():
    docs = robo.interpretar_resposta_rad(LINHA_RAD + "$&&*" + LINHA_ITR + "$&&*")
    assert len(docs) == 2
    d = docs[0]
    assert d["codigo_cvm"] == "19100" and d["categoria"] == "Fato Relevante"
    assert d["data_entrega"] == "24/09/2026 18:32"
    assert d["chave"] == "987654-1"
    assert "numProtocolo=987654" in d["link"] and "descTipo=IPE" in d["link"]
    assert docs[1]["link"].endswith("CodigoTipoInstituicao=1")  # ITR abre tela de visualização


def test_link_planilha():
    u = "https://docs.google.com/spreadsheets/d/ABC_123/edit#gid=42"
    assert robo.link_csv_da_planilha(u) == (
        "https://docs.google.com/spreadsheets/d/ABC_123/export?format=csv&gid=42"
    )
    pub = "https://docs.google.com/spreadsheets/d/e/X/pub?output=csv"
    assert robo.link_csv_da_planilha(pub) == pub


def test_carteira():
    csv_txt = "Ticker,Código CVM,Ativo\nvlid3,019100,SIM\nTUPY3,,sim\nXXXX3,,NÃO\nlixo,,\nVLID3,,\n"
    c = robo.interpretar_carteira(csv_txt)
    assert c == [{"ticker": "VLID3", "codigo_cvm": "19100"}, {"ticker": "TUPY3", "codigo_cvm": ""}]


def test_ipe_csv():
    import datetime as dt
    txt = (
        "CNPJ_Companhia;Nome_Companhia;Codigo_CVM;Data_Referencia;Categoria;Tipo;Especie;Assunto;"
        "Data_Entrega;Tipo_Apresentacao;Protocolo_Entrega;Versao;Link_Download\n"
        "x;VALID;19100;2026-09-23;Comunicado ao Mercado;;;Recompra;2026-09-23;AP;111;1;http://l\n"
        "x;OUTRA;99999;2026-09-23;Fato Relevante;;;A;2026-09-23;AP;222;1;http://l\n"
    )
    d = robo.interpretar_ipe_csv(txt, {"19100"}, dt.date(2026, 9, 22))
    assert len(d) == 1 and d[0]["chave"] == "111-1" and d[0]["assunto"] == "Recompra"


def test_mensagem_escapa_html():
    doc = robo.interpretar_resposta_rad(LINHA_RAD)[0]
    doc["empresa"] = "A&B <S.A.>"
    m = robo.montar_mensagem(doc, "VLID3")
    assert "A&amp;B &lt;S.A.&gt;" in m and m.startswith("🚨 <b>VLID3</b>")


class Resp:
    def __init__(self, dados=None, texto="", ok=True):
        self._d, self.text, self.ok, self.status_code = dados, texto, ok, 200 if ok else 500
        self.content = texto.encode()

    def json(self):
        return self._d

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError("http")


def rodar(tmp_path, rad_ok=True):
    enviados = []
    planilha = "Ticker,Codigo_CVM\nVLID3,\nTXSA34,\n"

    def get(url, **k):
        if "docs.google.com" in url:
            return Resp(texto=planilha)
        if "b3.com.br" in url:
            import base64
            raiz = json.loads(base64.b64decode(url.rsplit("/", 1)[1]))["company"]
            res = [{"issuingCompany": "VLID", "codeCVM": "19100", "tradingName": "VALID"}] if raiz == "VLID" else []
            return Resp({"results": res})
        if "dados.cvm" in url:
            return Resp(ok=False)
        return Resp(texto="")

    def post(url, json=None, **k):
        if "ListarDocumentos" in url:
            if not rad_ok:
                return Resp(ok=False)
            return Resp({"d": {"TemErro": False, "dados": LINHA_RAD}})
        if "telegram" in url:
            enviados.append(json["text"])
            return Resp({"ok": True})

    sess = mock.MagicMock()
    sess.get.side_effect, sess.post.side_effect = get, post
    env = {"TELEGRAM_TOKEN": "t", "TELEGRAM_CHAT_ID": "1", "PLANILHA_URL": "https://docs.google.com/spreadsheets/d/A/edit"}
    with mock.patch.object(robo.requests, "Session", return_value=sess), \
         mock.patch.object(robo, "ARQUIVO_ESTADO", tmp_path / "estado.json"), \
         mock.patch.object(robo.time, "sleep"), mock.patch.dict(os.environ, env), \
         mock.patch.object(sys, "argv", ["robo.py"]):
        robo.main()
    return enviados


def test_fluxo_completo(tmp_path):
    # 1ª execução: só memoriza e avisa que ligou
    e1 = rodar(tmp_path)
    assert len(e1) == 1 and "Robô CVM ligado" in e1[0] and "1 empresas" in e1[0]
    # 2ª execução: o mesmo documento não é reenviado
    assert rodar(tmp_path) == []
    # documento novo aparece -> é enviado
    est = json.loads((tmp_path / "estado.json").read_text())
    est["vistos"] = {}
    (tmp_path / "estado.json").write_text(json.dumps(est))
    e3 = rodar(tmp_path)
    assert len(e3) == 1 and "VLID3" in e3[0] and "Fato Relevante" in e3[0]


def test_alerta_cvm_fora(tmp_path):
    rodar(tmp_path)  # liga
    msgs = []
    for _ in range(robo.FALHAS_PARA_ALERTAR + 2):
        msgs += rodar(tmp_path, rad_ok=False)
    assert sum("não consigo consultar" in m for m in msgs) == 1  # avisa uma vez só
    assert any("normalizado" in m for m in rodar(tmp_path))
