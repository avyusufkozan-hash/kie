"""
ASGI application for Kozan İçtihat Entegresi (KİE) (simple deployment variant).

This is a minimal ASGI application that can be run with:
    uvicorn app:app --host 0.0.0.0 --port 8000

The MCP server will be available at:
    http://localhost:8000/mcp/

For the FastAPI-wrapped variant with CORS and extra metadata routes,
see asgi_app.py instead.

--- PAYLAŞILAN GİZLİ ANAHTAR KORUMASI (11.08.2026) --------------------------
Bu dosya `Dockerfile`'ın gerçekten çalıştırdığı dosyadır (`CMD ... app:app`).
Koruma HAM ASGI ara katmanıyla kurulur. Starlette'in `starlette.middleware.base`
modülündeki taban ara katman sınıfı BİLEREK KULLANILMAMIŞTIR — o sınıf yanıtı
tamponlar ve KİE'nin Streamable HTTP (SSE) akışını bozardı.

Anahtar İKİ yoldan kabul edilir:
  · `X-KIE-Anahtar` başlığı   → AvuSync köprüsü (backend/ictihat.js)
  · `?anahtar=<değer>`        → Av. Kozan'ın kendi Claude özel bağlantısı
                                (özel bağlantı yalnız adres alır, başlık ekleyemez)

Anahtar `KIE_ANAHTAR` ortam değişkeninden okunur. Kaynağa ASLA yazılmaz —
depo herkese açıktır. Değişken tanımsızsa sunucu açık kalır (geliştirme
kolaylığı) ama `/health` bunu `"koruma": "YOK"` diye açıkça söyler.
----------------------------------------------------------------------------
"""

import json
import os
import secrets
from urllib.parse import unquote_to_bytes

from starlette.responses import JSONResponse
from mcp_server_main import create_app

mcp = create_app()

# --- Anahtar: yalnız ortam değişkeninden. Kaynakta sabit değer YOK. ---------
KIE_ANAHTAR = os.getenv("KIE_ANAHTAR", "").strip()
_ANAHTAR = KIE_ANAHTAR.encode("utf-8")

# Serbest yollar: yalnız /health. (`/` ve `/status` bu dosyada TANIMLI DEĞİL —
# onlar asgi_app.py'nin uçlarıdır; var olmayan yolu serbest listesine yazma.)
SERBEST_YOLLAR = frozenset({"/health"})


@mcp.custom_route("/health", methods=["GET"])
async def health_check(request):
    """Health check endpoint for monitoring services (Fly.io, Render, etc.)."""
    return JSONResponse({
        "status": "healthy",
        "service": "Kozan İçtihat Entegresi (KİE)",
        "version": "0.2.1",
        # Sessiz korumasızlık olmayacak: durum burada açıkça söylenir.
        "koruma": "acik" if _ANAHTAR else "YOK",
    })


class AnahtarKontrol:
    """Ham ASGI ara katmanı — paylaşılan gizli anahtar denetimi.

    Ham ASGI'dir: `send`/`receive` olaylarını olduğu gibi aktarır, gövdeyi
    tamponlamaz. Bu yüzden Streamable HTTP / SSE akışı bozulmaz.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        # 1) HTTP olmayan her şey (özellikle `lifespan`) dokunulmadan geçer.
        #    Bloklanırsa sunucu hiç açılmaz.
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
        """Karşılaştırma BYTE üzerinden ve `secrets.compare_digest` ile.

        Metin karşılaştırmada ASCII dışı bir başlık gelirse `TypeError` fırlar,
        ara katman çöker ve 401 yerine 500 dönerdi. Bayt karşılaştırmada bu risk yok.
        """
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
        # `backend/ictihat.js` tam olarak 401 bekliyor (kimlik hatası, 502 değil).
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


# Create ASGI app directly from FastMCP server — sarmalama EN DIŞTA.
app = AnahtarKontrol(mcp.http_app())

# Endpoints:
# - /mcp/   - MCP server (Streamable HTTP transport, default FastMCP path) [KORUMALI]
# - /health - Health check for monitoring                                  [SERBEST]
