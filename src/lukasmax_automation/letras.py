"""A letra traduzida que aparece acima do titulo da musica.

Nos Reels montados no CapCut, cada verso da musica aparecia traduzido para o
portugues, em minusculas, uma frase por vez, logo acima do nome da musica. Este
modulo reproduz isso em tres passos:

1. ``buscar`` pega a letra sincronizada (formato LRC) no LRCLIB, uma base aberta
   e gratuita, e salva em ``data/letras/<artista>-<musica>.json``;
2. a traducao e preenchida no proprio arquivo -- pela sessao do Claude Code ou
   a mao. Fica num arquivo, e nao numa chamada de API no meio da montagem,
   pelo mesmo motivo das legendas de post: da para revisar antes de queimar no
   video, e a mesma musica serve para todos os videos gravados com ela;
3. ``gerar_ass`` converte os versos do trecho gravado em legendas ASS, que o
   ffmpeg queima com a libass.
"""

from __future__ import annotations

import json
import re
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .net import ssl_context

LRCLIB = "https://lrclib.net/api/search"

#: Medidas tiradas dos Reels antigos em 1080x1920. A legenda do CapCut quebrava
#: em ~29 caracteres ("ninguem mais pode tirar essas" cabia numa linha, "olhando
#: para a pagina em branco" nao), com as linhas coladas uma na outra.
FONTE = "Montserrat Medium"
TAMANHO = 84
MAX_CARACTERES = 29
ESPACO_LINHAS = 62
#: Centro vertical do bloco de texto, logo acima do titulo da musica. O bloco
#: inteiro e centralizado aqui: com uma linha so ela fica no meio de onde
#: estariam duas, e nao colada no titulo.
#:
#: Medido nos Reels antigos, do ultimo pixel da legenda ao topo do titulo:
#: ~41-46 px com duas linhas e ~61-76 px com uma. Em 1215 a legenda de duas
#: linhas ficava a 15 px do titulo, quase encostando; em 1193 fica a ~37 px e
#: a de uma linha a ~70 px.
CENTRO_Y = 1193
#: Linha de base = centro + metade da altura das letras minusculas, que e o que
#: o olho le como "meio" de uma linha em minusculas.
_MEIA_LINHA = 20

#: Palavras que so fazem sentido com a seguinte. Terminar a linha numa delas
#: parte a frase ("sou aquela / poderosa").
PALAVRAS_PRESAS = frozenset(
    """a o as os um uma uns umas de da do das dos em na no nas nos num numa pra pro
    para por pelo pela pelos pelas com sem e ou mas que se me te lhe meu minha meus
    minhas seu sua seus suas teu tua esse essa esses essas este esta aquele aquela
    não até como quando onde quem porque pois mais sou é tá está estou eu você ele
    ela the an my your
    and of to in on""".split()  # noqa: SIM905 -- lista de palavras, legivel e facil de editar
)
_PONTUACAO = tuple(",;:?!.")


def _custo_da_quebra(ultima: str, palavras_na_linha: int) -> float:
    """Custo extra de quebrar a linha depois de ``ultima``."""
    custo = 0.0
    # Sem tirar acento: "dá" (verbo) pode terminar a linha, "da" (de + a) nao.
    limpa = ultima.lower()
    if ultima.endswith(_PONTUACAO):
        custo -= 150  # quebra natural: uma frase em cima, outra embaixo
    elif limpa.strip("\"'") in PALAVRAS_PRESAS:
        custo += 400
    if palavras_na_linha == 1:
        custo += 80  # palavra sozinha numa linha
    return custo


#: Um verso fica na tela ate o proximo comecar, mas nao para sempre: num solo
#: instrumental sem linha vazia no LRC, a ultima frase ficaria parada por 20 s.
DURACAO_MAXIMA = 6.0

#: A legenda troca um pouco antes do verso comecar: quando a primeira palavra
#: sai, a frase ja esta na tela. Trocar no instante exato parece atrasado,
#: porque o olho ainda leva um tempo para achar o texto novo.
ANTECIPACAO = 0.1

_LINHA_LRC = re.compile(r"^\[(\d+):(\d+(?:\.\d+)?)\](.*)$")


class LetraError(RuntimeError):
    pass


def slug(artista: str, musica: str) -> str:
    texto = unicodedata.normalize("NFKD", f"{artista}-{musica}").encode("ascii", "ignore")
    return re.sub(r"[^a-z0-9]+", "-", texto.decode().lower()).strip("-")


def de_lrc(lrc: str) -> list[dict[str, Any]]:
    """Versos de um LRC, com a traducao ainda vazia.

    Linhas sem texto sao mantidas: no LRC elas marcam onde o verso anterior
    acaba, e sem elas uma frase ficaria na tela durante o instrumental.
    """
    linhas = []
    for bruta in lrc.splitlines():
        achou = _LINHA_LRC.match(bruta.strip())
        if achou:
            minutos, segundos, texto = achou.groups()
            linhas.append(
                {
                    "inicio": round(int(minutos) * 60 + float(segundos), 2),
                    "original": texto.strip(),
                    "traducao": "",
                }
            )
    return sorted(linhas, key=lambda linha: linha["inicio"])


def buscar(artista: str, musica: str) -> dict[str, Any]:
    """A primeira letra sincronizada que o LRCLIB devolve para a busca."""
    consulta = urllib.parse.urlencode({"artist_name": artista, "track_name": musica})
    pedido = urllib.request.Request(
        f"{LRCLIB}?{consulta}", headers={"User-Agent": "lukasmax-automation"}
    )
    with urllib.request.urlopen(pedido, context=ssl_context(), timeout=30) as resposta:
        resultados = json.load(resposta)
    sincronizadas = [item for item in resultados if item.get("syncedLyrics")]
    if not sincronizadas:
        raise LetraError(f"o LRCLIB nao tem letra sincronizada para {artista} - {musica}")
    escolhida = sincronizadas[0]
    return {
        "artista": escolhida["artistName"],
        "musica": escolhida["trackName"],
        "fonte": f"lrclib:{escolhida['id']}",
        "duracao": escolhida.get("duration"),
        "linhas": de_lrc(escolhida["syncedLyrics"]),
    }


def carregar(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def salvar(registro: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(registro, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def eventos(
    registro: dict[str, Any], *, inicio_musica: float, duracao: float
) -> list[tuple[float, float, str]]:
    """Os versos que caem no trecho gravado, em segundos do video montado.

    ``inicio_musica`` e o segundo da musica no primeiro quadro do video. Um verso
    sem traducao no trecho e erro: sair com uma frase faltando no meio do Reel
    e pior do que parar e pedir a traducao.

    ``ajuste`` no registro corrige um LRC inteiro que esta fora de tempo: o do
    "All Star" marca o primeiro verso 0,5 s depois de a voz comecar, e a legenda
    trocava sempre atrasada. Negativo adianta.

    O video sempre abre com legenda: se o primeiro verso so comeca depois do
    primeiro quadro, ele entra ja no quadro 0.
    """
    linhas = registro["linhas"]
    deslocamento = inicio_musica - registro.get("ajuste", 0.0) + ANTECIPACAO
    saida, faltando = [], []
    for indice, linha in enumerate(linhas):
        if not linha["original"]:
            continue
        proximo = linhas[indice + 1]["inicio"] if indice + 1 < len(linhas) else float("inf")
        comeco = linha["inicio"] - deslocamento
        fim = min(proximo - deslocamento, comeco + DURACAO_MAXIMA, duracao)
        if fim <= 0 or comeco >= duracao:
            continue
        texto = (linha.get("traducao") or "").strip()
        if not texto:
            faltando.append(linha["original"])
            continue
        saida.append((max(comeco, 0.0), fim, texto))
    if faltando:
        raise LetraError("versos sem traducao no trecho gravado: " + " / ".join(faltando))
    if saida:
        _, fim, texto = saida[0]
        saida[0] = (0.0, fim, texto)
    return saida


def quebrar(texto: str) -> list[str]:
    """Divide o verso em linhas equilibradas, sem partir a frase no lugar errado.

    Uma quebra gulosa (encher a linha e jogar o resto para baixo) deixava cinco
    palavras em cima e uma sozinha embaixo. Aqui o numero de linhas e o minimo
    que cabe em ``MAX_CARACTERES``, e entre as divisoes possiveis vence a de
    linhas mais parecidas, preferindo quebrar depois de pontuacao e nunca
    deixando no fim da linha uma palavra que so faz sentido com a seguinte
    ("a", "de", "que", "minha"...). Um ``|`` na traducao forca a quebra ali.
    """
    if "|" in texto:
        return [linha for parte in texto.split("|") for linha in quebrar(parte)]
    palavras = texto.split()
    if len(" ".join(palavras)) <= MAX_CARACTERES:
        return [" ".join(palavras)] if palavras else []

    def largura(i: int, j: int) -> int:
        return len(" ".join(palavras[i:j]))

    n = len(palavras)
    for total in range(2, n + 1):
        media = largura(0, n) / total
        # melhor[j][i]: menor custo pondo as i primeiras palavras em j linhas.
        infinito = float("inf")
        melhor = [[infinito] * (n + 1) for _ in range(total + 1)]
        corte = [[0] * (n + 1) for _ in range(total + 1)]
        melhor[0][0] = 0.0
        for j in range(1, total + 1):
            for i in range(j, n + 1):
                for k in range(j - 1, i):
                    if melhor[j - 1][k] == infinito:
                        continue
                    tamanho = largura(k, i)
                    if tamanho > MAX_CARACTERES and i - k > 1:
                        continue
                    custo = melhor[j - 1][k] + (tamanho - media) ** 2
                    if i < n:
                        custo += _custo_da_quebra(palavras[i - 1], i - k)
                    if custo < melhor[j][i]:
                        melhor[j][i], corte[j][i] = custo, k
        if melhor[total][n] < infinito:
            linhas, fim = [], n
            for j in range(total, 0, -1):
                inicio = corte[j][fim]
                linhas.append(" ".join(palavras[inicio:fim]))
                fim = inicio
            return linhas[::-1]
    return palavras


def _tempo(segundos: float) -> str:
    centesimos = round(segundos * 100)
    horas, resto = divmod(centesimos, 360000)
    minutos, resto = divmod(resto, 6000)
    return f"{horas}:{minutos:02d}:{resto // 100:02d}.{resto % 100:02d}"


def _escapar(texto: str) -> str:
    return texto.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def gerar_ass(eventos_: list[tuple[float, float, str]], largura: int, altura: int) -> str:
    """Legendas ASS com cada linha posicionada a mao.

    A libass nao deixa apertar o entrelinha, e o do CapCut e bem mais justo que
    o padrao. Por isso cada linha vira um evento proprio com ``\\pos``, ancorada
    pela base: a ultima linha fica sempre no mesmo lugar, colada no titulo.
    """
    cabecalho = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {largura}",
        f"PlayResY: {altura}",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Letra,{FONTE},{TAMANHO},&H00FFFFFF,&H00FFFFFF,&H00000000,&H90000000,"
        "0,0,0,0,100,100,0,0,1,0,2,2,0,0,0,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    dialogos = []
    for comeco, fim, texto in eventos_:
        linhas = quebrar(texto)
        for posicao, linha in enumerate(linhas):
            deslocamento = (posicao - (len(linhas) - 1) / 2) * ESPACO_LINHAS
            y = round(CENTRO_Y + _MEIA_LINHA + deslocamento)
            dialogos.append(
                f"Dialogue: 0,{_tempo(comeco)},{_tempo(fim)},Letra,,0,0,0,,"
                f"{{\\an2\\pos({largura // 2},{y})}}{_escapar(linha)}"
            )
    return "\n".join([*cabecalho, *dialogos]) + "\n"
