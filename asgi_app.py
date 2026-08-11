"""
ASGI application for Kozan İçtihat Entegresi (KİE)

This module provides ASGI/HTTP access to the KİE server,
allowing it to be deployed as a web service with FastAPI wrapper.

Usage:
    uvicorn asgi_app:app --host 0.0.0.0 --port 8000

--- PAYLAŞILAN GİZLİ ANAHTAR KORUMASI (11.08.2026) --------------------------
⚠ BUGÜN BU DOSYA ÇALIŞMIYOR. `Dockerfile` `app:app` diyor, yani canlıdaki
dosya `app.py`'dir (ölçüldü: canlı `/health` → version 0.2.1, üç alan;
bu dosya 0.1.0 + `tools_count` döndürür). Bu dosya ölü koddur.

Yine de yamalanmıştır (§4-b kararı): ileride biri `Dockerfile`'ı bu dosyaya
çevirirse koruma SESSİZCE kalkmasın diye. `app.py` ile aynı ara katman
bilerek KOPYALANMIŞTIR — ortak modüle taşımak `Dockerfile`'a yeni bir
`COPY` satırı gerektirirdi; unutulursa dağıtım kırılır. İki dosyadan birini
değiştiren diğerini de değiştirmelidir.
----------------------------------------------------------------------------
"""

import os
import json
import logging
import secrets
from urllib.parse import unquote_to_bytes

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware

from mcp_server_main import create_app

# Setup logging
logger = logging.getLogger(__name__)

# Configure CORS
cors_origins = os.getenv("ALLOWED_ORIGINS", "*").split(",")

# --- Anahtar: yalnız ortam değişkeninden. Kaynakta sabit değer YOK. ---------
KIE_ANAHTAR = os.getenv("KIE_ANAHTAR", "").strip()
_ANAHTAR = KIE_ANAHTAR.encode("utf-8")

# Serbest yollar: yalnız /health. `/` ve `/status` bu dosyada tanımlıdır ama
# BİLEREK korunur — `/status` sunucunun tüm araç listesini döker.
SERBEST_YOLLAR = frozenset({"/health"})

# Create MCP app
mcp_server = create_app()

# Create MCP Starlette sub-application
mcp_app = mcp_server.http_app(path="/")


# Configure JSON encoder for proper Turkish character support
class UTF8JSONResponse(JSONResponse):
    def __init__(self, content=None, status_code=200, headers=None, **kwargs):
        if headers is None:
            headers = {}
        headers["Content-Type"] = "application/json; charset=utf-8"
        super().__init__(content, status_code, headers, **kwargs)

    def render(self, content) -> bytes:
        return json.dumps(
            content,
            ensure_ascii=False,
            allow_nan=False,
            indent=None,
            separators=(",", ":"),
        ).encode("utf-8")


class AnahtarKontrol:
    """Ham ASGI ara katmanı — paylaşılan gizli anahtar denetimi.

    Starlette'in `starlette.middleware.base` modülündeki taban ara katman
    sınıfı BİLEREK KULLANILMAMIŞTIR: o sınıf yanıtı tamponlar ve KİE'nin
    Streamable HTTP (SSE) akışını bozar. Bu sınıf `send`/`receive` olaylarını
    olduğu gibi aktarır.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        # 1) HTTP olmayan her şey (özellikle `lifespan`) dokunulmadan geçer.
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # 2) Anahtar tanımsızsa koruma yok — sunucu açık kalır (/health söyler).
        if not _ANAHTAR:
            await self.app(scope, receive, send)
            return

        # 3) Ön-uçuş (preflight) serbest.
        if scope.get("method") == "OPTIONS":
            await self.app(scope, receive, send)
            return

        # 4) Serbest yollar.
        if self._yol(scope) in SERBEST_YOLLAR:
            await self.app(scope, receive, send)
            return

        # 5) Anahtar denetimi.
        if self._gecerli(scope):
            await self.app(scope, receive, send)
            return

        await self._reddet(send)

    @staticmethod
    def _yol(scope) -> str:
        yol = scope.get("path") or "/"
        return yol.rstrip("/") or "/"

    @staticmethod
    def _gecerli(scope) -> bool:
        """Karşılaştırma BYTE üzerinden ve `secrets.compare_digest` ile."""
        # a) X-KIE-Anahtar başlığı (AvuSync köprüsü)
        for ad, deger in scope.get("headers") or ():
            if ad == b"x-kie-anahtar" and secrets.compare_digest(deger.strip(), _ANAHTAR):
                return True

        # b) ?anahtar=<değer> sorgu dizesi (Av. Kozan'ın kendi Claude bağlantısı)
        for parca in (scope.get("query_string") or b"").split(b"&"):
            ad, ayrac, deger = parca.partition(b"=")
            if ad == b"anahtar" and ayrac:
                aday = unquote_to_bytes(deger.replace(b"+", b" ")).strip()
                if secrets.compare_digest(aday, _ANAHTAR):
                    return True

        return False

    @staticmethod
    async def _reddet(send):
        govde = json.dumps(
            {"error": "yetkisiz", "detay": "X-KIE-Anahtar geçersiz"},
            ensure_ascii=False,
        ).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(govde)).encode("ascii")),
            ],
        })
        await send({"type": "http.response.body", "body": govde})


custom_middleware = [
    Middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-Request-ID", "X-Session-ID", "X-KIE-Anahtar"],
    ),
]

# Create FastAPI wrapper application
fastapi_app = FastAPI(
    title="Kozan İçtihat Entegresi (KİE)",
    description="MCP server for Turkish legal databases",
    version="0.1.0",
    middleware=custom_middleware,
    default_response_class=UTF8JSONResponse,
    redirect_slashes=False,
)


@fastapi_app.get("/health")
async def health_check():
    """Health check endpoint for monitoring"""
    return {
        "status": "healthy",
        "service": "Kozan İçtihat Entegresi (KİE)",
        "version": "0.1.0",
        "tools_count": len(mcp_server._tool_manager._tools),
        # Sessiz korumasızlık olmayacak: durum burada açıkça söylenir.
        "koruma": "acik" if _ANAHTAR else "YOK",
    }


@fastapi_app.api_route("/mcp", methods=["GET", "POST", "HEAD", "OPTIONS"])
async def redirect_to_slash(request: Request):
    """Redirect /mcp to /mcp/ preserving HTTP method with 308"""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/mcp/", status_code=308)


@fastapi_app.get("/")
async def root():
    """Root endpoint with service information"""
    return {
        "service": "Kozan İçtihat Entegresi (KİE)",
        "description": "MCP server for Turkish legal databases",
        "endpoints": {
            "mcp": "/mcp",
            "health": "/health",
            "status": "/status",
        },
        "transports": {
            "http": "/mcp"
        },
        "supported_databases": [
            "Yargıtay (Court of Cassation)",
            "Danıştay (Council of State)",
            "Emsal (Precedent)",
            "Uyuşmazlık Mahkemesi (Court of Jurisdictional Disputes)",
            "Anayasa Mahkemesi (Constitutional Court)",
            "Kamu İhale Kurulu (Public Procurement Authority)",
            "Rekabet Kurumu (Competition Authority)",
            "Sayıştay (Court of Accounts)",
            "KVKK (Personal Data Protection Authority)",
            "BDDK (Banking Regulation and Supervision Agency)",
            "BTK (Information and Communication Technologies Authority)",
            "Bedesten API (Multiple courts)",
            "Sigorta Tahkim Komisyonu (Insurance Arbitration Commission)",
        ],
    }


@fastapi_app.get("/status")
async def status():
    """Status endpoint with detailed information"""
    tools = []
    for tool in mcp_server._tool_manager._tools.values():
        tools.append({
            "name": tool.name,
            "description": tool.description[:100] + "..." if len(tool.description) > 100 else tool.description
        })

    return {
        "status": "operational",
        "tools": tools,
        "total_tools": len(tools),
        "transport": "streamable_http",
    }


# Mount MCP app at /mcp/
fastapi_app.mount("/mcp/", mcp_app)

# Set the lifespan context after mounting
fastapi_app.router.lifespan_context = mcp_app.lifespan

# Anahtar denetimi EN DIŞTA — CORS ve yönlendirmeler dâhil her istek buradan geçer.
app = AnahtarKontrol(fastapi_app)

# Export for uvicorn
__all__ = ["app"]
