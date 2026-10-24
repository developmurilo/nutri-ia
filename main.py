import os
import sqlite3
import json
import re
import uvicorn
import time
from collections import defaultdict
from datetime import datetime, date, timedelta
from fastapi import FastAPI, HTTPException, Depends, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from typing import List, Optional
import base64
import openai
from dotenv import load_dotenv
from jose import jwt, JWTError
from passlib.context import CryptContext
from rag_nutricional import analisar_refeicao_rag, base_alimentos


load_dotenv()

# ==================== CONFIG ====================

JWT_SECRET = os.environ.get("JWT_SECRET")
if not JWT_SECRET:
    print("[AVISO SEGURANÇA] JWT_SECRET não configurado no .env! Usando segredo temporário gerado.")
    JWT_SECRET = "nutriia_secure_fallback_" + os.urandom(8).hex()

JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_HOURS = 72  # 3 dias

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

app = FastAPI(title="NutriIA - Calculadora Nutricional Inteligente")

# Configuração de CORS para segurança e compatibilidade
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate Limiter leve em memória para prevenir ataques e abusos
RATE_LIMITS = defaultdict(list)

def verificar_rate_limit(identificador: str, limite_requisicoes: int = 25, janela_segundos: int = 60):
    agora = time.time()
    RATE_LIMITS[identificador] = [t for t in RATE_LIMITS[identificador] if agora - t < janela_segundos]
    if len(RATE_LIMITS[identificador]) >= limite_requisicoes:
        raise HTTPException(
            status_code=429,
            detail="Muitas requisições em curto período. Por favor, aguarde alguns instantes."
        )
    RATE_LIMITS[identificador].append(agora)

# ==================== BANCO DE DADOS ====================
from db import get_db, init_db

init_db()

# ==================== HELPERS ====================

def extract_json(text: str) -> str:
    """Extrai JSON de blocos markdown, se houver."""
    match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        return match.group(1)
    return text

# Inicialização do SDK da OpenAI (via OpenRouter para Textos)
openai_client = openai.OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.environ.get("OPENROUTER_API_KEY"),
)

# Inicialização do SDK do Gemini
import google.generativeai as genai
try:
    genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
    gemini_client_ready = True
except Exception as e:
    print("Gemini client não configurado:", e)
    gemini_client_ready = False

def chamar_ia_com_retry(prompt: str, schema: dict, system_instruction: str, max_retries: int = 6, imagem_base64: Optional[str] = None) -> dict:
    """Encapsula a chamada à IA. Usa Gemini como primário e OpenRouter como fallback."""
    import time
    ultimo_erro = None
    
    full_system_instruction = f"{system_instruction}\n\nVocê deve retornar ESTRITAMENTE um JSON válido com esta estrutura exata (e responda apenas com o JSON):\n{json.dumps(schema)}"

    # ------------------ ROTA PRIMÁRIA (GEMINI) ------------------
    if gemini_client_ready:
        modelos_gemini = ["gemini-3.8-flash", "gemini-3.5-flash", "gemini-3.1-flash-lite"]
        for nome_modelo in modelos_gemini:
            try:
                import base64
                
                contents = []
                if imagem_base64:
                    # Extrai apenas o raw base64
                    img_data = imagem_base64.split(",")[1] if "," in imagem_base64 else imagem_base64
                    img_bytes = base64.b64decode(img_data)
                    contents.append(
                        {"mime_type": "image/jpeg", "data": img_bytes}
                    )
                contents.append(prompt)
                    
                model = genai.GenerativeModel(nome_modelo, system_instruction=full_system_instruction)
                response = model.generate_content(
                    contents,
                    generation_config=genai.GenerationConfig(
                        response_mime_type="application/json",
                        temperature=0.2,
                    )
                )
                json_str = extract_json(response.text)
                return json.loads(json_str)
            except Exception as e:
                print(f"[IA] Falha no Gemini ({nome_modelo}): {e}")
                ultimo_erro = e
    else:
        print("[IA] Gemini client não configurado. Tentando OpenRouter.")

    # ------------------ ROTA FALLBACK (OPENROUTER) ------------------
    if imagem_base64:
        raise ValueError(f"Falha ao usar IA para imagem (Gemini). O fallback não suporta imagens. Erro: {ultimo_erro}")

    full_system_instruction = f"{system_instruction}\n\nVocê deve retornar ESTRITAMENTE um JSON válido com esta estrutura:\n{json.dumps(schema)}"

    modelos_gratuitos = [
        "google/gemma-4-31b-it:free",
        "liquid/lfm-2.5-2.6b:free",
        "nvidia/nemotron-3.5-lightning:free",
        "qwen/qwen3.8-27b:free",
        "inclusionai/ling-3.0-flash-vl:free"
    ]

    for tentativa in range(max_retries):
        modelo_atual = modelos_gratuitos[tentativa % len(modelos_gratuitos)]
        try:
            response = openai_client.chat.completions.create(
                model=modelo_atual,
                messages=[
                    {"role": "system", "content": full_system_instruction},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.2 + (tentativa * 0.1)
            )
            
            content = response.choices[0].message.content
            if not content:
                raise ValueError("Resposta vazia da IA.")

            json_str = extract_json(content)
            data = json.loads(json_str)
            return data
        except Exception as e:
            ultimo_erro = e

    raise ValueError(f"Falha ao interpretar retorno da IA após {max_retries} tentativas. Último erro: {ultimo_erro}")

# ==================== AUTH ====================

def criar_token(usuario_id: int, nome: str) -> str:
    payload = {
        "sub": str(usuario_id),
        "nome": nome,
        "exp": datetime.utcnow() + timedelta(hours=JWT_EXPIRATION_HOURS)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def obter_usuario_atual(authorization: Optional[str] = Header(None)) -> dict:
    """Dependency: extrai e valida o token JWT do header Authorization."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token de autenticação não fornecido.")
    token = authorization.split(" ")[1]
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        usuario_id = int(payload["sub"])
        nome = payload["nome"]
        
        sexo = "não informado"
        try:
            conn = get_db()
            perfil = conn.execute("SELECT sexo FROM perfis WHERE usuario_id = ?", (usuario_id,)).fetchone()
            if perfil and perfil["sexo"]:
                sexo = perfil["sexo"]
            conn.close()
        except:
            pass

        return {"id": usuario_id, "nome": nome, "sexo": sexo}
    except JWTError:
        raise HTTPException(status_code=401, detail="Token inválido ou expirado. Faça login novamente.")

# ==================== MODELOS ====================

class RegistroRequest(BaseModel):
    nome: str
    email: str
    senha: str

class LoginRequest(BaseModel):
    email: str
    senha: str

class RecuperarSenhaRequest(BaseModel):
    email: str

class AlterarSenhaRequest(BaseModel):
    senha_atual: str
    nova_senha: str

class PerfilUsuario(BaseModel):
    peso_kg: float
    altura_cm: float
    idade: int
    sexo: str = Field(pattern="^(masculino|feminino)$")
    nivel_atividade: float
    objetivo: str = Field(pattern="^(perda|manutencao|ganho)$")

class RefeicaoRequest(BaseModel):
    tipo: str
    descricao: Optional[str] = ""
    data: Optional[str] = None
    foto_base64: Optional[str] = None

class SalvarRefeicaoRequest(BaseModel):
    tipo: str
    descricao: str
    data: Optional[str] = None
    total_kcal: float
    proteinas_g: float
    carboidratos_g: float
    gorduras_g: float
    dica_ia: str = ""
    qualidade_vegetais: Optional[str] = ""
    qualidade_proteinas: Optional[str] = ""
    qualidade_fibras: Optional[str] = ""
    qualidade_acucar: Optional[str] = ""
    qualidade_ultraprocessados: Optional[str] = ""
    nivel_confianca: Optional[str] = None
    itens_detalhados: Optional[list] = None

class RefeicaoManualRequest(BaseModel):
    tipo: str = "Refeição"
    nome_alimento: Optional[str] = None
    alimento: Optional[str] = None
    quantidade: float = 1.0
    unidade: str = "porção"
    peso_g: Optional[float] = None
    calorias: float
    proteinas_g: Optional[float] = 0.0
    carboidratos_g: Optional[float] = 0.0
    gorduras_g: Optional[float] = 0.0
    fibras_g: Optional[float] = 0.0
    salvar_na_base: Optional[bool] = None
    aprender: Optional[bool] = None
    data: Optional[str] = None

class ExercicioRequest(BaseModel):
    descricao: str
    duracao_minutos: int
    intensidade: str
    peso_kg: float
    data: Optional[str] = None

class LogUpdateRequest(BaseModel):
    descricao: str
    calorias: float
    proteinas_g: Optional[float] = 0
    carboidratos_g: Optional[float] = 0
    gorduras_g: Optional[float] = 0

class FotoRequest(BaseModel):
    foto_base64: str

class AguaRequest(BaseModel):
    quantidade_ml: int
    data: Optional[str] = None

# ==================== ROTAS AUTH ====================

@app.post("/api/auth/registrar")
def registrar(dados: RegistroRequest, request: Request):
    ip = request.client.host if request.client else "unknown"
    verificar_rate_limit(f"reg_{ip}", limite_requisicoes=10, janela_segundos=60)

    if len(dados.senha) < 6:
        raise HTTPException(status_code=400, detail="A senha deve ter pelo menos 6 caracteres.")
    if not dados.nome.strip():
        raise HTTPException(status_code=400, detail="O nome é obrigatório.")
    if "@" not in dados.email:
        raise HTTPException(status_code=400, detail="Email inválido.")

    conn = get_db()
    try:
        # Verificar se email já existe
        existente = conn.execute("SELECT id FROM usuarios WHERE email = ?", (dados.email.lower().strip(),)).fetchone()
        if existente:
            raise HTTPException(status_code=409, detail="Este email já está cadastrado.")

        senha_hash = pwd_context.hash(dados.senha)
        c = conn.cursor()
        c.execute(
            "INSERT INTO usuarios (nome, email, senha_hash) VALUES (?, ?, ?)",
            (dados.nome.strip(), dados.email.lower().strip(), senha_hash)
        )
        conn.commit()
        usuario_id = c.lastrowid

        token = criar_token(usuario_id, dados.nome.strip())
        return {"token": token, "nome": dados.nome.strip(), "id": usuario_id}
    finally:
        conn.close()

@app.post("/api/auth/login")
def login(dados: LoginRequest, request: Request):
    ip = request.client.host if request.client else "unknown"
    verificar_rate_limit(f"login_{ip}", limite_requisicoes=15, janela_segundos=60)

    conn = get_db()
    try:
        user = conn.execute(
            "SELECT id, nome, senha_hash FROM usuarios WHERE email = ?",
            (dados.email.lower().strip(),)
        ).fetchone()

        if not user or not pwd_context.verify(dados.senha, user["senha_hash"]):
            raise HTTPException(status_code=401, detail="Email ou senha incorretos.")

        token = criar_token(user["id"], user["nome"])
        return {"token": token, "nome": user["nome"], "id": user["id"]}
    finally:
        conn.close()

@app.post("/api/auth/recuperar-senha")
def recuperar_senha(dados: RecuperarSenhaRequest, request: Request):
    ip = request.client.host if request.client else "unknown"
    verificar_rate_limit(f"recup_{ip}", limite_requisicoes=5, janela_segundos=60)

    import random
    conn = get_db()
    try:
        user = conn.execute("SELECT id FROM usuarios WHERE email = ?", (dados.email.lower().strip(),)).fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="Email não encontrado.")
        
        nova_senha = str(random.randint(100000, 999999))
        senha_hash = pwd_context.hash(nova_senha)
        
        conn.execute("UPDATE usuarios SET senha_hash = ? WHERE id = ?", (senha_hash, user["id"]))
        conn.commit()
        
        print(f"\n[SEGURANÇA] Senha temporária para o usuário '{dados.email}': {nova_senha}\n")

        # Se a requisição veio de rede externa pública, NUNCA expor a senha no JSON
        is_local = ip in ("127.0.0.1", "::1", "localhost")
        if is_local:
            return {"sucesso": True, "nova_senha": nova_senha}
        else:
            return {
                "sucesso": True,
                "mensagem": "Senha temporária redefinida com segurança. Por proteção, a senha foi impressa no console do servidor."
            }
    finally:
        conn.close()

@app.post("/api/auth/alterar-senha")
def alterar_senha(dados: AlterarSenhaRequest, usuario: dict = Depends(obter_usuario_atual)):
    if len(dados.nova_senha) < 4:
        raise HTTPException(status_code=400, detail="A nova senha deve ter pelo menos 4 caracteres.")
        
    conn = get_db()
    try:
        user = conn.execute("SELECT senha_hash FROM usuarios WHERE id = ?", (usuario["id"],)).fetchone()
        
        if not user or not pwd_context.verify(dados.senha_atual, user["senha_hash"]):
            raise HTTPException(status_code=400, detail="Senha atual incorreta.")
            
        senha_hash = pwd_context.hash(dados.nova_senha)
        conn.execute("UPDATE usuarios SET senha_hash = ? WHERE id = ?", (senha_hash, usuario["id"]))
        conn.commit()
        
        return {"sucesso": True}
    finally:
        conn.close()

@app.get("/api/auth/me")
def me(usuario: dict = Depends(obter_usuario_atual)):
    conn = get_db()
    try:
        user_db = conn.execute("SELECT foto_perfil FROM usuarios WHERE id = ?", (usuario["id"],)).fetchone()
        perfil = conn.execute(
            "SELECT * FROM perfis WHERE usuario_id = ?", (usuario["id"],)
        ).fetchone()
        
        novo_token = criar_token(usuario["id"], usuario["nome"])
        
        return {
            "id": usuario["id"],
            "nome": usuario["nome"],
            "foto_perfil": user_db["foto_perfil"] if user_db else None,
            "perfil": dict(perfil) if perfil else None,
            "token": novo_token
        }
    finally:
        conn.close()

# ==================== ROTAS PERFIL ====================

@app.post("/api/perfil/foto")
def atualizar_foto(dados: FotoRequest, usuario: dict = Depends(obter_usuario_atual)):
    conn = get_db()
    try:
        conn.execute("UPDATE usuarios SET foto_perfil = ? WHERE id = ?", (dados.foto_base64, usuario["id"]))
        conn.commit()
        return {"sucesso": True}
    finally:
        conn.close()

@app.post("/api/perfil/calcular")
def calcular_metricas_perfil(dados: PerfilUsuario, usuario: dict = Depends(obter_usuario_atual)):
    # Cálculo do IMC
    altura_m = dados.altura_cm / 100.0
    imc = round(dados.peso_kg / (altura_m ** 2), 2)

    # Taxa Metabólica Basal (Mifflin-St Jeor)
    if dados.sexo == "masculino":
        tmb = (10 * dados.peso_kg) + (6.25 * dados.altura_cm) - (5 * dados.idade) + 5
    else:
        tmb = (10 * dados.peso_kg) + (6.25 * dados.altura_cm) - (5 * dados.idade) - 161

    gasto_diario_total = tmb * dados.nivel_atividade

    # Ajuste de calorias segundo o objetivo com segurança clínica e piso metabólico
    piso_seguro = 1500.0 if dados.sexo == "masculino" else 1200.0
    if dados.objetivo == "perda":
        # Déficit moderado de 20%, garantindo que não caia abaixo do piso biológico seguro
        meta_calorica = max(gasto_diario_total * 0.80, piso_seguro)
    elif dados.objetivo == "ganho":
        # Superávit moderado de 15% para ganho de massa muscular
        meta_calorica = gasto_diario_total * 1.15
    else:
        meta_calorica = gasto_diario_total

    # Salvar perfil no banco
    conn = get_db()
    try:
        existente = conn.execute("SELECT id FROM perfis WHERE usuario_id = ?", (usuario["id"],)).fetchone()
        if existente:
            conn.execute('''
                UPDATE perfis SET peso_kg=?, altura_cm=?, idade=?, sexo=?, nivel_atividade=?,
                objetivo=?, imc=?, tmb=?, gasto_diario=?, meta_calorica=?, atualizado_em=?
                WHERE usuario_id=?
            ''', (dados.peso_kg, dados.altura_cm, dados.idade, dados.sexo, dados.nivel_atividade,
                  dados.objetivo, imc, round(tmb, 0), round(gasto_diario_total, 0),
                  round(meta_calorica, 0), datetime.now().isoformat(), usuario["id"]))
        else:
            conn.execute('''
                INSERT INTO perfis (usuario_id, peso_kg, altura_cm, idade, sexo, nivel_atividade,
                objetivo, imc, tmb, gasto_diario, meta_calorica)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (usuario["id"], dados.peso_kg, dados.altura_cm, dados.idade, dados.sexo,
                  dados.nivel_atividade, dados.objetivo, imc, round(tmb, 0),
                  round(gasto_diario_total, 0), round(meta_calorica, 0)))
        conn.commit()
    finally:
        conn.close()

    return {
        "imc": imc,
        "tmb": round(tmb, 0),
        "gasto_diario_total": round(gasto_diario_total, 0),
        "meta_calorica": round(meta_calorica, 0)
    }

# ==================== ROTAS NUTRIÇÃO ====================

@app.post("/api/logs/salvar-refeicao")
def salvar_refeicao_direto(dados: SalvarRefeicaoRequest, usuario: dict = Depends(obter_usuario_atual)):
    conn = get_db()
    try:
        data_hora_atual = datetime.now()
        if dados.data:
            try:
                dt = datetime.strptime(dados.data, "%Y-%m-%d")
                data_hora_atual = data_hora_atual.replace(year=dt.year, month=dt.month, day=dt.day)
            except ValueError:
                pass

        qualidade_dict = {
            "vegetais": dados.qualidade_vegetais,
            "proteinas": dados.qualidade_proteinas,
            "fibras": dados.qualidade_fibras,
            "acucar": dados.qualidade_acucar,
            "ultraprocessados": dados.qualidade_ultraprocessados
        }
        qualidade_json = json.dumps(qualidade_dict)

        c = conn.cursor()
        c.execute('''
            INSERT INTO logs (usuario_id, tipo, titulo, descricao, detalhes, calorias, cor, tag, data_hora, proteinas_g, carboidratos_g, gorduras_g, dica_ia, qualidade_json, nivel_confianca, itens_detalhados_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            usuario["id"],
            "refeicao",
            f"{dados.total_kcal} kcal",
            dados.descricao,
            f"P: {dados.proteinas_g}g | C: {dados.carboidratos_g}g | G: {dados.gorduras_g}g",
            dados.total_kcal,
            "green",
            dados.tipo,
            data_hora_atual.isoformat(),
            dados.proteinas_g,
            dados.carboidratos_g,
            dados.gorduras_g,
            dados.dica_ia,
            qualidade_json,
            dados.nivel_confianca,
            json.dumps(dados.itens_detalhados or [], ensure_ascii=False)
        ))
        conn.commit()
        return {"sucesso": True, "id": c.lastrowid}
    finally:
        conn.close()

@app.post("/api/nutricao/salvar-manual")
def salvar_refeicao_manual(dados: RefeicaoManualRequest, usuario: dict = Depends(obter_usuario_atual)):
    nome_final = (dados.nome_alimento or dados.alimento or "").strip()
    if not nome_final:
        raise HTTPException(status_code=400, detail="Informe o nome do alimento.")
    if dados.calorias < 0:
        raise HTTPException(status_code=400, detail="As calorias não podem ser negativas.")

    deve_aprender = dados.salvar_na_base if dados.salvar_na_base is not None else (dados.aprender if dados.aprender is not None else True)

    conn = get_db()
    try:
        data_hora_atual = datetime.now()
        if dados.data:
            try:
                dt = datetime.strptime(dados.data, "%Y-%m-%d")
                data_hora_atual = data_hora_atual.replace(year=dt.year, month=dt.month, day=dt.day)
            except ValueError:
                pass

        # Determina o peso estimado em gramas para a porção consumida
        peso_total_g = dados.peso_g
        if not peso_total_g or peso_total_g <= 0:
            if dados.unidade.lower() in ("g", "gramas", "ml"):
                peso_total_g = float(dados.quantidade)
            else:
                peso_total_g = 100.0 * max(float(dados.quantidade), 1.0)

        # Monta item detalhado
        item_detalhe = [{
            "nome": nome_final,
            "nome_original": nome_final,
            "quantidade_original": f"{dados.quantidade:g}",
            "unidade_original": dados.unidade.strip(),
            "peso_g": round(peso_total_g, 1),
            "kcal": round(float(dados.calorias), 1),
            "prot": round(float(dados.proteinas_g or 0), 1),
            "carb": round(float(dados.carboidratos_g or 0), 1),
            "gord": round(float(dados.gorduras_g or 0), 1),
            "confianca": "Alta (Manual)",
            "fonte": "Cadastrado pelo Usuário",
            "metodo_medida": "manual"
        }]

        # 1. Salvar no diário (logs)
        c = conn.cursor()
        c.execute('''
            INSERT INTO logs (usuario_id, tipo, titulo, descricao, detalhes, calorias, cor, tag, data_hora, proteinas_g, carboidratos_g, gorduras_g, dica_ia, qualidade_json, nivel_confianca, itens_detalhados_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            usuario["id"],
            "refeicao",
            f"{round(dados.calorias)} kcal",
            f"{dados.quantidade:g} {dados.unidade} de {nome_final}",
            f"P: {dados.proteinas_g or 0}g | C: {dados.carboidratos_g or 0}g | G: {dados.gorduras_g or 0}g",
            round(dados.calorias, 1),
            "green",
            dados.tipo or "Refeição",
            data_hora_atual.isoformat(),
            dados.proteinas_g or 0,
            dados.carboidratos_g or 0,
            dados.gorduras_g or 0,
            "Alimento registrado manualmente pelo usuário e adicionado ao histórico.",
            json.dumps({"vegetais": "razoavel", "proteinas": "boa", "fibras": "razoavel", "acucar": "razoavel", "ultraprocessados": "razoavel"}),
            "Manual",
            json.dumps(item_detalhe, ensure_ascii=False)
        ))
        log_id = c.lastrowid

        # 2. Se optou por salvar na base para a IA aprender:
        aprendido = False
        if deve_aprender and peso_total_g > 0:
            fator_100 = 100.0 / peso_total_g
            kcal_100 = round(dados.calorias * fator_100, 1)
            prot_100 = round((dados.proteinas_g or 0) * fator_100, 1)
            carb_100 = round((dados.carboidratos_g or 0) * fator_100, 1)
            gord_100 = round((dados.gorduras_g or 0) * fator_100, 1)
            fibra_100 = round((dados.fibras_g or 0) * fator_100, 1)

            # Grava na tabela alimentos do banco
            try:
                c.execute('''
                    INSERT INTO alimentos (nome, categoria, kcal_100g, proteinas_100g, carboidratos_100g, gorduras_100g, fibras_100g, unidade_referencia, fonte)
                    VALUES (?, 'Personalizado', ?, ?, ?, ?, ?, 'g', 'Usuário')
                ''', (nome_final, kcal_100, prot_100, carb_100, gord_100, fibra_100))
                alimento_id = c.lastrowid
                if alimento_id and dados.unidade.lower() not in ("g", "ml"):
                    gramas_unitarias = peso_total_g / max(float(dados.quantidade), 1.0)
                    c.execute('''
                        INSERT INTO medidas_caseiras (alimento_id, unidade, gramas)
                        VALUES (?, ?, ?)
                    ''', (alimento_id, dados.unidade.strip(), round(gramas_unitarias, 1)))
            except Exception as e_db:
                print(f"[MANUAL] Aviso ao gravar alimento no banco: {e_db}")

            # Ensina diretamente o motor RAG em memória e grava no correcoes_base.csv
            base_alimentos.aprender_alimento(
                nome=nome_final,
                kcal=kcal_100,
                prot=prot_100,
                gord=gord_100,
                carb=carb_100
            )
            aprendido = True

        conn.commit()
        return {
            "sucesso": True,
            "id": log_id,
            "aprendido": aprendido,
            "mensagem": "Refeição salva e novo alimento aprendido pela IA!" if aprendido else "Refeição registrada com sucesso!"
        }
    finally:
        conn.close()

@app.post("/api/nutricao/pre-analisar")
def pre_analisar_refeicao(dados: RefeicaoRequest, usuario: dict = Depends(obter_usuario_atual)):
    """Analisa a refeição via RAG (base CSV + medidas caseiras) SEM salvar. O front confirma depois."""
    if not (dados.descricao or "").strip() and not dados.foto_base64:
        raise HTTPException(status_code=400, detail="Descreva o que você comeu ou envie uma foto.")
    try:
        return analisar_refeicao_rag(
            descricao=dados.descricao or "",
            tipo=dados.tipo,
            usuario=usuario,
            chamar_ia=chamar_ia_com_retry,
            foto_base64=dados.foto_base64,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/nutricao/analisar")
def analisar_refeicao(dados: RefeicaoRequest, usuario: dict = Depends(obter_usuario_atual)):
    """Analisa a refeição via RAG e já salva no diário."""
    if not (dados.descricao or "").strip() and not dados.foto_base64:
        raise HTTPException(status_code=400, detail="Descreva o que você comeu ou envie uma foto.")
    try:
        resultado = analisar_refeicao_rag(
            descricao=dados.descricao or "",
            tipo=dados.tipo,
            usuario=usuario,
            chamar_ia=chamar_ia_com_retry,
            foto_base64=dados.foto_base64,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    total_kcal = resultado["total_kcal"]
    prot = resultado["proteinas_g"]
    carb = resultado["carboidratos_g"]
    gord = resultado["gorduras_g"]

    conn = get_db()
    try:
        data_hora_atual = datetime.now()
        if dados.data:
            try:
                dt = datetime.strptime(dados.data, "%Y-%m-%d")
                data_hora_atual = data_hora_atual.replace(year=dt.year, month=dt.month, day=dt.day)
            except ValueError:
                pass

        qualidade_json = json.dumps({
            "vegetais": resultado.get("qualidade_vegetais", "razoavel"),
            "proteinas": resultado.get("qualidade_proteinas", "razoavel"),
            "fibras": resultado.get("qualidade_fibras", "razoavel"),
            "acucar": resultado.get("qualidade_acucar", "razoavel"),
            "ultraprocessados": resultado.get("qualidade_ultraprocessados", "razoavel"),
        })

        conn.execute('''
            INSERT INTO logs (usuario_id, tipo, titulo, descricao, detalhes, calorias, cor, tag, data_hora, proteinas_g, carboidratos_g, gorduras_g, dica_ia, qualidade_json, nivel_confianca, itens_detalhados_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            usuario["id"],
            "refeicao",
            f"{total_kcal} kcal",
            dados.descricao,
            f"P: {prot}g | C: {carb}g | G: {gord}g",
            total_kcal,
            "green",
            dados.tipo,
            data_hora_atual.isoformat(),
            prot,
            carb,
            gord,
            resultado.get("dica_ia", ""),
            qualidade_json,
            resultado.get("nivel_confianca", "Média"),
            json.dumps(resultado.get("itens_detalhados", []), ensure_ascii=False),
        ))
        conn.commit()
    finally:
        conn.close()

    return resultado


# ==================== ROTAS EXERCÍCIO ====================

@app.post("/api/exercicio/analisar")
def analisar_exercicio(dados: ExercicioRequest, usuario: dict = Depends(obter_usuario_atual)):
    prompt = f"""
    Calcule o gasto energético deste exercício do paciente/aluno {usuario['nome']} (sexo: {usuario['sexo']}):
    - Atividade: {dados.descricao}
    - Duração: {dados.duracao_minutos} minutos
    - Intensidade: {dados.intensidade}
    - Peso do praticante: {dados.peso_kg} kg

    Use a fórmula: Calorias = MET × Peso(kg) × Duração(horas)

    Tabela de referência de METs:
    * Caminhada leve (4 km/h): 3.0
    * Caminhada moderada (5.5 km/h): 3.8
    * Corrida leve (8 km/h): 8.0
    * Corrida moderada (9.5 km/h): 10.0
    * Corrida intensa (12+ km/h): 12.5
    * Musculação leve: 3.5
    * Musculação moderada: 5.0
    * Musculação intensa: 6.0
    * Ciclismo leve: 4.0
    * Ciclismo moderado: 8.0
    * Natação moderada: 7.0
    * HIIT / Crossfit: 8.0-12.0
    * Yoga / Pilates: 2.5-3.5
    * Futebol recreativo: 7.0
    * Dança: 4.5-7.0
    * Pular corda: 10.0-12.0

    IMPORTANTE: Use o MET correto da tabela acima para a atividade e intensidade informadas. Não invente valores de MET.

Forneça também uma dica clínica, fisiológica ou biomecânica breve sobre recuperação, hidratação ou desempenho relacionada ao exercício realizado.

A dica deve ser altamente profissional, baseada em medicina esportiva e acolhedora. Seja inspirador e vá direto ao ponto, não use textos longos. Dirija-se diretamente a {usuario['nome']} respeitando o gênero ({usuario['sexo']}). Não faça piadas ou zoeiras.

A dica deve estar relacionada ao exercício realmente realizado. Não invente recomendações genéricas apenas para preencher espaço.

Considere, quando relevante:

* hidratação;
* descanso e recuperação muscular;
* alimentação após o exercício;
* alongamento ou mobilidade;
* intensidade e duração do treino;
* necessidade de descanso após exercícios mais intensos;
* progressão gradual do treino.

Não incentive o usuário a treinar além dos próprios limites apenas para gastar mais calorias. Se o exercício tiver sido intenso, priorize recuperação adequada.

O objetivo é informar e motivar de maneira prática e técnica.
 (chave: dica_ia).
    """

    schema = {
        "type": "OBJECT",
        "properties": {
            "exercicio_identificado": {"type": "STRING"},
            "calorias_gastas": {"type": "NUMBER"},
            "met_utilizado": {"type": "NUMBER"},
            "dica_ia": {"type": "STRING"},
            "observacao": {"type": "STRING"}
        },
        "required": ["exercicio_identificado", "calorias_gastas", "dica_ia"]
    }

    try:
        data = chamar_ia_com_retry(
            prompt=prompt,
            schema=schema,
            system_instruction=f"Você é a NuriIA, profissional (Médico Esportivo e Personal Trainer). O aluno se chama {usuario['nome']} (sexo: {usuario['sexo']}). Seja técnica, não faça zoeiras."
        )

        # Cálculo determinístico em Python: Calorias = MET × Peso(kg) × Duração(horas)
        try:
            met = float(data.get("met_utilizado") or 5.0)
        except (ValueError, TypeError):
            met = 5.0
        duracao_horas = max(dados.duracao_minutos, 1) / 60.0
        data["calorias_gastas"] = round(met * dados.peso_kg * duracao_horas, 1)

        # Salvar no banco
        conn = get_db()
        try:
            data_hora_atual = datetime.now()
            if dados.data:
                try:
                    dt = datetime.strptime(dados.data, "%Y-%m-%d")
                    data_hora_atual = data_hora_atual.replace(year=dt.year, month=dt.month, day=dt.day)
                except ValueError:
                    pass

            conn.execute('''
                INSERT INTO logs (usuario_id, tipo, titulo, descricao, detalhes, calorias, cor, tag, data_hora, proteinas_g, carboidratos_g, gorduras_g, dica_ia)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                usuario["id"],
                "exercicio",
                f"-{data['calorias_gastas']} kcal",
                f"{data['exercicio_identificado']} ({dados.duracao_minutos} min - {dados.intensidade})",
                f"MET calculado: {data.get('met_utilizado', 'N/A')}",
                data['calorias_gastas'],
                "orange",
                "Exercício",
                data_hora_atual.isoformat(),
                0, 0, 0,
                data.get('dica_ia', '')
            ))
            conn.commit()
        finally:
            conn.close()

        return data
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# ==================== ROTAS LOGS ====================

@app.get("/api/logs/data/{data}")
def obter_logs_por_data(data: str, usuario: dict = Depends(obter_usuario_atual)):
    # data no formato YYYY-MM-DD
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute(
            "SELECT * FROM logs WHERE usuario_id = ? AND date(data_hora) = ? ORDER BY data_hora DESC",
            (usuario["id"], data)
        )
        logs = [dict(row) for row in c.fetchall()]
        return logs
    finally:
        conn.close()

@app.delete("/api/logs/{log_id}")
def excluir_log(log_id: int, usuario: dict = Depends(obter_usuario_atual)):
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("DELETE FROM logs WHERE id = ? AND usuario_id = ?", (log_id, usuario["id"]))
        if c.rowcount == 0:
            raise HTTPException(status_code=404, detail="Registro não encontrado.")
        conn.commit()
        return {"sucesso": True}
    finally:
        conn.close()

@app.put("/api/logs/{log_id}")
def editar_log(log_id: int, dados: LogUpdateRequest, usuario: dict = Depends(obter_usuario_atual)):
    conn = get_db()
    try:
        c = conn.cursor()
        # Verificar se existe e qual o tipo
        log_atual = c.execute("SELECT * FROM logs WHERE id = ? AND usuario_id = ?", (log_id, usuario["id"])).fetchone()
        if not log_atual:
            raise HTTPException(status_code=404, detail="Registro não encontrado.")
        
        titulo = f"{dados.calorias} kcal" if log_atual['tipo'] == 'refeicao' else f"-{dados.calorias} kcal"
        detalhes = f"P: {dados.proteinas_g}g | C: {dados.carboidratos_g}g | G: {dados.gorduras_g}g" if log_atual['tipo'] == 'refeicao' else log_atual['detalhes']

        c.execute('''
            UPDATE logs 
            SET descricao = ?, calorias = ?, titulo = ?, detalhes = ?, proteinas_g = ?, carboidratos_g = ?, gorduras_g = ?
            WHERE id = ? AND usuario_id = ?
        ''', (dados.descricao, dados.calorias, titulo, detalhes, dados.proteinas_g, dados.carboidratos_g, dados.gorduras_g, log_id, usuario["id"]))
        conn.commit()
        return {"sucesso": True}
    finally:
        conn.close()

@app.get("/api/logs/resumo_semanal")
def resumo_semanal(usuario: dict = Depends(obter_usuario_atual)):
    conn = get_db()
    try:
        hoje = date.today()
        ha_7_dias = hoje - timedelta(days=6) # Últimos 7 dias (incluindo hoje)
        
        c = conn.cursor()
        c.execute('''
            SELECT date(data_hora) as data_dia, tipo, SUM(calorias) as total_calorias,
                   SUM(proteinas_g) as total_prot, SUM(carboidratos_g) as total_carb, SUM(gorduras_g) as total_gord
            FROM logs 
            WHERE usuario_id = ? AND date(data_hora) >= ? AND date(data_hora) <= ?
            GROUP BY date(data_hora), tipo
            ORDER BY data_dia ASC
        ''', (usuario["id"], ha_7_dias.isoformat(), hoje.isoformat()))
        
        resultados = c.fetchall()
        
        # Agrupar por dia
        resumo = {}
        for row in resultados:
            dia = row['data_dia']
            if dia not in resumo:
                resumo[dia] = {'consumidas': 0, 'gastas': 0, 'proteinas': 0, 'carboidratos': 0, 'gorduras': 0}
            
            if row['tipo'] == 'refeicao':
                resumo[dia]['consumidas'] += row['total_calorias'] or 0
                resumo[dia]['proteinas'] += row['total_prot'] or 0
                resumo[dia]['carboidratos'] += row['total_carb'] or 0
                resumo[dia]['gorduras'] += row['total_gord'] or 0
            else:
                resumo[dia]['gastas'] += row['total_calorias'] or 0
                
        # Transformar em lista ordenada para preencher dias faltantes
        lista_resumo = []
        for i in range(7):
            d = (ha_7_dias + timedelta(days=i)).isoformat()
            if d in resumo:
                lista_resumo.append({'data': d, **resumo[d]})
            else:
                lista_resumo.append({'data': d, 'consumidas': 0, 'gastas': 0, 'proteinas': 0, 'carboidratos': 0, 'gorduras': 0})
                
        return lista_resumo
    finally:
        conn.close()

@app.get("/api/logs/analisar-dia/{data}")
def analisar_dia(data: str, usuario: dict = Depends(obter_usuario_atual)):
    conn = get_db()
    try:
        # Obter perfil
        perfil = conn.execute("SELECT meta_calorica FROM perfis WHERE usuario_id = ?", (usuario["id"],)).fetchone()
        meta = perfil["meta_calorica"] if perfil else 2000

        c = conn.cursor()
        c.execute("SELECT * FROM logs WHERE usuario_id = ? AND date(data_hora) = ?", (usuario["id"], data))
        logs = c.fetchall()

        refeicoes = []
        exercicios = []
        consumidas = 0
        gastas = 0
        
        for log in logs:
            if log["tipo"] == "refeicao":
                refeicoes.append(f"- {log['tag']}: {log['descricao']} ({log['calorias']} kcal)")
                consumidas += log["calorias"]
            else:
                exercicios.append(f"- {log['descricao']} ({log['calorias']} kcal gastas)")
                gastas += log["calorias"]
                
        saldo = meta - consumidas + gastas
        
        refeicoes_texto = "\n".join(refeicoes) if refeicoes else "Nenhuma refeição registrada."
        exercicios_texto = "\n".join(exercicios) if exercicios else "Nenhum exercício registrado neste dia."

        prompt = f"""
        Analise o dia completo deste usuário:
        Data: {data}
        Meta calórica diária: {meta} kcal
        
        Refeições consumidas:
        {refeicoes_texto}
        Total consumido: {consumidas} kcal
        
        Exercícios:
        {exercicios_texto}
        Total gasto extra: {gastas} kcal
        
        Saldo final (Meta - Consumidas + Gastas): {saldo} kcal. 
        (Se o saldo for positivo, ficou em déficit. Se negativo, estourou as calorias).
        
        Faça uma avaliação clínica, nutricional e fisiológica do dia deste paciente. Comente sobre o balanço energético (se manteve o déficit, superávit ou manutenção) e a qualidade das escolhas.
        
        MUITO IMPORTANTE: A resposta deve ser ALTAMENTE PROFISSIONAL, clínica, acolhedora e construtiva. Dirija-se a {usuario['nome']} (sexo: {usuario['sexo']}). NÃO FAÇA ZOEIRAS, piadas ou deboches. Aja como um verdadeiro Médico Esportivo e Nutricionista que está acompanhando o paciente de perto. Mostre o que foi bom e o que precisa ser ajustado cientificamente.
        
        Responda apenas com o texto da análise, direto ao ponto. Sem formatação markdown de código json, só o texto mesmo (você vai retornar um JSON com um campo "analise").
        """
        
        schema = {
            "type": "OBJECT",
            "properties": {
                "analise": {"type": "STRING"}
            },
            "required": ["analise"]
        }
        
        data_res = chamar_ia_com_retry(
            prompt=prompt,
            schema=schema,
            system_instruction=f"Você é a NuriIA, uma assistente altamente profissional (Médico Esportivo + Nutricionista). O paciente é {usuario['nome']} (sexo: {usuario['sexo']}). Nenhuma zoeira ou piada é permitida."
        )
        return data_res
    finally:
        conn.close()

@app.get("/api/logs/dica-do-dia/{data}")
def dica_do_dia(data: str, usuario: dict = Depends(obter_usuario_atual)):
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("SELECT descricao, calorias FROM logs WHERE usuario_id = ? AND date(data_hora) = ? AND tipo = 'refeicao'", (usuario["id"], data))
        logs = c.fetchall()

        if not logs:
            return {"dica": "Ainda não há registros para hoje. Comece adicionando sua primeira refeição para receber orientações personalizadas."}

        refeicoes = "\n".join([f"- {l['descricao']} ({l['calorias']} kcal)" for l in logs])

        prompt = f"""
        Baseado no que o usuário comeu até agora hoje:
        {refeicoes}
        
        Escreva UMA ÚNICA FRASE (curta e direta) com um insight profissional sobre nutrição ou medicina esportiva aplicável ao dia de {usuario['nome']}.
        Exemplo: "Seu consumo de proteínas está baixo, tente incluir uma fonte magra na próxima refeição." ou "Lembre-se de manter a hidratação constante."
        Seja técnico, clínico e acolhedor, SEM QUALQUER ZOEIRA.
        """
        
        schema = {
            "type": "OBJECT",
            "properties": {
                "dica": {"type": "STRING"}
            },
            "required": ["dica"]
        }
        
        data_res = chamar_ia_com_retry(
            prompt=prompt,
            schema=schema,
            system_instruction=f"Você é a NuriIA, assistente profissional (Médico/Nutricionista). O paciente é {usuario['nome']} ({usuario['sexo']}). Seja técnica e objetiva, sem brincadeiras."
        )
        return data_res
    finally:
        conn.close()

@app.get("/api/logs/analisar-semana")
def analisar_semana(usuario: dict = Depends(obter_usuario_atual)):
    conn = get_db()
    try:
        # Obter perfil
        perfil = conn.execute("SELECT meta_calorica FROM perfis WHERE usuario_id = ?", (usuario["id"],)).fetchone()
        meta = perfil["meta_calorica"] if perfil else 2000

        hoje = date.today()
        ha_7_dias = hoje - timedelta(days=6)
        
        c = conn.cursor()
        c.execute('''
            SELECT date(data_hora) as data_dia, tipo, SUM(calorias) as total_calorias
            FROM logs 
            WHERE usuario_id = ? AND date(data_hora) >= ? AND date(data_hora) <= ?
            GROUP BY date(data_hora), tipo
            ORDER BY data_dia ASC
        ''', (usuario["id"], ha_7_dias.isoformat(), hoje.isoformat()))
        
        resultados = c.fetchall()
        
        resumo = {}
        for row in resultados:
            dia = row['data_dia']
            if dia not in resumo:
                resumo[dia] = {'consumidas': 0, 'gastas': 0}
            if row['tipo'] == 'refeicao':
                resumo[dia]['consumidas'] += row['total_calorias'] or 0
            else:
                resumo[dia]['gastas'] += row['total_calorias'] or 0
                
        texto_semana = []
        for i in range(7):
            d = (ha_7_dias + timedelta(days=i)).isoformat()
            if d in resumo:
                saldo = meta - resumo[d]['consumidas'] + resumo[d]['gastas']
                status = "Déficit ok" if saldo >= 0 else "ESTOUROU"
                texto_semana.append(f"Dia {d}: Consumiu {resumo[d]['consumidas']} kcal | Gastou no treino {resumo[d]['gastas']} kcal | Saldo: {saldo} ({status})")
            else:
                texto_semana.append(f"Dia {d}: Sem registros.")
                
        historico = "\n".join(texto_semana)

        prompt = f"""
        Analise a última semana inteira deste usuário (últimos 7 dias):
        Meta calórica diária: {meta} kcal
        
        Histórico diário:
        {historico}
        
        Faça uma análise clínica do progresso semanal de {usuario['nome']}. Avalie a consistência metabólica, o controle de calorias e o impacto de eventuais faltas de registro no acompanhamento fisiológico.
        
        MUITO IMPORTANTE: Seja altamente profissional, construtivo e embasado. Fale como o Médico Esportivo/Nutricionista do paciente analisando os resultados da semana. Não use zoeiras, deboches ou linguagem informal. Mantenha a ética e o direcionamento para resultados.
        """
        
        schema = {
            "type": "OBJECT",
            "properties": {
                "analise": {"type": "STRING"}
            },
            "required": ["analise"]
        }
        
        data_res = chamar_ia_com_retry(
            prompt=prompt,
            schema=schema,
            system_instruction=f"Você é a NuriIA, Médica Esportiva e Nutricionista Clínica de elite. O paciente se chama {usuario['nome']} (sexo: {usuario['sexo']}). Forneça análises de alto nível profissional sem deboche."
        )
        return data_res
    finally:
        conn.close()

# ==================== SERVIR FRONTEND ====================

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def home():
    return FileResponse("static/index.html")

# ==================== ROTAS AGUA ====================

@app.post("/api/agua")
def registrar_agua(dados: AguaRequest, usuario: dict = Depends(obter_usuario_atual)):
    conn = get_db()
    try:
        data_hora_atual = datetime.now()
        if dados.data:
            try:
                dt = datetime.strptime(dados.data, "%Y-%m-%d")
                data_hora_atual = data_hora_atual.replace(year=dt.year, month=dt.month, day=dt.day)
            except ValueError:
                pass
                
        conn.execute('''
            INSERT INTO agua (usuario_id, quantidade_ml, data_hora)
            VALUES (?, ?, ?)
        ''', (usuario["id"], dados.quantidade_ml, data_hora_atual.isoformat()))
        conn.commit()
        return {"sucesso": True}
    finally:
        conn.close()

@app.get("/api/agua/{data}")
def obter_agua(data: str, usuario: dict = Depends(obter_usuario_atual)):
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("SELECT SUM(quantidade_ml) as total FROM agua WHERE usuario_id = ? AND date(data_hora) = ?", (usuario["id"], data))
        result = c.fetchone()
        return {"total_ml": result["total"] or 0}
    finally:
        conn.close()
# ==================== INICIALIZAÇÃO ====================

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)