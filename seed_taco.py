import sqlite3
import json

DB_PATH = "banco.db"

# Exemplo de alimentos baseados na TACO (alguns adaptados de rótulos comuns)
ALIMENTOS = [
    # Cereais e Pães
    {"nome": "Arroz branco cozido", "categoria": "Cereais", "kcal": 130, "prot": 2.5, "carb": 28.1, "gord": 0.2, "fibra": 1.2},
    {"nome": "Arroz integral cozido", "categoria": "Cereais", "kcal": 124, "prot": 2.6, "carb": 25.8, "gord": 1.0, "fibra": 2.7},
    {"nome": "Feijão carioca cozido", "categoria": "Leguminosas", "kcal": 76, "prot": 4.8, "carb": 13.6, "gord": 0.5, "fibra": 8.5},
    {"nome": "Feijão preto cozido", "categoria": "Leguminosas", "kcal": 77, "prot": 4.5, "carb": 14.0, "gord": 0.5, "fibra": 8.4},
    {"nome": "Pão de forma tradicional", "categoria": "Pães", "kcal": 253, "prot": 8.0, "carb": 49.9, "gord": 1.9, "fibra": 2.3, "fonte": "Rótulo médio"},
    {"nome": "Pão de forma integral", "categoria": "Pães", "kcal": 253, "prot": 9.4, "carb": 46.3, "gord": 3.4, "fibra": 6.0, "fonte": "Rótulo médio"},
    {"nome": "Pão francês", "categoria": "Pães", "kcal": 300, "prot": 8.0, "carb": 58.6, "gord": 3.1, "fibra": 2.3},
    {"nome": "Aveia em flocos", "categoria": "Cereais", "kcal": 394, "prot": 13.9, "carb": 66.6, "gord": 8.5, "fibra": 9.1},
    {"nome": "Tapioca (goma)", "categoria": "Cereais", "kcal": 336, "prot": 0.0, "carb": 83.0, "gord": 0.0, "fibra": 0.0},
    {"nome": "Cuscuz de milho", "categoria": "Cereais", "kcal": 113, "prot": 2.2, "carb": 25.3, "gord": 0.2, "fibra": 1.5},

    # Carnes e Ovos
    {"nome": "Peito de frango grelhado/cozido", "categoria": "Carnes", "kcal": 165, "prot": 31.0, "carb": 0.0, "gord": 3.6, "fibra": 0.0},
    {"nome": "Carne bovina (patinho) grelhada", "categoria": "Carnes", "kcal": 219, "prot": 35.9, "carb": 0.0, "gord": 7.3, "fibra": 0.0},
    {"nome": "Carne bovina (músculo) cozido", "categoria": "Carnes", "kcal": 194, "prot": 31.2, "carb": 0.0, "gord": 6.7, "fibra": 0.0},
    {"nome": "Peixe (tilápia) grelhado", "categoria": "Carnes", "kcal": 128, "prot": 26.1, "carb": 0.0, "gord": 2.6, "fibra": 0.0},
    {"nome": "Ovo de galinha cozido/poché", "categoria": "Ovos", "kcal": 143, "prot": 12.6, "carb": 0.7, "gord": 9.5, "fibra": 0.0},
    {"nome": "Ovo de galinha frito", "categoria": "Ovos", "kcal": 216, "prot": 14.1, "carb": 0.8, "gord": 16.7, "fibra": 0.0, "fonte": "Estimativa com óleo"},

    # Laticínios e Frios
    {"nome": "Leite de vaca integral", "categoria": "Laticínios", "kcal": 60, "prot": 3.2, "carb": 4.5, "gord": 3.2, "fibra": 0.0, "unidade": "ml"},
    {"nome": "Leite de vaca desnatado", "categoria": "Laticínios", "kcal": 35, "prot": 3.4, "carb": 4.9, "gord": 0.1, "fibra": 0.0, "unidade": "ml"},
    {"nome": "Queijo muçarela", "categoria": "Laticínios", "kcal": 320, "prot": 22.0, "carb": 3.0, "gord": 24.0, "fibra": 0.0, "fonte": "Rótulo médio"},
    {"nome": "Queijo prato", "categoria": "Laticínios", "kcal": 360, "prot": 22.7, "carb": 2.0, "gord": 29.0, "fibra": 0.0},
    {"nome": "Queijo minas frescal", "categoria": "Laticínios", "kcal": 243, "prot": 17.4, "carb": 3.2, "gord": 17.8, "fibra": 0.0},
    {"nome": "Requeijão tradicional", "categoria": "Laticínios", "kcal": 257, "prot": 9.6, "carb": 2.4, "gord": 23.4, "fibra": 0.0},
    {"nome": "Iogurte natural integral", "categoria": "Laticínios", "kcal": 61, "prot": 3.5, "carb": 4.7, "gord": 3.3, "fibra": 0.0},
    {"nome": "Iogurte natural desnatado", "categoria": "Laticínios", "kcal": 41, "prot": 3.8, "carb": 5.8, "gord": 0.3, "fibra": 0.0},
    {"nome": "Presunto cozido", "categoria": "Frios", "kcal": 105, "prot": 16.0, "carb": 1.0, "gord": 3.5, "fibra": 0.0, "fonte": "Rótulo médio"},
    {"nome": "Peito de peru defumado", "categoria": "Frios", "kcal": 98, "prot": 20.0, "carb": 1.5, "gord": 1.2, "fibra": 0.0, "fonte": "Rótulo médio"},
    
    # Suplementos
    {"nome": "Whey protein concentrado", "categoria": "Suplementos", "kcal": 400, "prot": 80.0, "carb": 6.0, "gord": 6.0, "fibra": 0.0, "fonte": "Rótulo médio"},
    {"nome": "Whey protein isolado", "categoria": "Suplementos", "kcal": 370, "prot": 90.0, "carb": 1.0, "gord": 1.0, "fibra": 0.0, "fonte": "Rótulo médio"},
    {"nome": "Creatina em pó", "categoria": "Suplementos", "kcal": 0, "prot": 0.0, "carb": 0.0, "gord": 0.0, "fibra": 0.0, "fonte": "Rótulo médio"},
    {"nome": "Pasta de amendoim integral", "categoria": "Suplementos/Doces", "kcal": 588, "prot": 25.0, "carb": 20.0, "gord": 49.0, "fibra": 8.0, "fonte": "Rótulo médio"},

    # Frutas
    {"nome": "Banana prata/nanica crua", "categoria": "Frutas", "kcal": 98, "prot": 1.3, "carb": 26.0, "gord": 0.1, "fibra": 2.0},
    {"nome": "Maçã fuji crua", "categoria": "Frutas", "kcal": 52, "prot": 0.3, "carb": 15.2, "gord": 0.0, "fibra": 1.3},
    {"nome": "Mamão papaia cru", "categoria": "Frutas", "kcal": 40, "prot": 0.5, "carb": 10.4, "gord": 0.1, "fibra": 1.0},
    {"nome": "Laranja baía crua", "categoria": "Frutas", "kcal": 37, "prot": 1.0, "carb": 8.9, "gord": 0.1, "fibra": 1.1},
    {"nome": "Abacate cru", "categoria": "Frutas", "kcal": 96, "prot": 1.2, "carb": 6.0, "gord": 8.4, "fibra": 6.3},

    # Bebidas e Outros
    {"nome": "Café sem açúcar (infusão)", "categoria": "Bebidas", "kcal": 1, "prot": 0.1, "carb": 0.0, "gord": 0.0, "fibra": 0.0, "unidade": "ml"},
    {"nome": "Açúcar cristal/refinado", "categoria": "Açúcares", "kcal": 387, "prot": 0.3, "carb": 99.5, "gord": 0.0, "fibra": 0.0},
    {"nome": "Óleo de soja", "categoria": "Óleos", "kcal": 884, "prot": 0.0, "carb": 0.0, "gord": 100.0, "fibra": 0.0, "unidade": "ml"},
    {"nome": "Azeite de oliva extra virgem", "categoria": "Óleos", "kcal": 884, "prot": 0.0, "carb": 0.0, "gord": 100.0, "fibra": 0.0, "unidade": "ml"},
    {"nome": "Refrigerante tipo cola", "categoria": "Bebidas", "kcal": 42, "prot": 0.0, "carb": 10.5, "gord": 0.0, "fibra": 0.0, "unidade": "ml", "fonte": "Rótulo médio"},
    {"nome": "Refrigerante tipo cola zero", "categoria": "Bebidas", "kcal": 0, "prot": 0.0, "carb": 0.0, "gord": 0.0, "fibra": 0.0, "unidade": "ml", "fonte": "Rótulo médio"},
]

# Conversões comuns de medidas caseiras por alimento
MEDIDAS = [
    {"alimento": "Arroz branco cozido", "unidade": "colher de sopa", "gramas": 25},
    {"alimento": "Arroz branco cozido", "unidade": "escumadeira", "gramas": 70},
    {"alimento": "Feijão carioca cozido", "unidade": "colher de sopa", "gramas": 20},
    {"alimento": "Feijão carioca cozido", "unidade": "concha", "gramas": 140},
    {"alimento": "Pão de forma tradicional", "unidade": "fatia", "gramas": 25},
    {"alimento": "Pão de forma integral", "unidade": "fatia", "gramas": 25},
    {"alimento": "Pão francês", "unidade": "unidade", "gramas": 50},
    {"alimento": "Aveia em flocos", "unidade": "colher de sopa", "gramas": 15},
    {"alimento": "Tapioca (goma)", "unidade": "colher de sopa", "gramas": 20},
    {"alimento": "Peito de frango grelhado/cozido", "unidade": "bife", "gramas": 100},
    {"alimento": "Peito de frango grelhado/cozido", "unidade": "filezinho", "gramas": 40},
    {"alimento": "Ovo de galinha cozido/poché", "unidade": "unidade", "gramas": 50},
    {"alimento": "Ovo de galinha frito", "unidade": "unidade", "gramas": 50},
    {"alimento": "Leite de vaca integral", "unidade": "copo", "gramas": 200},
    {"alimento": "Leite de vaca integral", "unidade": "xícara", "gramas": 240},
    {"alimento": "Queijo muçarela", "unidade": "fatia", "gramas": 20},
    {"alimento": "Queijo prato", "unidade": "fatia", "gramas": 20},
    {"alimento": "Queijo minas frescal", "unidade": "fatia", "gramas": 30},
    {"alimento": "Requeijão tradicional", "unidade": "colher de sopa", "gramas": 30},
    {"alimento": "Presunto cozido", "unidade": "fatia", "gramas": 15},
    {"alimento": "Peito de peru defumado", "unidade": "fatia", "gramas": 15},
    {"alimento": "Whey protein concentrado", "unidade": "scoop", "gramas": 30},
    {"alimento": "Whey protein isolado", "unidade": "scoop", "gramas": 30},
    {"alimento": "Creatina em pó", "unidade": "scoop", "gramas": 3},
    {"alimento": "Pasta de amendoim integral", "unidade": "colher de sopa", "gramas": 15},
    {"alimento": "Banana prata/nanica crua", "unidade": "unidade", "gramas": 70},
    {"alimento": "Maçã fuji crua", "unidade": "unidade", "gramas": 130},
    {"alimento": "Mamão papaia cru", "unidade": "fatia", "gramas": 100},
    {"alimento": "Laranja baía crua", "unidade": "unidade", "gramas": 150},
    {"alimento": "Café sem açúcar (infusão)", "unidade": "xícara", "gramas": 50},
    {"alimento": "Açúcar cristal/refinado", "unidade": "colher de chá", "gramas": 5},
    {"alimento": "Açúcar cristal/refinado", "unidade": "colher de sopa", "gramas": 14},
    {"alimento": "Óleo de soja", "unidade": "colher de sopa", "gramas": 8},
    {"alimento": "Azeite de oliva extra virgem", "unidade": "colher de sopa", "gramas": 8},
    {"alimento": "Refrigerante tipo cola", "unidade": "lata", "gramas": 350},
    {"alimento": "Refrigerante tipo cola zero", "unidade": "lata", "gramas": 350},
]

def seed_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Limpar tabelas para reiniciar do zero
    c.execute("DELETE FROM medidas_caseiras")
    c.execute("DELETE FROM alimentos")
    
    # Inserir Alimentos
    nome_to_id = {}
    for item in ALIMENTOS:
        unidade_ref = item.get("unidade", "g")
        fonte = item.get("fonte", "TACO")
        
        c.execute('''
            INSERT INTO alimentos (nome, categoria, kcal_100g, proteinas_100g, carboidratos_100g, gorduras_100g, fibras_100g, unidade_referencia, fonte)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (item["nome"], item["categoria"], item["kcal"], item["prot"], item["carb"], item["gord"], item["fibra"], unidade_ref, fonte))
        
        item_id = c.lastrowid
        nome_to_id[item["nome"]] = item_id

        # Para todos, adicionar 1 g = 1 e 1 ml = 1 se for o caso
        if unidade_ref == 'g':
            c.execute("INSERT INTO medidas_caseiras (alimento_id, unidade, gramas) VALUES (?, ?, ?)", (item_id, "g", 1))
            c.execute("INSERT INTO medidas_caseiras (alimento_id, unidade, gramas) VALUES (?, ?, ?)", (item_id, "gramas", 1))
            c.execute("INSERT INTO medidas_caseiras (alimento_id, unidade, gramas) VALUES (?, ?, ?)", (item_id, "grama", 1))
        else:
            c.execute("INSERT INTO medidas_caseiras (alimento_id, unidade, gramas) VALUES (?, ?, ?)", (item_id, "ml", 1))
            c.execute("INSERT INTO medidas_caseiras (alimento_id, unidade, gramas) VALUES (?, ?, ?)", (item_id, "mls", 1))

    # Inserir Medidas Caseiras Específicas
    for m in MEDIDAS:
        if m["alimento"] in nome_to_id:
            a_id = nome_to_id[m["alimento"]]
            c.execute("INSERT INTO medidas_caseiras (alimento_id, unidade, gramas) VALUES (?, ?, ?)", (a_id, m["unidade"], m["gramas"]))
    
    conn.commit()
    conn.close()
    print("Seed completo! Alimentos e medidas injetados no banco.")

if __name__ == "__main__":
    seed_db()
