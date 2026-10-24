"""
Serviço de IA — NutriIA
=======================
Encapsula as chamadas ao Gemini (primário, SDK `google-genai`) e ao OpenRouter (fallback).

Por que este módulo existe (causa da lentidão de minutos no "Calculando"):
  * As chamadas não tinham NENHUM timeout. O SDK da OpenAI usa 600 s por padrão (com 2 retries
    internos) e o Gemini também pode ficar pendurado por minutos em modelos instáveis.
  * Cada requisição testava todos os modelos em sequência, inclusive os que estavam fora do ar
    ou nem existiam, repetindo a mesma falha a cada novo pedido.
  * O fallback ainda fazia até 6 tentativas em modelos gratuitos (filas longas/rate-limit).

Estratégia atual:
  * Timeout curto por tentativa + prazo total (deadline) por requisição.
  * "Circuit breaker": modelo que falhou (404/timeout/429/5xx) fica em quarentena por um tempo
    e é pulado nas requisições seguintes, então o usuário não paga a falha de novo.
  * Cada modelo é tentado no máximo uma vez por requisição.
  * Cache de respostas (memória + banco): pedidos idênticos são respondidos instantaneamente.
  * Tudo configurável por variáveis de ambiente (veja .env.example).
"""

import base64
import hashlib
import json
import os
import re
import threading
import time
from collections import OrderedDict
from typing import Optional

import openai
from google import genai
from google.genai import types

# ==================== CONFIGURAÇÃO ====================

def _lista_env(nome: str, padrao: list) -> list:
    bruto = os.environ.get(nome, "")
    itens = [i.strip() for i in bruto.split(",") if i.strip()]
    return itens or padrao


MODELOS_GEMINI = _lista_env("GEMINI_MODELS", ["gemini-3.8-flash", "gemini-3.5-flash", "gemini-3.1-flash-lite"])
MODELOS_OPENROUTER = _lista_env("OPENROUTER_MODELS", [
    "google/gemma-4-31b-it:free",
    "liquid/lfm-2.5-2.6b:free",
    "nvidia/nemotron-3.5-lightning:free",
    "qwen/qwen3.8-27b:free",
    "inclusionai/ling-3.0-flash-vl:free",
])

# Tempo máximo (s) de UMA tentativa, e prazo total (s) de uma requisição inteira
TIMEOUT_TENTATIVA = float(os.environ.get("IA_TIMEOUT_TENTATIVA", "20"))
TIMEOUT_TENTATIVA_IMAGEM = float(os.environ.get("IA_TIMEOUT_TENTATIVA_IMAGEM", "30"))
PRAZO_TOTAL = float(os.environ.get("IA_PRAZO_TOTAL", "45"))

# Cache de respostas
CACHE_ATIVO = os.environ.get("IA_CACHE", "1") != "0"
CACHE_TTL_SEGUNDOS = int(os.environ.get("IA_CACHE_TTL_HORAS", "168")) * 3600  # 7 dias
CACHE_MEMORIA_MAX = 256

# Quarentena (s) de um modelo após falha
QUARENTENA_INEXISTENTE = 3600   # 404 / modelo não suportado
QUARENTENA_TEMPORARIA = 90      # timeout / 429 / 5xx

openai_client = openai.OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.environ.get("OPENROUTER_API_KEY") or "sem-chave",
    max_retries=0,  # os retries são controlados aqui, respeitando o prazo total
)

_GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
try:
    gemini_client = genai.Client(api_key=_GEMINI_KEY) if _GEMINI_KEY else None
except Exception as e:  # pragma: no cover
    print("Gemini client não configurado:", e)
    gemini_client = None
gemini_client_ready = gemini_client is not None

# ==================== CIRCUIT BREAKER ====================

_quarentena: dict = {}
_lock = threading.Lock()


def _em_quarentena(modelo: str) -> bool:
    with _lock:
        ate = _quarentena.get(modelo, 0)
        if ate and ate > time.time():
            return True
        _quarentena.pop(modelo, None)
        return False


def _punir(modelo: str, erro: Exception):
    msg = str(erro).lower()
    inexistente = any(s in msg for s in ("404", "not found", "is not supported", "no endpoints found", "invalid model"))
    segundos = QUARENTENA_INEXISTENTE if inexistente else QUARENTENA_TEMPORARIA
    with _lock:
        _quarentena[modelo] = time.time() + segundos


# ==================== CACHE ====================

_cache_mem: "OrderedDict[str, tuple]" = OrderedDict()


def _chave_cache(prompt: str, schema: dict, system_instruction: str) -> str:
    bruto = json.dumps([prompt, schema, system_instruction], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(bruto.encode("utf-8")).hexdigest()


def _cache_get(chave: str) -> Optional[dict]:
    agora = time.time()
    with _lock:
        item = _cache_mem.get(chave)
        if item and agora - item[0] < CACHE_TTL_SEGUNDOS:
            _cache_mem.move_to_end(chave)
            return item[1]
    try:
        from db import get_db, garantir_tabelas_auxiliares
        garantir_tabelas_auxiliares()
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT resposta FROM ia_cache WHERE chave = ? AND criado_em >= ?",
                (chave, agora - CACHE_TTL_SEGUNDOS),
            ).fetchone()
        finally:
            conn.close()
        if row:
            dados = json.loads(row["resposta"])
            _cache_set_mem(chave, dados)
            return dados
    except Exception as e:
        print(f"[IA] Cache indisponível (leitura): {e}")
    return None


def _cache_set_mem(chave: str, dados: dict):
    with _lock:
        _cache_mem[chave] = (time.time(), dados)
        _cache_mem.move_to_end(chave)
        while len(_cache_mem) > CACHE_MEMORIA_MAX:
            _cache_mem.popitem(last=False)


def _cache_set(chave: str, dados: dict):
    _cache_set_mem(chave, dados)
    try:
        from db import get_db, garantir_tabelas_auxiliares
        garantir_tabelas_auxiliares()
        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO ia_cache (chave, resposta, criado_em) VALUES (?, ?, ?) "
                "ON CONFLICT (chave) DO UPDATE SET resposta = excluded.resposta, criado_em = excluded.criado_em",
                (chave, json.dumps(dados, ensure_ascii=False), time.time()),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        print(f"[IA] Cache indisponível (escrita): {e}")


# ==================== HELPERS ====================

def extract_json(text: str) -> str:
    """Extrai JSON de blocos markdown, se houver."""
    match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    return match.group(1) if match else text


# ==================== CHAMADA PRINCIPAL ====================

def chamar_ia_com_retry(
    prompt: str,
    schema: dict,
    system_instruction: str,
    max_retries: int = 6,
    imagem_base64: Optional[str] = None,
) -> dict:
    """Cache -> Gemini (primário) -> OpenRouter (fallback), com timeouts e prazo total.

    Respostas com imagem não são cacheadas (cada foto é única).
    """
    usar_cache = CACHE_ATIVO and not imagem_base64
    chave = _chave_cache(prompt, schema, system_instruction) if usar_cache else None
    if usar_cache:
        em_cache = _cache_get(chave)
        if em_cache is not None:
            return em_cache

    resultado = _chamar_modelos(prompt, schema, system_instruction, max_retries, imagem_base64)

    if usar_cache and isinstance(resultado, dict) and resultado:
        _cache_set(chave, resultado)
    return resultado


def _chamar_modelos(prompt, schema, system_instruction, max_retries, imagem_base64) -> dict:
    inicio = time.time()
    ultimo_erro: Optional[Exception] = None
    timeout_base = TIMEOUT_TENTATIVA_IMAGEM if imagem_base64 else TIMEOUT_TENTATIVA

    def restante() -> float:
        return PRAZO_TOTAL - (time.time() - inicio)

    instrucao = (
        f"{system_instruction}\n\nVocê deve retornar ESTRITAMENTE um JSON válido com esta estrutura exata "
        f"(e responda apenas com o JSON):\n{json.dumps(schema)}"
    )

    # ------------------ ROTA PRIMÁRIA (GEMINI) ------------------
    if gemini_client_ready:
        contents = []
        if imagem_base64:
            img_data = imagem_base64.split(",")[1] if "," in imagem_base64 else imagem_base64
            contents.append(types.Part.from_bytes(data=base64.b64decode(img_data), mime_type="image/jpeg"))
        contents.append(prompt)

        for nome_modelo in MODELOS_GEMINI:
            if _em_quarentena(nome_modelo):
                continue
            tempo = min(timeout_base, restante())
            if tempo < 3:
                break
            try:
                response = gemini_client.models.generate_content(
                    model=nome_modelo,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=instrucao,
                        response_mime_type="application/json",
                        temperature=0.2,
                        http_options=types.HttpOptions(timeout=int(tempo * 1000)),  # milissegundos
                    ),
                )
                return json.loads(extract_json(response.text))
            except Exception as e:
                print(f"[IA] Falha no Gemini ({nome_modelo}) após {time.time() - inicio:.1f}s: {e}")
                ultimo_erro = e
                _punir(nome_modelo, e)
    else:
        print("[IA] Gemini client não configurado. Tentando OpenRouter.")

    # ------------------ ROTA FALLBACK (OPENROUTER) ------------------
    if imagem_base64:
        raise ValueError(f"Falha ao usar IA para imagem (Gemini). O fallback não suporta imagens. Erro: {ultimo_erro}")

    tentativas = 0
    for modelo in MODELOS_OPENROUTER:
        if tentativas >= max_retries:
            break
        if _em_quarentena(modelo):
            continue
        tempo = min(timeout_base, restante())
        if tempo < 3:
            break
        tentativas += 1
        try:
            response = openai_client.with_options(timeout=tempo).chat.completions.create(
                model=modelo,
                messages=[
                    {"role": "system", "content": instrucao},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2 + (tentativas * 0.05),
            )
            content = response.choices[0].message.content
            if not content:
                raise ValueError("Resposta vazia da IA.")
            return json.loads(extract_json(content))
        except Exception as e:
            print(f"[IA] Falha no OpenRouter ({modelo}) após {time.time() - inicio:.1f}s: {e}")
            ultimo_erro = e
            _punir(modelo, e)

    raise ValueError(
        "A IA está demorando mais que o normal ou indisponível no momento. "
        f"Tente novamente em instantes. (Último erro: {ultimo_erro})"
    )
