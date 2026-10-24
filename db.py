"""
Módulo de Conexão com Banco de Dados (Dual-Engine: Supabase PostgreSQL & SQLite)
NutriIA
================================================================================
Se a variável DATABASE_URL estiver configurada no .env (ou nas variáveis de
ambiente da Vercel / Cloud), utiliza PostgreSQL (Supabase).
Caso contrário, utiliza SQLite local (banco.db) para desenvolvimento offline.
"""

import os
import sqlite3
import re
from typing import Optional, Any, List, Dict

DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    # Compatibilidade com URLs do SQLAlchemy / psycopg2
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

IS_POSTGRES = bool(DATABASE_URL)

DB_PATH = "banco.db"

# ==================== POSTGRESQL / SUPABASE WRAPPER ====================

if IS_POSTGRES:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    class PostgresCursorWrapper:
        def __init__(self, raw_cursor):
            self.cursor = raw_cursor
            self.lastrowid = None
            self._last_result = None

        @property
        def rowcount(self):
            return self.cursor.rowcount

        def _adapt_sql(self, sql: str) -> str:
            # Converte placeholders ? (SQLite) para %s (PostgreSQL)
            adapted = re.sub(r"\?", "%s", sql)
            # Se for INSERT e não tiver RETURNING, adiciona RETURNING id para suportar lastrowid
            clean_sql = adapted.strip()
            sem_id = any(t in clean_sql.upper() for t in ("RATE_LIMITS", "IA_CACHE"))  # tabelas sem coluna id
            if clean_sql.upper().startswith("INSERT INTO") and "RETURNING" not in clean_sql.upper() and not sem_id:
                clean_sql = clean_sql.rstrip("; ") + " RETURNING id"
            return clean_sql

        def execute(self, sql: str, params=()):
            adapted_sql = self._adapt_sql(sql)
            self.cursor.execute(adapted_sql, params or ())
            
            # Se for INSERT com RETURNING id, captura lastrowid
            if "RETURNING id" in adapted_sql.upper():
                try:
                    res = self.cursor.fetchone()
                    if res and "id" in res:
                        self.lastrowid = res["id"]
                except Exception:
                    pass
            return self

        def fetchone(self):
            return self.cursor.fetchone()

        def fetchall(self):
            return self.cursor.fetchall()

        def close(self):
            self.cursor.close()

    class PostgresConnectionWrapper:
        def __init__(self, raw_conn):
            self.conn = raw_conn

        def cursor(self):
            return PostgresCursorWrapper(self.conn.cursor(cursor_factory=RealDictCursor))

        def execute(self, sql: str, params=()):
            c = self.cursor()
            c.execute(sql, params)
            return c

        def commit(self):
            self.conn.commit()

        def rollback(self):
            self.conn.rollback()

        def close(self):
            self.conn.close()

# ==================== FACTORY ====================

def get_db():
    """Retorna uma conexão compatível com Supabase PostgreSQL ou SQLite local."""
    global DATABASE_URL, IS_POSTGRES
    
    # Re-verifica caso tenha mudado em tempo de execução
    current_db_url = os.environ.get("DATABASE_URL")
    if current_db_url and current_db_url.startswith("postgres://"):
        current_db_url = current_db_url.replace("postgres://", "postgresql://", 1)
        
    if current_db_url:
        import psycopg2
        raw_conn = psycopg2.connect(current_db_url, connect_timeout=15)
        return PostgresConnectionWrapper(raw_conn)
    else:
        conn = sqlite3.connect(DB_PATH, timeout=20.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

def init_db():
    """Inicializa as tabelas no banco de dados ativo (PostgreSQL ou SQLite)."""
    current_db_url = os.environ.get("DATABASE_URL")
    if current_db_url:
        print("[DATABASE] Conectado ao Supabase (PostgreSQL). Tabelas gerenciadas pelo Supabase.")
        # No Supabase as tabelas são criadas pelo script supabase_setup.sql
        return

    # SQLite Local
    conn = get_db()
    c = conn.cursor()

    c.execute('''
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            senha_hash TEXT NOT NULL,
            criado_em TEXT DEFAULT CURRENT_TIMESTAMP,
            foto_perfil TEXT
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS perfis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id INTEGER NOT NULL UNIQUE,
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
            atualizado_em TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id INTEGER NOT NULL,
            tipo TEXT NOT NULL,
            titulo TEXT NOT NULL,
            descricao TEXT NOT NULL,
            detalhes TEXT NOT NULL,
            calorias REAL NOT NULL,
            cor TEXT NOT NULL,
            tag TEXT NOT NULL,
            data_hora TEXT DEFAULT CURRENT_TIMESTAMP,
            proteinas_g REAL DEFAULT 0,
            carboidratos_g REAL DEFAULT 0,
            gorduras_g REAL DEFAULT 0,
            dica_ia TEXT,
            qualidade_json TEXT,
            nivel_confianca TEXT,
            itens_detalhados_json TEXT,
            FOREIGN KEY(usuario_id) REFERENCES usuarios(id)
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS agua (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario_id INTEGER NOT NULL,
            quantidade_ml INTEGER NOT NULL,
            data_hora TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(usuario_id) REFERENCES usuarios(id)
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS alimentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            categoria TEXT,
            kcal_100g REAL NOT NULL,
            proteinas_100g REAL NOT NULL,
            carboidratos_100g REAL NOT NULL,
            gorduras_100g REAL NOT NULL,
            fibras_100g REAL DEFAULT 0,
            unidade_referencia TEXT DEFAULT 'g',
            fonte TEXT DEFAULT 'TACO'
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS medidas_caseiras (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alimento_id INTEGER NOT NULL,
            unidade TEXT NOT NULL,
            gramas REAL NOT NULL,
            FOREIGN KEY(alimento_id) REFERENCES alimentos(id)
        )
    ''')

    conn.commit()
    conn.close()


# ==================== TABELAS AUXILIARES (rate limit compartilhado e cache da IA) ====================

_AUX_OK = False


def garantir_tabelas_auxiliares():
    """Cria (se não existirem) as tabelas rate_limits e ia_cache. Idempotente e executa uma vez por processo."""
    global _AUX_OK
    if _AUX_OK:
        return
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("CREATE TABLE IF NOT EXISTS rate_limits (chave TEXT NOT NULL, ts DOUBLE PRECISION NOT NULL)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_rate_limits_chave_ts ON rate_limits (chave, ts)")
        c.execute("CREATE TABLE IF NOT EXISTS ia_cache (chave TEXT PRIMARY KEY, resposta TEXT NOT NULL, criado_em DOUBLE PRECISION NOT NULL)")
        if os.environ.get("DATABASE_URL"):
            # Impede acesso pela API pública (PostgREST/anon) do Supabase; o app usa a conexão direta
            c.execute("ALTER TABLE rate_limits ENABLE ROW LEVEL SECURITY")
            c.execute("ALTER TABLE ia_cache ENABLE ROW LEVEL SECURITY")
        conn.commit()
        _AUX_OK = True
    finally:
        conn.close()
