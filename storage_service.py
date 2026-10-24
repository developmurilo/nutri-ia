"""
Storage de fotos de perfil — Supabase Storage
=============================================
Evita guardar imagens em base64 dentro da tabela `usuarios` (deixava /api/auth/me pesado).
Usa a API REST do Supabase Storage (apenas biblioteca padrão).

Variáveis de ambiente:
  SUPABASE_URL          ex: https://abcdxyz.supabase.co
  SUPABASE_SERVICE_KEY  chave "service_role" (Project Settings > API). NUNCA exponha no front-end.
  SUPABASE_BUCKET       (opcional) nome do bucket, padrão "avatars" (criado automaticamente como público)
"""

import base64
import json
import os
import time
import urllib.error
import urllib.request
from typing import Optional

LIMITE_BYTES = 5 * 1024 * 1024


def storage_configurado() -> bool:
    return bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_KEY"))


def _base() -> str:
    return os.environ["SUPABASE_URL"].rstrip("/")


def _bucket() -> str:
    return os.environ.get("SUPABASE_BUCKET", "avatars")


def _headers(content_type: str) -> dict:
    chave = os.environ["SUPABASE_SERVICE_KEY"]
    return {"Authorization": f"Bearer {chave}", "apikey": chave, "Content-Type": content_type}


def _requisitar(url: str, dados: bytes, content_type: str, extra: dict = None, metodo: str = "POST"):
    headers = _headers(content_type)
    headers.update(extra or {})
    req = urllib.request.Request(url, data=dados, headers=headers, method=metodo)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.status


def _criar_bucket():
    corpo = json.dumps({"id": _bucket(), "name": _bucket(), "public": True}).encode()
    try:
        _requisitar(f"{_base()}/storage/v1/bucket", corpo, "application/json")
    except urllib.error.HTTPError as e:
        if e.code not in (400, 409):  # já existe
            raise


def salvar_avatar(usuario_id: int, data_uri: str) -> Optional[str]:
    """Envia a imagem (data URI base64) ao Storage e devolve a URL pública. None se falhar."""
    if not storage_configurado() or not data_uri or not data_uri.startswith("data:"):
        return None
    try:
        cabecalho, _, b64 = data_uri.partition(",")
        mime = cabecalho[5:].split(";")[0] or "image/jpeg"
        if not mime.startswith("image/"):
            return None
        conteudo = base64.b64decode(b64)
        if len(conteudo) > LIMITE_BYTES:
            print("[STORAGE] Imagem acima do limite de 5 MB.")
            return None

        nome = f"u{usuario_id}"
        url_upload = f"{_base()}/storage/v1/object/{_bucket()}/{nome}"
        try:
            _requisitar(url_upload, conteudo, mime, {"x-upsert": "true"})
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):  # bucket inexistente -> cria e tenta de novo
                _criar_bucket()
                _requisitar(url_upload, conteudo, mime, {"x-upsert": "true"})
            else:
                raise
        return f"{_base()}/storage/v1/object/public/{_bucket()}/{nome}?v={int(time.time())}"
    except Exception as e:
        print(f"[STORAGE] Falha ao enviar avatar: {e}")
        return None
