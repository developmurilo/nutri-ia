import sqlite3
import os

DB_PATH = "banco.db"
OUTPUT_SQL = "supabase_setup.sql"

def sql_val(col, val):
    if val is None:
        return "NULL"
    if col == "foto_perfil" and len(str(val)) > 50000:
        # Fotos de câmera de celular em base64 possuem 3MB a 4MB cada.
        # No SQL Editor web do Supabase isso estoura o limite de payload HTTP (413).
        # Para migrar com as fotos completas, use o script python migrate_to_supabase.py.
        return "NULL"
    if isinstance(val, (int, float)):
        return str(val)
    # escape single quotes for SQL
    s = str(val).replace("'", "''")
    return f"'{s}'"

def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    with open(OUTPUT_SQL, "w", encoding="utf-8") as f:
        f.write("-- ========================================================\n")
        f.write("-- SCRIPT DE CONFIGURACAO E MIGRACAO PARA O SUPABASE\n")
        f.write("-- Como usar:\n")
        f.write("-- 1. Acesse seu painel no Supabase (supabase.com)\n")
        f.write("-- 2. Vá em 'SQL Editor' -> 'New query'\n")
        f.write("-- 3. Cole todo o conteudo deste arquivo e clique em 'RUN'\n")
        f.write("-- ========================================================\n\n")

        f.write("-- 1. Extensões úteis\n")
        f.write("CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\";\n\n")

        f.write("-- 2. Estrutura das Tabelas\n")
        f.write("""CREATE TABLE IF NOT EXISTS usuarios (
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

-- ========================================================
-- 3. Insercao de Dados Migrados
-- ========================================================
""")

        tables = ['usuarios', 'perfis', 'alimentos', 'medidas_caseiras', 'logs', 'agua']
        for t in tables:
            if t == "perfis":
                c.execute(f"SELECT * FROM {t} WHERE usuario_id IN (SELECT id FROM usuarios) ORDER BY id ASC")
            elif t == "logs":
                c.execute(f"SELECT * FROM {t} WHERE usuario_id IS NULL OR usuario_id IN (SELECT id FROM usuarios) ORDER BY id ASC")
            elif t == "agua":
                c.execute(f"SELECT * FROM {t} WHERE usuario_id IN (SELECT id FROM usuarios) ORDER BY id ASC")
            elif t == "medidas_caseiras":
                c.execute(f"SELECT * FROM {t} WHERE alimento_id IN (SELECT id FROM alimentos) ORDER BY id ASC")
            else:
                c.execute(f"SELECT * FROM {t} ORDER BY id ASC")
            rows = c.fetchall()
            if not rows:
                continue
            cols = list(rows[0].keys())
            cols_str = ", ".join(cols)
            f.write(f"\n-- Tabela: {t} ({len(rows)} registros)\n")
            for r in rows:
                vals = [sql_val(col, r[col]) for col in cols]
                vals_str = ", ".join(vals)
                f.write(f"INSERT INTO {t} ({cols_str}) VALUES ({vals_str}) ON CONFLICT (id) DO NOTHING;\n")

        f.write("\n-- ========================================================\n")
        f.write("-- 4. Sincronizacao das Sequences de Auto-Incremento\n")
        f.write("-- ========================================================\n")
        f.write("SELECT setval(pg_get_serial_sequence('usuarios', 'id'), COALESCE(MAX(id), 1)) FROM usuarios;\n")
        f.write("SELECT setval(pg_get_serial_sequence('perfis', 'id'), COALESCE(MAX(id), 1)) FROM perfis;\n")
        f.write("SELECT setval(pg_get_serial_sequence('logs', 'id'), COALESCE(MAX(id), 1)) FROM logs;\n")
        f.write("SELECT setval(pg_get_serial_sequence('agua', 'id'), COALESCE(MAX(id), 1)) FROM agua;\n")
        f.write("SELECT setval(pg_get_serial_sequence('alimentos', 'id'), COALESCE(MAX(id), 1)) FROM alimentos;\n")
        f.write("SELECT setval(pg_get_serial_sequence('medidas_caseiras', 'id'), COALESCE(MAX(id), 1)) FROM medidas_caseiras;\n")

    print(f"Exportacao concluida com sucesso em '{OUTPUT_SQL}'!")

if __name__ == "__main__":
    main()
