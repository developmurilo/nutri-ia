import os
import sqlite3
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

DB_SQLITE = "banco.db"
DATABASE_URL = os.environ.get("DATABASE_URL")

SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE IF NOT EXISTS usuarios (
    id SERIAL PRIMARY KEY,
    nome TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    senha_hash TEXT NOT NULL,
    criado_em TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    foto_perfil TEXT
);

CREATE TABLE IF NOT EXISTS perfis (
    id SERIAL PRIMARY KEY,
    usuario_id INTEGER NOT NULL UNIQUE REFERENCES usuarios(id) ON DELETE CASCADE,
    peso_kg REAL,
    altura_cm REAL,
    idade INTEGER,
    sexo TEXT,
    nivel_atividade REAL,
    objetivo TEXT,
    imc REAL,
    tmb REAL,
    gasto_diario REAL,
    meta_calorica REAL,
    atualizado_em TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS logs (
    id SERIAL PRIMARY KEY,
    usuario_id INTEGER REFERENCES usuarios(id) ON DELETE CASCADE,
    tipo TEXT NOT NULL,
    titulo TEXT NOT NULL,
    descricao TEXT NOT NULL,
    detalhes TEXT NOT NULL,
    calorias REAL NOT NULL,
    cor TEXT NOT NULL,
    tag TEXT NOT NULL,
    data_hora TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    proteinas_g REAL DEFAULT 0,
    carboidratos_g REAL DEFAULT 0,
    gorduras_g REAL DEFAULT 0,
    dica_ia TEXT,
    qualidade_json TEXT,
    nivel_confianca TEXT,
    itens_detalhados_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_logs_usuario_data ON logs(usuario_id, data_hora);

CREATE TABLE IF NOT EXISTS agua (
    id SERIAL PRIMARY KEY,
    usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    quantidade_ml INTEGER NOT NULL,
    data_hora TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_agua_usuario_data ON agua(usuario_id, data_hora);

CREATE TABLE IF NOT EXISTS alimentos (
    id SERIAL PRIMARY KEY,
    nome TEXT NOT NULL,
    categoria TEXT,
    kcal_100g REAL NOT NULL,
    proteinas_100g REAL NOT NULL,
    carboidratos_100g REAL NOT NULL,
    gorduras_100g REAL NOT NULL,
    fibras_100g REAL DEFAULT 0,
    unidade_referencia TEXT DEFAULT 'g',
    fonte TEXT DEFAULT 'TACO'
);
CREATE INDEX IF NOT EXISTS idx_alimentos_nome ON alimentos(nome);

CREATE TABLE IF NOT EXISTS medidas_caseiras (
    id SERIAL PRIMARY KEY,
    alimento_id INTEGER NOT NULL REFERENCES alimentos(id) ON DELETE CASCADE,
    unidade TEXT NOT NULL,
    gramas REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_medidas_caseiras_alimento ON medidas_caseiras(alimento_id);
"""

def migrate():
    if not DATABASE_URL:
        print("[ERRO] Variável DATABASE_URL não encontrada no seu arquivo .env!")
        print("Adicione a URL do Supabase no .env: DATABASE_URL=\"postgresql://postgres...\"")
        return

    print("=== INICIANDO MIGRAÇÃO PARA O SUPABASE ===")
    print("Conectando ao SQLite local...")
    conn_sq = sqlite3.connect(DB_SQLITE)
    conn_sq.row_factory = sqlite3.Row
    c_sq = conn_sq.cursor()

    print("Conectando ao Supabase (PostgreSQL)...")
    conn_pg = psycopg2.connect(DATABASE_URL)
    c_pg = conn_pg.cursor()

    print("Criando estrutura de tabelas e índices no Supabase...")
    c_pg.execute(SCHEMA_SQL)
    conn_pg.commit()

    tabelas = ['usuarios', 'perfis', 'alimentos', 'medidas_caseiras', 'logs', 'agua']
    total_migrado = 0

    for t in tabelas:
        if t == "perfis":
            c_sq.execute(f"SELECT * FROM {t} WHERE usuario_id IN (SELECT id FROM usuarios) ORDER BY id ASC")
        elif t == "logs":
            c_sq.execute(f"SELECT * FROM {t} WHERE usuario_id IS NULL OR usuario_id IN (SELECT id FROM usuarios) ORDER BY id ASC")
        elif t == "agua":
            c_sq.execute(f"SELECT * FROM {t} WHERE usuario_id IN (SELECT id FROM usuarios) ORDER BY id ASC")
        elif t == "medidas_caseiras":
            c_sq.execute(f"SELECT * FROM {t} WHERE alimento_id IN (SELECT id FROM alimentos) ORDER BY id ASC")
        else:
            c_sq.execute(f"SELECT * FROM {t} ORDER BY id ASC")
        rows = c_sq.fetchall()
        if not rows:
            print(f"-> Tabela '{t}': vazia (0 registros)")
            continue

        cols = list(rows[0].keys())
        cols_str = ", ".join(cols)
        placeholders = ", ".join(["%s"] * len(cols))

        sql_insert = f"INSERT INTO {t} ({cols_str}) VALUES ({placeholders}) ON CONFLICT (id) DO NOTHING"

        count = 0
        for r in rows:
            valores = [r[col] for col in cols]
            c_pg.execute(sql_insert, valores)
            count += 1

        conn_pg.commit()
        print(f"-> Tabela '{t}': {count} registros transferidos com sucesso!")
        total_migrado += count

    print("Sincronizando sequences de IDs no PostgreSQL...")
    for t in tabelas:
        c_pg.execute(f"SELECT setval(pg_get_serial_sequence('{t}', 'id'), COALESCE(MAX(id), 1)) FROM {t}")
    conn_pg.commit()

    conn_sq.close()
    conn_pg.close()

    print("====================================================")
    print(f"🎉 MIGRAÇÃO CONCLUÍDA! {total_migrado} registros gravados no Supabase.")
    print("====================================================")

if __name__ == "__main__":
    migrate()
