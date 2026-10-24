"""
Serviço de IA — NutriIA
=======================
Encapsula as chamadas ao Gemini (primário) e ao OpenRouter (fallback).

Por que este módulo existe (causa da lentidão de minutos no "Calculando"):
  * As chamadas não tinham NENHUM timeout. O SDK da OpenAI usa 600 s por padrão (com 2 retries
    internos) e o SDK do Gemini também pode ficar pendurado por minutos em modelos instáveis.
  * Cada requisição testava todos os modelos em sequência, inclusive os que estavam fora do ar
    ou nem existiam, repetindo a mesma falha a cada novo pedido.
  * O fallback ainda fazia até 6 tentativas em modelos gratuitos (filas longas/rate-limit).

Estratégia atual:
  * Timeout curto por tentativa + prazo total (deadline) por requisição.
  * "Circuit breaker": modelo que falhou (404/timeout/429/5xx) fica em quarentena por um tempo
    e é pulado nas requisições seguintes, então o usuário não paga a falha de novo.
  * Cada modelo é tentado no máximo uma vez por requisição.
  * Tudo configurável por variáveis de ambiente (veja .env.example).
"""

import base64
import json
import os
import re
import threading
import time
from typing import Optional

import openai
import google.generativeai as genai

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

# Quarentena (s) de um modelo após falha
QUARENTENA_INEXISTENTE = 3600   # 404 / modelo não suportado
QUARENTENA_TEMPORARIA = 90      # timeout / 429 / 5xx

openai_client = openai.OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.environ.get("OPENROUTER_API_KEY") or "sem-chave",
    max_retries=0,  # os retries são controlados aqui, respeitando o prazo total
)

try:
    genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
    gemini_client_ready = bool(os.environ.get("GEMINI_API_KEY"))
except Exception as e:  # pragma: no cover
    print("Gemini client não configurado:", e)
    gemini_client_ready = False

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
    """Usa Gemini como primário e OpenRouter como fallback, com timeouts e prazo total."""
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
            contents.append({"mime_type": "image/jpeg", "data": base64.b64decode(img_data)})
        contents.append(prompt)

        for nome_modelo in MODELOS_GEMINI:
            if _em_quarentena(nome_modelo):
                continue
            tempo = min(timeout_base, restante())
            if tempo < 3:
                break
            try:
                model = genai.GenerativeModel(nome_modelo, system_instruction=instrucao)
                response = model.generate_content(
                    contents,
                    generation_config=genai.GenerationConfig(
                        response_mime_type="application/json",
                        temperature=0.2,
                    ),
                    request_options={"timeout": tempo},
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
