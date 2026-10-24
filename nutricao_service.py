import sqlite3
import difflib
import json
from typing import List, Dict, Any, Tuple

DB_PATH = "banco.db"

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def buscar_alimento(nome_buscado: str, cursor) -> sqlite3.Row:
    """Busca o alimento mais próximo no banco usando difflib ou LIKE."""
    cursor.execute("SELECT * FROM alimentos")
    alimentos = cursor.fetchall()
    
    if not alimentos:
        return None
        
    nomes = [a['nome'] for a in alimentos]
    matches = difflib.get_close_matches(nome_buscado.lower(), [n.lower() for n in nomes], n=1, cutoff=0.5)
    
    if matches:
        match_nome = matches[0]
        for a in alimentos:
            if a['nome'].lower() == match_nome:
                return a
    
    # Fallback para busca por palavra chave
    palavras = nome_buscado.split()
    for p in palavras:
        if len(p) > 3:
            cursor.execute("SELECT * FROM alimentos WHERE nome LIKE ?", (f"%{p}%",))
            resultado = cursor.fetchone()
            if resultado:
                return resultado
                
    return None

def buscar_conversao(alimento_id: int, unidade: str, cursor) -> float:
    """Busca a conversão de medida caseira para gramas."""
    unidade = unidade.lower().strip()
    
    # Se já for grama ou ml
    if unidade in ['g', 'grama', 'gramas', 'ml', 'mls']:
        return 1.0
        
    # Busca na tabela
    cursor.execute("SELECT gramas FROM medidas_caseiras WHERE alimento_id = ? AND unidade LIKE ?", (alimento_id, f"{unidade}%"))
    resultado = cursor.fetchone()
    if resultado:
        return resultado['gramas']
        
    # Tenta sem o 's' do plural
    if unidade.endswith('s'):
        cursor.execute("SELECT gramas FROM medidas_caseiras WHERE alimento_id = ? AND unidade LIKE ?", (alimento_id, f"{unidade[:-1]}%"))
        resultado = cursor.fetchone()
        if resultado:
            return resultado['gramas']
            
    # Tenta bater com palavras chave comuns na unidade (ex: "colher" -> "colher de sopa")
    cursor.execute("SELECT gramas, unidade FROM medidas_caseiras WHERE alimento_id = ?", (alimento_id,))
    todas = cursor.fetchall()
    for t in todas:
        if t['unidade'] in unidade or unidade in t['unidade']:
            return t['gramas']
            
    return None

def estimativa_generica(unidade: str) -> float:
    """Fallback quando o alimento não tem a medida cadastrada."""
    u = unidade.lower()
    if 'colher de sopa' in u or u == 'colher': return 15.0
    if 'colher de ch' in u: return 5.0
    if 'fatia' in u: return 25.0
    if 'xícara' in u or 'xicara' in u: return 200.0
    if 'copo' in u: return 200.0
    if 'concha' in u: return 100.0
    if 'escumadeira' in u: return 70.0
    if 'scoop' in u: return 30.0
    if 'porção' in u or 'porcao' in u: return 100.0
    if 'prato' in u: return 300.0
    if 'unidade' in u or 'und' in u: return 100.0
    if 'bife' in u or 'filé' in u or 'file' in u: return 100.0
    if 'pedaço' in u or 'pedaco' in u: return 50.0
    return 100.0 # Padrão genérico se falhar tudo
    
def estimativa_calorica_generica(nome_buscado: str, quantidade: float, gramas_totais: float) -> dict:
    """Fallback extremo: o alimento não existe na base de dados de forma alguma."""
    nome = nome_buscado.lower()
    kcal_100 = 150 # media genérica
    prot = 5
    carb = 15
    gord = 5
    
    if 'salada' in nome or 'alface' in nome or 'tomate' in nome:
        kcal_100, prot, carb, gord = 20, 1, 4, 0
    elif 'carne' in nome or 'frango' in nome or 'peixe' in nome or 'porco' in nome:
        kcal_100, prot, carb, gord = 200, 25, 0, 10
    elif 'doce' in nome or 'chocolate' in nome or 'bolo' in nome:
        kcal_100, prot, carb, gord = 400, 5, 50, 20
    elif 'pão' in nome or 'massa' in nome or 'macarrão' in nome:
        kcal_100, prot, carb, gord = 300, 10, 60, 5
    elif 'óleo' in nome or 'azeite' in nome or 'manteiga' in nome:
        kcal_100, prot, carb, gord = 880, 0, 0, 100
        
    fator = gramas_totais / 100.0
    
    return {
        "nome": nome_buscado,
        "kcal": round(kcal_100 * fator, 1),
        "prot": round(prot * fator, 1),
        "carb": round(carb * fator, 1),
        "gord": round(gord * fator, 1),
        "fibras": 0,
        "quantidade_original": quantidade,
        "unidade_original": "g/ml",
        "peso_g": round(gramas_totais, 1),
        "confianca": "Estimativa Aproximada",
        "fonte": "Heurística Genérica"
    }

def calcular_refeicao(alimentos_ia: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[Dict[str, Any]], str]:
    """
    Recebe do LLM: [{"nome": "pão", "quantidade": 2, "unidade": "fatia"}]
    Retorna: (Totais, ItensDetalhados, Nível de Confiança Geral)
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    totais = {"kcal": 0, "prot": 0, "carb": 0, "gord": 0, "fibras": 0}
    itens_detalhados = []
    
    confianca_geral = "Alta Confiança"
    
    for item in alimentos_ia:
        nome_buscado = item.get("nome", "")
        quantidade = float(item.get("quantidade", 1))
        unidade = item.get("unidade", "unidade")
        
        # 1. Verifica se a IA forneceu os macros no rótulo ("rotulo_kcal", etc)
        # Se for industrializado, a IA envia os valores baseados no rótulo.
        if item.get("rotulo_kcal") is not None:
            # Assumimos que o rótulo seja para a porção que o usuário disse
            kcal = float(item["rotulo_kcal"]) * quantidade
            prot = float(item.get("rotulo_prot", 0)) * quantidade
            carb = float(item.get("rotulo_carb", 0)) * quantidade
            gord = float(item.get("rotulo_gord", 0)) * quantidade
            
            detalhe = {
                "nome": f"{nome_buscado} (Rótulo do Usuário)",
                "nome_original": nome_buscado,
                "kcal": round(kcal, 1),
                "prot": round(prot, 1),
                "carb": round(carb, 1),
                "gord": round(gord, 1),
                "fibras": 0,
                "quantidade_original": quantidade,
                "unidade_original": unidade,
                "peso_g": 0, # Ignora peso aqui já que veio direto
                "confianca": "Alta Confiança",
                "fonte": "Fornecido pelo Usuário"
            }
            itens_detalhados.append(detalhe)
            
            totais["kcal"] += detalhe["kcal"]
            totais["prot"] += detalhe["prot"]
            totais["carb"] += detalhe["carb"]
            totais["gord"] += detalhe["gord"]
            continue

        alimento_db = buscar_alimento(nome_buscado, cursor)
        
        if alimento_db:
            fator_conversao = buscar_conversao(alimento_db['id'], unidade, cursor)
            
            if fator_conversao is not None:
                peso_g = quantidade * fator_conversao
                confianca = "Alta Confiança"
            else:
                peso_g = quantidade * estimativa_generica(unidade)
                confianca = "Estimativa (Unidade genérica)"
                if confianca_geral == "Alta Confiança": confianca_geral = "Estimativa"
                
            fator = peso_g / 100.0
            kcal = alimento_db['kcal_100g'] * fator
            prot = alimento_db['proteinas_100g'] * fator
            carb = alimento_db['carboidratos_100g'] * fator
            gord = alimento_db['gorduras_100g'] * fator
            fibras = alimento_db['fibras_100g'] * fator
            
            detalhe = {
                "nome": alimento_db['nome'],
                "nome_original": nome_buscado,
                "kcal": round(kcal, 1),
                "prot": round(prot, 1),
                "carb": round(carb, 1),
                "gord": round(gord, 1),
                "fibras": round(fibras, 1),
                "quantidade_original": quantidade,
                "unidade_original": unidade,
                "peso_g": round(peso_g, 1),
                "confianca": confianca,
                "fonte": alimento_db['fonte']
            }
        else:
            # Não encontrou o alimento de jeito nenhum
            peso_g = quantidade * estimativa_generica(unidade)
            detalhe = estimativa_calorica_generica(nome_buscado, quantidade, peso_g)
            detalhe["unidade_original"] = unidade
            if confianca_geral != "Estimativa Aproximada":
                confianca_geral = "Estimativa Aproximada"
            
        # Verifica se o estado de preparo adiciona calorias
        estado = item.get("estado", "").lower()
        if estado == "frito" and detalhe["gord"] < 10 and not ("frito" in detalhe["nome"].lower()):
            # Adiciona óleo genérico (1 colher sopa = 8g = 70kcal)
            detalhe["kcal"] += 70.6
            detalhe["gord"] += 8.0
            detalhe["confianca"] += " (+óleo)"
            if confianca_geral == "Alta Confiança": confianca_geral = "Estimativa"
            
        itens_detalhados.append(detalhe)
        
        totais["kcal"] += detalhe["kcal"]
        totais["prot"] += detalhe["prot"]
        totais["carb"] += detalhe["carb"]
        totais["gord"] += detalhe["gord"]
        totais["fibras"] += detalhe["fibras"]
        
    conn.close()
    
    # Arredondar totais
    for k in totais:
        totais[k] = round(totais[k], 1)
        
    return totais, itens_detalhados, confianca_geral
