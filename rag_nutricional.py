"""
RAG Nutricional — NutriIA
=========================

Fluxo:
1. RECUPERAÇÃO: o texto que o usuário digitou é quebrado em trechos ("2 ovos", "arroz",
   "feijão"...) e cada trecho é buscado na base `dados_referencia/banco_de_alimentos_100g_estrito.csv`
   (busca por tokens normalizados + tolerância a erro de digitação). Os melhores candidatos
   viram o CONTEXTO enviado à IA.
2. GERAÇÃO: a IA só precisa INTERPRETAR — qual item da base corresponde a cada alimento,
   qual quantidade e qual medida caseira o usuário usou. Ela NÃO calcula calorias de itens
   que existem na base.
3. CÁLCULO DETERMINÍSTICO: o Python converte a medida caseira em gramas/ml usando
   `dados_referencia/medidas_caseiras.json` e faz a conta  valor_100g × gramas ÷ 100.
   Só quando o alimento realmente não existe na base é que os valores estimados pela IA são usados.
"""

import csv
import difflib
import json
import os
import re
import time
import unicodedata
from math import log
from typing import Callable, Dict, List, Optional, Tuple

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DADOS_DIR = os.path.join(BASE_DIR, "dados_referencia")
CSV_PATH = os.path.join(DADOS_DIR, "banco_de_alimentos_100g_estrito.csv")
MEDIDAS_PATH = os.path.join(DADOS_DIR, "medidas_caseiras.json")
CORRECOES_PATH = os.path.join(DADOS_DIR, "correcoes_base.csv")
# Alimentos que realmente têm 0 kcal (não são erro de dado)
ZERO_LEGITIMO = {"sal", "creatina", "agua", "zero", "diet", "adocante", "gelo"}

# ==================== NORMALIZAÇÃO ====================

STOPWORDS = {
    "de", "da", "do", "das", "dos", "com", "e", "a", "o", "as", "os", "em", "ao", "na", "no",
    "nas", "nos", "para", "pra", "um", "uma", "uns", "umas", "meu", "minha", "tipo", "por",
    "comi", "tomei", "bebi", "almocei", "jantei", "lanchei", "mais", "tambem", "pouco", "pouca",
    "porcao", "porcoes", "unidade", "unidades", "fatia", "fatias", "colher", "colheres",
    "xicara", "xicaras", "copo", "copos", "concha", "conchas", "grama", "gramas", "g",
    "ml", "kg", "litro", "litros", "lata", "latas", "garrafa", "pedaco", "pedacos",
    "escumadeira", "scoop", "medio", "media", "grande", "pequeno", "pequena", "cheia", "cheio",
    "rasa", "raso", "meia", "meio", "pegador", "pote", "caneca", "americano",
}

# Frases de medida caseira removidas da consulta antes da busca (para "sopa"/"chá" não virarem alimento)
FRASES_MEDIDA = re.compile(
    r"\bcolher(?:es)?(?:zinhas?)?\s+(?:de\s+)?(?:sopa|cha|cafe|sobremesa|servir|arroz)\b"
    r"|\bxicaras?\s+(?:de\s+)?cha\b|\bcopos?\s+(?:de\s+)?requeijao\b|\bponta\s+de\s+faca\b"
)

# Expansões de sinônimos comuns (termo digitado -> termos que existem na base)
SINONIMOS = {
    "mussarela": "mozarela", "mucarela": "mozarela", "muzarela": "mozarela", "mozzarella": "mozarela",
    "refri": "refrigerante", "coca": "refrigerante cola", "guarana": "refrigerante guarana",
    "aipim": "mandioca", "macaxeira": "mandioca", "mexerica": "tangerina", "bergamota": "tangerina",
    "contrafile": "contra file", "branco": "tipo1", "frances": "trigo frances", "cacetinho": "trigo frances",
    "pao de sal": "trigo frances", "forma": "trigo forma", "preto": "preto", "coado": "infusao",
    "cafezinho": "cafe infusao", "whey": "whey protein", "omelete": "omelete", "ovos": "ovo",
    "peito de peru": "peito peru", "sassami": "frango peito", "danone": "iogurte",
    "batata doce": "batata doce", "inglesa": "inglesa", "macarrao": "macarrao", "espaguete": "macarrao",
    "miojo": "macarrao instantaneo", "requeijao": "requeijao", "chocolate": "chocolate", "leite": "vaca",
    "patinho": "bovina patinho", "alcatra": "bovina alcatra", "maminha": "bovina maminha",
    "acem": "bovina acem", "tilapia": "peixe tilapia", "salmao": "peixe salmao",
    "azeite": "azeite oliva", "manteiga": "manteiga", "tapioca": "mandioca farinha",
    "nescau": "achocolatado po", "toddy": "achocolatado po", "cuscuz": "milho cuscuz",
}


def normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode("ascii")
    return texto.lower().strip()


def singular(p: str) -> str:
    if len(p) <= 3:
        return p
    if p.endswith("oes") or p.endswith("aes"):
        return p[:-3] + "ao"
    if p.endswith("res") or p.endswith("zes") or p.endswith("les"):
        return p[:-2]
    if p.endswith("is") and len(p) > 4:
        return p[:-2] + "l"
    if p.endswith("s") and not p.endswith("ss"):
        return p[:-1]
    return p


def tokenizar(texto: str, remover_stop: bool = True) -> List[str]:
    texto_n = re.sub(r"\btipo\s+(\d)", r"tipo\1", normalizar(texto))
    palavras = re.split(r"[^a-z0-9]+", texto_n)
    out = []
    for p in palavras:
        if not p:
            continue
        p = singular(p)
        if remover_stop and (p in STOPWORDS or p.isdigit()):
            continue
        out.append(p)
    return out


# ==================== BASE DE ALIMENTOS ====================

class BaseAlimentos:
    def __init__(self, caminho_csv: str = CSV_PATH):
        self.itens: List[Dict] = []
        self.por_nome_norm: Dict[str, Dict] = {}
        self.indice: Dict[str, List[int]] = {}
        self.idf: Dict[str, float] = {}
        self.vocab: List[str] = []
        self._ultima_sync = 0.0
        self._carregar(caminho_csv)

    @staticmethod
    def _ler_csv(caminho: str) -> List[Dict]:
        linhas = []
        if not os.path.exists(caminho):
            return linhas
        with open(caminho, encoding="utf-8-sig", newline="") as f:
            leitor = csv.reader(f)
            next(leitor, None)  # cabeçalho
            for linha in leitor:
                if len(linha) < 5 or not linha[0].strip():
                    continue
                try:
                    linhas.append({
                        "nome": linha[0].strip(),
                        "kcal": float(linha[1] or 0),
                        "prot": float(linha[2] or 0),
                        "gord": float(linha[3] or 0),
                        "carb": float(linha[4] or 0),
                    })
                except ValueError:
                    continue
        return linhas

    def _carregar(self, caminho: str):
        if not os.path.exists(caminho):
            print(f"[RAG] AVISO: base de alimentos não encontrada em {caminho}")
            return

        # Correções manuais sobrescrevem linhas da base principal com o mesmo nome
        correcoes = {normalizar(c["nome"]): c for c in self._ler_csv(CORRECOES_PATH)}
        descartados = []

        for item in self._ler_csv(caminho):
            item = correcoes.pop(normalizar(item["nome"]), item)
            zerado = item["kcal"] == 0 and item["prot"] == 0 and item["gord"] == 0 and item["carb"] == 0
            if zerado and not (set(tokenizar(item["nome"], remover_stop=False)) & ZERO_LEGITIMO):
                descartados.append(item["nome"])  # dado faltante na tabela (ex: "NA" da TACO)
                continue
            self._adicionar(item)
        for extra in correcoes.values():  # itens novos que só existem nas correções
            self._adicionar(extra)

        n = max(len(self.itens), 1)
        self.idf = {t: log(1 + n / len(ids)) for t, ids in self.indice.items()}
        self.vocab = list(self.indice.keys())
        print(f"[RAG] Base de alimentos carregada: {len(self.itens)} itens, {len(self.vocab)} termos.")
        if descartados:
            print(f"[RAG] Ignorados por estarem zerados (corrija em correcoes_base.csv): {descartados}")

    def _adicionar(self, item: Dict):
        _toks = tokenizar(item["nome"], remover_stop=False)
        item["tokens"] = set(_toks) - {"de", "da", "do", "com", "e"}
        item["primeiro"] = _toks[0] if _toks else ""
        idx = len(self.itens)
        self.itens.append(item)
        self.por_nome_norm[normalizar(item["nome"])] = item
        for t in item["tokens"]:
            self.indice.setdefault(t, []).append(idx)

    def aprender_alimento(self, nome: str, kcal: float, prot: float, gord: float, carb: float) -> Dict:
        """Adiciona um alimento personalizado à base em memória e persiste em correcoes_base.csv."""
        existente = self.por_nome_norm.get(normalizar(nome))
        if existente is not None:  # já aprendido: atualiza os valores em vez de duplicar no índice
            existente.update({"kcal": float(kcal), "prot": float(prot), "gord": float(gord), "carb": float(carb)})
            return existente
        item = {
            "nome": nome.strip(),
            "kcal": float(kcal),
            "prot": float(prot),
            "gord": float(gord),
            "carb": float(carb)
        }
        self._adicionar(item)
        n = max(len(self.itens), 1)
        self.idf = {t: log(1 + n / len(ids)) for t, ids in self.indice.items()}
        self.vocab = list(self.indice.keys())

        # Persistir em correcoes_base.csv para manter o aprendizado
        try:
            arquivo_existe = os.path.exists(CORRECOES_PATH)
            with open(CORRECOES_PATH, "a", encoding="utf-8-sig", newline="") as f:
                escritor = csv.writer(f)
                if not arquivo_existe or os.path.getsize(CORRECOES_PATH) == 0:
                    escritor.writerow(["Alimento", "Calorias (kcal)", "Proteínas (g)", "Gorduras (g)", "Carboidratos (g)"])
                escritor.writerow([item["nome"], item["kcal"], item["prot"], item["gord"], item["carb"]])
        except Exception as e:
            print(f"[RAG] Aviso ao salvar em correcoes_base.csv: {e}")

        print(f"[RAG] Novo alimento aprendido e indexado: {item['nome']} ({item['kcal']} kcal/100g)")
        return item

    def sincronizar_aprendidos(self, get_db: Callable, intervalo: float = 300.0) -> int:
        """Carrega do banco (Supabase) os alimentos ensinados pelos usuários (fonte 'Usuário').

        O CSV é efêmero em ambiente serverless; o banco é a fonte de verdade do aprendizado.
        Limitado por `intervalo` segundos para não consultar o banco a cada pedido.
        Retorna quantos alimentos novos entraram na base em memória.
        """
        agora = time.time()
        if agora - self._ultima_sync < intervalo:
            return 0
        self._ultima_sync = agora
        novos = 0
        conn = get_db()
        try:
            linhas = conn.execute(
                "SELECT nome, kcal_100g, proteinas_100g, gorduras_100g, carboidratos_100g "
                "FROM alimentos WHERE fonte = ?", ("Usuário",)
            ).fetchall()
        finally:
            conn.close()
        for r in linhas:
            nome = (r["nome"] or "").strip()
            if not nome:
                continue
            valores = {"kcal": float(r["kcal_100g"] or 0), "prot": float(r["proteinas_100g"] or 0),
                       "gord": float(r["gorduras_100g"] or 0), "carb": float(r["carboidratos_100g"] or 0)}
            existente = self.por_nome_norm.get(normalizar(nome))
            if existente is not None:
                existente.update(valores)
                continue
            self._adicionar({"nome": nome, **valores})
            novos += 1
        if novos:
            n = max(len(self.itens), 1)
            self.idf = {t: log(1 + n / len(ids)) for t, ids in self.indice.items()}
            self.vocab = list(self.indice.keys())
            print(f"[RAG] {novos} alimento(s) aprendido(s) carregado(s) do banco.")
        return novos

    # ---------- recuperação ----------
    def _expandir(self, texto: str) -> str:
        t = FRASES_MEDIDA.sub(" ", normalizar(texto))
        extras = []
        for termo, exp in SINONIMOS.items():
            if re.search(rf"\b{re.escape(termo)}\b", t):
                extras.append(exp)
        return t + " " + " ".join(extras)

    def buscar(self, texto: str, k: int = 10) -> List[Tuple[float, Dict]]:
        if not self.itens:
            return []
        tokens_q = list(dict.fromkeys(tokenizar(self._expandir(texto))))
        if not tokens_q:
            return []

        pontos: Dict[int, float] = {}
        casados: Dict[int, set] = {}
        for i, q in enumerate(tokens_q):
            peso_pos = 1.25 if i == 0 else 1.0  # o primeiro termo costuma ser o alimento principal
            alvos: Dict[str, float] = {}
            if q in self.indice:
                alvos[q] = 1.0
            if len(q) >= 4:
                for v in self.vocab:
                    if v == q or len(v) < 4:
                        continue
                    # "frang" -> "frango" (prefixo digitado) ou "integrai" -> "integral" (quase igual)
                    if v.startswith(q) or (q.startswith(v) and len(v) >= len(q) - 2):
                        alvos[v] = max(alvos.get(v, 0), 0.8)
            if q not in self.indice and len(q) >= 4:
                for v in difflib.get_close_matches(q, self.vocab, n=4, cutoff=0.8):
                    alvos[v] = max(alvos.get(v, 0), 0.75)
            for v, w in alvos.items():
                ganho = w * self.idf.get(v, 1.0) * peso_pos
                for idx in self.indice[v]:
                    pontos[idx] = pontos.get(idx, 0) + ganho
                    casados.setdefault(idx, set()).add(q)

        resultado = []
        cabeca = tokens_q[0]
        usuario_pediu_cru = any(t in tokens_q for t in ("cru", "natura", "seco"))
        graos_e_carnes = {"arroz", "feijao", "macarrao", "lentilha", "batata", "mandioca", "frango", "carne", "peixe", "bovina", "suina"}

        for idx, p in pontos.items():
            item = self.itens[idx]
            sobras = len(item["tokens"]) - len(casados[idx])
            p = p - 0.08 * max(sobras, 0)  # prefere nomes mais "enxutos"
            if item["primeiro"] == cabeca:
                p += 1.5  # "Leite de vaca" > "Doce de leite" quando a busca é "leite"

            # Priorização clínica de Cozido vs Cru para evitar distorções calóricas graves
            if item["tokens"] & graos_e_carnes:
                if not usuario_pediu_cru:
                    if "cru" in item["tokens"]:
                        p -= 3.5  # penaliza cru
                    if any(c in item["tokens"] for c in ("cozido", "grelhado", "assado", "refogado")):
                        p += 2.0  # bonifica preparado
                else:
                    if "cru" in item["tokens"]:
                        p += 2.5

            resultado.append((p, item))
        resultado.sort(key=lambda x: -x[0])
        return resultado[:k]

    def obter(self, nome: str) -> Optional[Dict]:
        if not nome:
            return None
        n = normalizar(nome)
        if n in self.por_nome_norm:
            return self.por_nome_norm[n]
        proximos = difflib.get_close_matches(n, list(self.por_nome_norm.keys()), n=1, cutoff=0.88)
        return self.por_nome_norm[proximos[0]] if proximos else None


# ==================== MEDIDAS CASEIRAS ====================

CANON_TAMANHO = {
    "media": "medio", "medio": "medio", "pequena": "pequeno", "pequeno": "pequeno",
    "grande": "grande", "cheia": "cheio", "cheio": "cheio", "rasa": "raso", "raso": "raso",
}
TAMANHOS = {"pequeno": 0.7, "medio": 1.0, "grande": 1.3}
ALIASES_UNIDADE = {
    "un": "unidade", "und": "unidade", "unid": "unidade", "uni": "unidade", "cs": "colher sopa",
    "cc": "colher cha", "csp": "colher sopa", "colherzinha": "colher cha", "xic": "xicara",
    "xicrinha": "xicrinha", "gramas": "g", "gr": "g", "mls": "ml", "lt": "l", "ltr": "l",
    "file": "file", "longneck": "long neck", "sache": "sache", "sachet": "sache",
}


def _tokens_unidade(texto: str) -> List[str]:
    out = []
    for p in re.split(r"[^a-z0-9]+", normalizar(texto)):
        if not p or p == "de":
            continue
        p = ALIASES_UNIDADE.get(p, p)
        for sub in p.split():
            sub = singular(sub) if sub not in ("l", "g", "ml", "kg", "mg", "dl") else sub
            sub = CANON_TAMANHO.get(sub, sub)
            out.append(sub)
    return out


class TabelaMedidas:
    def __init__(self, caminho: str = MEDIDAS_PATH):
        with open(caminho, encoding="utf-8") as f:
            dados = json.load(f)
        self.absolutas = {normalizar(k): float(v) for k, v in dados.get("unidades_absolutas", {}).items()}
        self.genericas = self._preparar(dados.get("genericas", {}))
        self.especificas = []
        for regra in dados.get("especificas", []):
            self.especificas.append({
                "contem": [singular(normalizar(p)) for p in regra.get("contem", [])],
                "medidas": self._preparar(regra.get("medidas", {})),
                "medidas_raw": regra.get("medidas", {}),
            })

    @staticmethod
    def _preparar(medidas: Dict[str, float]) -> List[Tuple[set, float, str]]:
        return [(set(_tokens_unidade(k)), float(v), k) for k, v in medidas.items()]

    @staticmethod
    def _casar(tokens_u: set, medidas: List[Tuple[set, float, str]]) -> Optional[Tuple[int, float, str, set]]:
        melhor = None
        for chave_tokens, gramas, nome in medidas:
            if chave_tokens and chave_tokens.issubset(tokens_u):
                score = len(chave_tokens)
                if melhor is None or score > melhor[0]:
                    melhor = (score, gramas, nome, chave_tokens)
        return melhor

    def regras_para(self, nome_alimento: str) -> List[Dict]:
        tokens = set(tokenizar(nome_alimento, remover_stop=False))
        return [r for r in self.especificas if all(c in tokens for c in r["contem"])]

    def resolver(self, nome_alimento: str, quantidade: float, unidade: str) -> Tuple[Optional[float], str, str]:
        """Retorna (gramas_totais | None, metodo, descricao_da_medida)."""
        unidade = (unidade or "").strip()

        # "200g", "1,5 l" dentro do campo de unidade
        m = re.match(r"^\s*(\d+(?:[.,]\d+)?)\s*([a-zA-Z]+)\s*$", unidade)
        if m:
            quantidade = quantidade * float(m.group(1).replace(",", "."))
            unidade = m.group(2)

        tokens = _tokens_unidade(unidade)
        tokens_set = set(tokens)

        # 1) Unidades absolutas (g, ml, kg, l)
        if len(tokens) >= 1 and tokens[0] in self.absolutas and len(tokens) <= 2:
            return quantidade * self.absolutas[tokens[0]], "absoluta", unidade

        # "colher" sem tipo => colher de sopa
        if "colher" in tokens_set and not tokens_set & {"sopa", "cha", "cafe", "sobremesa", "servir", "arroz"}:
            tokens_set.add("sopa")

        fator = 1.0
        if "raso" in tokens_set:
            fator *= 0.6

        # 2) Regras específicas do alimento
        melhor_especifica = None
        for regra in self.regras_para(nome_alimento):
            r = self._casar(tokens_set, regra["medidas"])
            if r and (melhor_especifica is None or r[0] > melhor_especifica[0]):
                melhor_especifica = r
        melhor_generica = self._casar(tokens_set, self.genericas)

        escolhido, metodo = None, None
        if melhor_especifica and (not melhor_generica or melhor_especifica[0] >= melhor_generica[0]):
            escolhido, metodo = melhor_especifica, "especifica"
        elif melhor_generica:
            escolhido, metodo = melhor_generica, "generica"

        if escolhido:
            _, gramas, nome_medida, chave_tokens = escolhido
            tamanho = next((t for t in tokens_set if t in TAMANHOS), None)
            if tamanho and not (chave_tokens & set(TAMANHOS)):
                fator *= TAMANHOS[tamanho]
            return quantidade * gramas * fator, metodo, nome_medida

        return None, "desconhecida", unidade

    def texto_referencia(self) -> str:
        """Resumo compacto das medidas para enviar como contexto à IA."""
        gen = ", ".join(f"{k}={int(v) if float(v).is_integer() else v}" for k, v in
                        ((n, g) for _, g, n in self.genericas))
        return f"Medidas genéricas (g ou ml): {gen}."

    def texto_especificas_para(self, nomes: List[str]) -> str:
        linhas, vistos = [], set()
        for nome in nomes:
            for regra in self.regras_para(nome):
                chave = tuple(regra["contem"])
                if chave in vistos:
                    continue
                vistos.add(chave)
                med = ", ".join(f"{k}={v}" for k, v in regra["medidas_raw"].items())
                linhas.append(f"- alimentos com '{' '.join(regra['contem'])}': {med}")
        return "\n".join(linhas)


# ==================== INSTÂNCIAS ====================

base_alimentos = BaseAlimentos()
try:
    tabela_medidas = TabelaMedidas()
except Exception as e:  # pragma: no cover
    print(f"[RAG] Falha ao carregar medidas caseiras: {e}")
    tabela_medidas = None


# ==================== RECUPERAÇÃO DE CONTEXTO ====================

SEPARADORES = re.compile(r"[,;\n+/]|\s(?:e|com|mais|acompanhado de|junto com)\s", re.IGNORECASE)


def segmentar(descricao: str) -> List[str]:
    partes = [p.strip() for p in SEPARADORES.split(descricao or "") if p and p.strip()]
    return partes or ([descricao] if descricao else [])


def recuperar_candidatos(textos: List[str], por_trecho: int = 12, limite: int = 90) -> List[Dict]:
    vistos, candidatos = set(), []
    consultas = []
    for t in textos:
        consultas.extend(segmentar(t))
        consultas.append(t)  # a frase inteira também ajuda em pratos compostos
    for q in consultas:
        for _, item in base_alimentos.buscar(q, k=por_trecho):
            if item["nome"] not in vistos:
                vistos.add(item["nome"])
                candidatos.append(item)
            if len(candidatos) >= limite:
                return candidatos
    return candidatos


def formatar_candidatos(candidatos: List[Dict]) -> str:
    return "\n".join(
        f"- {c['nome']} | {c['kcal']:g} kcal | P {c['prot']:g} g | C {c['carb']:g} g | G {c['gord']:g} g"
        for c in candidatos
    )


# ==================== PIPELINE PRINCIPAL ====================

def _num(v, padrao=0.0) -> float:
    try:
        if isinstance(v, str):
            v = v.replace(",", ".").strip()
            frac = re.match(r"^(\d+)\s*/\s*(\d+)$", v)
            if frac:
                return float(frac.group(1)) / float(frac.group(2))
            if normalizar(v) in ("meia", "meio"):
                return 0.5
        return float(v)
    except (TypeError, ValueError):
        return padrao


SCHEMA_IDENTIFICAR = {
    "type": "OBJECT",
    "properties": {
        "alimentos": {"type": "ARRAY", "items": {"type": "STRING"},
                      "description": "Nome + quantidade aparente de cada alimento visível. Ex: 'arroz branco 4 colheres de sopa'"}
    },
    "required": ["alimentos"],
}

SCHEMA_RAG = {
    "type": "OBJECT",
    "properties": {
        "itens": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "alimento_informado": {"type": "STRING", "description": "Como o alimento foi descrito no texto"},
                    "alimento_base": {"type": "STRING", "description": "Nome EXATO copiado da BASE DE REFERÊNCIA, ou 'NAO_ENCONTRADO'"},
                    "quantidade": {"type": "NUMBER", "description": "Quantidade numérica (ex: 2, 0.5, 150)"},
                    "unidade": {"type": "STRING", "description": "Unidade/medida como informada: g, ml, colher de sopa, concha, unidade, fatia, copo grande..."},
                    "gramas_estimados": {"type": "NUMBER", "description": "Peso/volume TOTAL estimado em g ou ml para esta quantidade"},
                    "kcal_100g": {"type": "NUMBER", "description": "Somente se NAO_ENCONTRADO: kcal por 100 g/ml"},
                    "prot_100g": {"type": "NUMBER", "description": "Somente se NAO_ENCONTRADO"},
                    "carb_100g": {"type": "NUMBER", "description": "Somente se NAO_ENCONTRADO"},
                    "gord_100g": {"type": "NUMBER", "description": "Somente se NAO_ENCONTRADO"},
                },
                "required": ["alimento_informado", "alimento_base", "quantidade", "unidade", "gramas_estimados"],
            },
        },
        "fibras_g": {"type": "NUMBER", "description": "Estimativa total de fibras da refeição"},
        "dica_ia": {"type": "STRING", "description": "Uma ou duas dicas cirúrgicas (máx 3 frases), SEM citar números de calorias"},
        "qualidade_vegetais": {"type": "STRING", "description": "boa, razoavel, ou ruim"},
        "qualidade_proteinas": {"type": "STRING", "description": "boa, razoavel, ou ruim"},
        "qualidade_fibras": {"type": "STRING", "description": "boa, razoavel, ou ruim"},
        "qualidade_acucar": {"type": "STRING", "description": "boa, razoavel, ou ruim"},
        "qualidade_ultraprocessados": {"type": "STRING", "description": "boa, razoavel, ou ruim"},
    },
    "required": ["itens", "fibras_g", "dica_ia"],
}


def _system_instruction(usuario: Dict) -> str:
    return f"""Você é a NutriIA, nutricionista clínica e esportiva de alta precisão. Paciente: {usuario.get('nome')} (sexo: {usuario.get('sexo')}).
Dirija-se ao paciente pelo nome na dica, com tom técnico, acolhedor e sem brincadeiras.

Sua tarefa NÃO é calcular calorias totais. Sua tarefa é INTERPRETAR a refeição e mapear cada alimento para a BASE DE REFERÊNCIA fornecida.
O sistema Python fará todos os cálculos matemáticos a partir da base (valores por 100 g ou 100 ml) e da tabela de medidas caseiras.

DIRETRIZES CLÍNICAS OBRIGATÓRIAS:
1. SEPARAÇÃO & DECOMPOSIÇÃO:
   - Separe a refeição em alimentos individuais.
   - Para pratos compostos (ex: "strogonoff de frango", "omelete de queijo", "feijoada", "sanduíche natural", "lasanha"), DECOMPONHA nos ingredientes base disponíveis na referência (ex: frango cozido + molho de tomate + creme de leite) com pesos plausíveis para a porção descrita.
   - Se o alimento foi frito, mexido ou refogado (ex: "2 ovos mexidos", "frango frito"), e a base do alimento não inclui gordura, inclua o item de gordura de preparo (ex: "Óleo de soja" ou "Manteiga", 1 colher de chá ou sopa conforme o caso).

2. CRU vs. COZIDO (MUITO IMPORTANTE):
   - NUNCA selecione alimentos na versão CRUA para arroz, feijão, macarrão, batata, mandioca ou carnes, a menos que o usuário tenha explicitamente dito "cru" ou "em grão cru".
   - 99% das pessoas pesam e informam alimentos prontos no prato. Arroz comum é SEMPRE "Arroz tipo 1 cozido" (~128 kcal/100g, NUNCA o cru de 360 kcal). Feijão é SEMPRE "Feijão carioca cozido" (~76 kcal/100g). Carnes e frango são cozidos/grelhados/assados. Frutas e saladas são cruas.

3. MAPEAMENTO PARA A BASE:
   - Em "alimento_base", copie o nome EXATO do item correspondente na BASE DE REFERÊNCIA fornecida.
   - "Arroz branco" = "Arroz tipo 1 cozido". "Pão francês" = "Pão trigo francês". "Pão de forma" = "Pão trigo forma".
   - Se NÃO existir correspondência razoável na base, use "NAO_ENCONTRADO" e preencha kcal_100g, prot_100g, carb_100g, gord_100g com valores fidedignos de referência TACO/USDA/rótulo (por 100 g ou 100 ml).

4. QUANTIDADE E PESO ESTIMADO:
   - "quantidade" e "unidade": copie o que o usuário disse (ex: 2 + "colher de sopa", 1 + "copo grande", 150 + "g", 1 + "concha", 2 + "unidade", 1 + "fatia"). Se não informado, use 1 porção usual e unidade "porcao".
   - "gramas_estimados": peso/volume TOTAL plausível em g ou ml, consultando a TABELA DE MEDIDAS CASEIRAS. Se a unidade for g ou ml, repita o valor. Seja conservador e realista.

5. DICA CLÍNICA:
   - A dica não deve citar números de calorias (o sistema calcula e exibe os totais). Foque em qualidade nutricional, micronutrientes, saciedade e hidratação."""


def analisar_refeicao_rag(
    descricao: str,
    tipo: str,
    usuario: Dict,
    chamar_ia: Callable[..., Dict],
    foto_base64: Optional[str] = None,
) -> Dict:
    descricao = (descricao or "").strip()
    textos_busca = [descricao] if descricao else []

    # Passo 0 (apenas com foto): identificar alimentos para poder buscar na base
    if foto_base64:
        try:
            ident = chamar_ia(
                prompt=f"Liste os alimentos visíveis na foto com a quantidade aparente. Observação do usuário: {descricao or 'nenhuma'}",
                schema=SCHEMA_IDENTIFICAR,
                system_instruction="Você identifica alimentos em fotos de refeições com precisão. Não invente itens que não aparecem.",
                imagem_base64=foto_base64,
            )
            textos_busca.extend([str(a) for a in ident.get("alimentos", []) if a])
        except Exception as e:
            print(f"[RAG] Falha na identificação por foto: {e}")

    # Passo 1: recuperação
    candidatos = recuperar_candidatos(textos_busca)
    nomes_cand = [c["nome"] for c in candidatos]
    medidas_txt = tabela_medidas.texto_referencia() if tabela_medidas else ""
    especificas_txt = tabela_medidas.texto_especificas_para(nomes_cand) if tabela_medidas else ""

    prompt = f"""REFEIÇÃO ({tipo}): {descricao or '(ver foto)'}

=== BASE DE REFERÊNCIA (valores por 100 g ou 100 ml) — itens recuperados para esta refeição ===
{formatar_candidatos(candidatos) or '(nenhum item encontrado na base)'}

=== TABELA DE MEDIDAS CASEIRAS ===
{medidas_txt}
Medidas específicas dos alimentos acima:
{especificas_txt or '(nenhuma)'}

Mapeie cada alimento da refeição para a base e informe quantidade, unidade e gramas_estimados."""

    # Passo 2: geração (interpretação)
    data = chamar_ia(
        prompt=prompt,
        schema=SCHEMA_RAG,
        system_instruction=_system_instruction(usuario),
        imagem_base64=foto_base64,
    )

    # Passo 3: cálculo determinístico
    itens_out = []
    tot = {"kcal": 0.0, "prot": 0.0, "carb": 0.0, "gord": 0.0}
    niveis = []

    for it in data.get("itens", []) or []:
        nome_usuario = str(it.get("alimento_informado") or it.get("alimento_base") or "Alimento")
        quantidade = _num(it.get("quantidade"), 1.0) or 1.0
        unidade = str(it.get("unidade") or "porcao")
        gramas_ia = _num(it.get("gramas_estimados"), 0.0)

        ref = None
        alimento_base = str(it.get("alimento_base") or "")
        if alimento_base and alimento_base.upper() != "NAO_ENCONTRADO":
            ref = base_alimentos.obter(alimento_base)

        nome_para_medida = ref["nome"] if ref else nome_usuario
        gramas, metodo, medida_nome = (None, "desconhecida", unidade)
        if tabela_medidas:
            gramas, metodo, medida_nome = tabela_medidas.resolver(nome_para_medida, quantidade, unidade)
        if gramas is None:
            gramas = gramas_ia if gramas_ia > 0 else 100.0 * quantidade
            metodo = "estimativa_ia" if gramas_ia > 0 else "padrao"

        if ref:
            por100 = {"kcal": ref["kcal"], "prot": ref["prot"], "carb": ref["carb"], "gord": ref["gord"]}
            fonte = "Base de referência"
        else:
            por100 = {
                "kcal": _num(it.get("kcal_100g")), "prot": _num(it.get("prot_100g")),
                "carb": _num(it.get("carb_100g")), "gord": _num(it.get("gord_100g")),
            }
            fonte = "Estimativa IA (fora da base)"

        fator = gramas / 100.0
        valores = {k: round(v * fator, 1) for k, v in por100.items()}

        if ref and metodo in ("absoluta", "especifica"):
            conf = "Alta"
        elif ref:
            conf = "Média"
        else:
            conf = "Baixa"
        niveis.append(conf)

        qtd_txt = f"{quantidade:g}"
        itens_out.append({
            "nome": ref["nome"] if ref else nome_usuario,
            "nome_original": nome_usuario,
            "quantidade_original": qtd_txt,
            "unidade_original": unidade,
            "peso_g": round(gramas, 1),
            "kcal": valores["kcal"],
            "prot": valores["prot"],
            "carb": valores["carb"],
            "gord": valores["gord"],
            "confianca": conf,
            "fonte": fonte,
            "metodo_medida": metodo,
        })
        for k in tot:
            tot[k] += valores[k]

    if "Baixa" in niveis:
        nivel = "Baixa"
    elif "Média" in niveis:
        nivel = "Média"
    elif niveis:
        nivel = "Alta"
    else:
        nivel = "Baixa"

    return {
        "total_kcal": round(tot["kcal"]),
        "proteinas_g": round(tot["prot"], 1),
        "carboidratos_g": round(tot["carb"], 1),
        "gorduras_g": round(tot["gord"], 1),
        "fibras_g": round(_num(data.get("fibras_g")), 1),
        "nivel_confianca": nivel,
        "itens_detalhados": itens_out,
        "dica_ia": data.get("dica_ia", ""),
        "qualidade_vegetais": data.get("qualidade_vegetais", "razoavel"),
        "qualidade_proteinas": data.get("qualidade_proteinas", "razoavel"),
        "qualidade_fibras": data.get("qualidade_fibras", "razoavel"),
        "qualidade_acucar": data.get("qualidade_acucar", "razoavel"),
        "qualidade_ultraprocessados": data.get("qualidade_ultraprocessados", "razoavel"),
        "candidatos_consultados": len(candidatos),
    }
