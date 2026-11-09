# bedesten_mcp_module/client.py

import asyncio
import base64
import io
import logging
import os
import time
from collections import OrderedDict
from typing import Optional

import httpx
from markitdown import MarkItDown

from .models import (
    BedestenSearchRequest, BedestenSearchResponse,
    BedestenDocumentRequest, BedestenDocumentResponse,
    BedestenDocumentMarkdown, BedestenDocumentRequestData
)
from .enums import get_full_birim_adi

logger = logging.getLogger(__name__)


class BedestenRateLimited(Exception):
    """Raised when the local rate-limit bucket would block longer than allowed.

    Carries the suggested retry-after (seconds) so callers can surface a
    structured 429-style response to the MCP client instead of silently
    blocking the event-loop slot for the full bucket-pause window.
    """

    def __init__(self, retry_after: float) -> None:
        self.retry_after = retry_after
        super().__init__(f"local bucket would block {retry_after:.1f}s")


class _TokenBucket:
    """Asyncio token bucket with explicit back-pressure.

    Measured Bedesten limit (per source IP, 2026-05-08): 10 requests per
    rolling 30s window with full refill — equivalent to capacity=10,
    refill_rate=1 token / 3s. Even with margin, 429s still leak through
    when other clients share the egress IP, so we also expose
    ``penalize_until`` so callers can freeze the bucket when the server
    actually returns 429 (Retry-After).
    """

    def __init__(self, capacity: int, refill_per_s: float) -> None:
        self.capacity = float(capacity)
        self.refill_per_s = float(refill_per_s)
        self._tokens = float(capacity)
        self._last = time.monotonic()
        self._not_before = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self, max_wait: Optional[float] = None) -> None:
        """Acquire one token. If ``max_wait`` is set and the next wait would
        exceed it, raise :class:`BedestenRateLimited` immediately instead of
        sleeping — keeps a single rate-limited request from holding the
        worker-slot for the full bucket-pause window (up to ~30s on 429)."""
        deadline = (time.monotonic() + max_wait) if max_wait is not None else None
        while True:
            async with self._lock:
                now = time.monotonic()
                if now < self._not_before:
                    wait_s = self._not_before - now
                else:
                    self._tokens = min(
                        self.capacity,
                        self._tokens + (now - self._last) * self.refill_per_s,
                    )
                    self._last = now
                    if self._tokens >= 1.0:
                        self._tokens -= 1.0
                        return
                    wait_s = (1.0 - self._tokens) / self.refill_per_s
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if wait_s > remaining:
                    raise BedestenRateLimited(retry_after=wait_s)
            await asyncio.sleep(wait_s)

    def penalize_until(self, monotonic_deadline: float) -> None:
        """Pause the bucket until ``monotonic_deadline`` (drains tokens)."""
        self._not_before = max(self._not_before, monotonic_deadline)
        self._tokens = 0.0
        self._last = time.monotonic()

class BedestenApiClient:
    """
    API Client for Bedesten (bedesten.adalet.gov.tr) - Alternative legal decision search system.
    Currently used for Yargıtay decisions, but can be extended for other court types.
    """
    BASE_URL = "https://bedesten.adalet.gov.tr"
    SEARCH_ENDPOINT = "/emsal-karar/searchDocuments"
    DOCUMENT_ENDPOINT = "/emsal-karar/getDocumentContent"
    
    # Measured limit (per source IP): 10 requests per 30s window with full
    # refill (≈ 1 token / 3s steady). We default to 1-token capacity and
    # 3.5s spacing (no burst, ~14% safety margin). Override via env:
    #   BEDESTEN_RATE_CAPACITY (default 1)
    #   BEDESTEN_RATE_REFILL_S (default 3.5; seconds per token)
    #   BEDESTEN_RATE_MAX_WAIT_S (default 8.0; max seconds to wait in the
    #     local bucket before returning a structured 429 to the caller)
    _DEFAULT_CAPACITY = int(os.getenv("BEDESTEN_RATE_CAPACITY", "1"))
    _DEFAULT_REFILL_S = float(os.getenv("BEDESTEN_RATE_REFILL_S", "3.5"))
    _DEFAULT_MAX_WAIT_S = float(os.getenv("BEDESTEN_RATE_MAX_WAIT_S", "8.0"))
    # 429'da sunucu kendisi bekler ve EN ÇOK BİR KEZ yeniden dener (09.11.2026):
    # bekleme süresi (Retry-After ya da yerel kova) bu sınırı aşarsa yeniden denenmez,
    # yapılandırılmış 429 cevabı eskisi gibi çağırana döner.
    _RETRY_MAX_S = float(os.getenv("BEDESTEN_RETRY_MAX_S", "8.0"))
    # Tam metin önbelleği: aynı karar tekrar istenince Bedesten'e gidilmez (dışarıdan görünmez).
    # Yalnız başarılı belgeler saklanır; sınırı aşınca en eski atılır.
    _ONBELLEK_ADET = int(os.getenv("BEDESTEN_ONBELLEK_ADET", "200"))
    _ONBELLEK_KRKT = int(os.getenv("BEDESTEN_ONBELLEK_KRKT", "10000000"))
    _ONBELLEK_SURE_S = float(os.getenv("BEDESTEN_ONBELLEK_SURE_S", "86400"))

    def __init__(self, request_timeout: float = 60.0):
        self.http_client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers={
                "Accept": "*/*",
                "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
                "AdaletApplicationName": "UyapMevzuat",
                "Content-Type": "application/json; charset=utf-8",
                "Origin": "https://mevzuat.adalet.gov.tr",
                "Referer": "https://mevzuat.adalet.gov.tr/",
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-site",
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
            },
            timeout=request_timeout
        )
        self._bucket = _TokenBucket(
            capacity=self._DEFAULT_CAPACITY,
            refill_per_s=1.0 / self._DEFAULT_REFILL_S,
        )
        # Süreç içi tam metin önbelleği (09.11.2026): belge kimliği → (zaman, sonuç).
        self._onbellek: "OrderedDict[str, tuple]" = OrderedDict()
        self._onbellek_krkt = 0

    def _onbellekten(self, document_id: str) -> Optional[BedestenDocumentMarkdown]:
        kayit = self._onbellek.get(document_id)
        if kayit is None:
            return None
        zaman, sonuc = kayit
        if time.monotonic() - zaman >= self._ONBELLEK_SURE_S:
            self._onbellek.pop(document_id, None)
            self._onbellek_krkt -= len(sonuc.markdown_content or "")
            return None
        self._onbellek.move_to_end(document_id)
        return sonuc.model_copy()

    def _onbellege_koy(self, document_id: str, sonuc: BedestenDocumentMarkdown) -> None:
        boy = len(sonuc.markdown_content or "")
        if self._ONBELLEK_ADET <= 0 or boy == 0 or boy > self._ONBELLEK_KRKT:
            return
        eski = self._onbellek.pop(document_id, None)
        if eski is not None:
            self._onbellek_krkt -= len(eski[1].markdown_content or "")
        self._onbellek[document_id] = (time.monotonic(), sonuc.model_copy())
        self._onbellek_krkt += boy
        while self._onbellek and (len(self._onbellek) > self._ONBELLEK_ADET or self._onbellek_krkt > self._ONBELLEK_KRKT):
            _, (_, atilan) = self._onbellek.popitem(last=False)
            self._onbellek_krkt -= len(atilan.markdown_content or "")

    @staticmethod
    def _retry_after_saniye(response: httpx.Response) -> Optional[float]:
        """Retry-After başlığını saniyeye çevirir; yoksa ya da okunamazsa None."""
        try:
            return float(response.headers.get("Retry-After", ""))
        except (TypeError, ValueError):
            return None

    async def _istek_gonder(self, endpoint: str, govde: dict, op: str) -> httpx.Response:
        """Kovadan izin alıp POST eder. 429'da (yerel kova ya da Bedesten) bekleme
        _RETRY_MAX_S'i aşmıyorsa bekler ve EN ÇOK BİR KEZ yeniden dener."""
        for deneme in (0, 1):
            try:
                bekleme_siniri = self._DEFAULT_MAX_WAIT_S if deneme == 0 else self._RETRY_MAX_S + 1.5
                await self._bucket.acquire(max_wait=bekleme_siniri)
            except BedestenRateLimited as e:
                if deneme == 0 and e.retry_after <= self._RETRY_MAX_S:
                    logger.info(f"BedestenApiClient: local bucket full on {op}; waiting {e.retry_after:.1f}s and retrying once")
                    await asyncio.sleep(e.retry_after)
                    continue
                raise
            response = await self.http_client.post(endpoint, json=govde)
            if response.status_code != 429:
                return response
            self._handle_429(response, op)
            bekle = self._retry_after_saniye(response)
            if deneme == 0 and bekle is not None and bekle <= self._RETRY_MAX_S:
                logger.info(f"BedestenApiClient: 429 on {op}; Retry-After {bekle:.1f}s ≤ {self._RETRY_MAX_S:.0f}s, retrying once")
                continue
            return response
        return response

    def _handle_429(self, response: httpx.Response, op: str) -> None:
        """Apply back-pressure to the shared bucket based on Retry-After."""
        retry_after_raw = response.headers.get("Retry-After", "")
        try:
            retry_after = float(retry_after_raw)
        except (TypeError, ValueError):
            retry_after = 30.0
        # Cap penalty so a hostile/buggy server can't freeze us indefinitely.
        retry_after = max(1.0, min(retry_after, 60.0))
        self._bucket.penalize_until(time.monotonic() + retry_after + 0.5)
        logger.warning(
            f"BedestenApiClient: 429 on {op}; bucket paused {retry_after + 0.5:.1f}s"
        )
    
    async def search_documents(self, search_request: BedestenSearchRequest) -> BedestenSearchResponse:
        """
        Search for documents using Bedesten API.
        Currently supports: YARGITAYKARARI, DANISTAYKARARI, YERELHUKMAHKARARI, etc.
        """
        logger.info(f"BedestenApiClient: Searching documents with phrase: {search_request.data.phrase}")
        
        # Map abbreviated birimAdi to full Turkish name before sending to API
        original_birim_adi = search_request.data.birimAdi
        mapped_birim_adi = get_full_birim_adi(original_birim_adi)
        search_request.data.birimAdi = mapped_birim_adi
        if original_birim_adi != "ALL":
            logger.info(f"BedestenApiClient: Mapped birimAdi '{original_birim_adi}' to '{mapped_birim_adi}'")
        
        try:
            # Create request dict and remove birimAdi if empty
            request_dict = search_request.model_dump()
            if not request_dict["data"]["birimAdi"]:  # Remove if empty string
                del request_dict["data"]["birimAdi"]
            
            response = await self._istek_gonder(self.SEARCH_ENDPOINT, request_dict, "search")
            response.raise_for_status()
            response_json = response.json()

            # Parse and return the response
            return BedestenSearchResponse(**response_json)

        except httpx.RequestError as e:
            logger.error(f"BedestenApiClient: HTTP request error during search: {e}")
            raise
        except Exception as e:
            logger.error(f"BedestenApiClient: Error processing search response: {e}")
            raise
    
    async def get_document_as_markdown(self, document_id: str) -> BedestenDocumentMarkdown:
        """
        Get document content and convert to markdown.
        Handles both HTML (text/html) and PDF (application/pdf) content types.
        """
        logger.info(f"BedestenApiClient: Fetching document for markdown conversion (ID: {document_id})")
        onbellekte = self._onbellekten(document_id)
        if onbellekte is not None:
            logger.info(f"BedestenApiClient: document {document_id} served from in-process cache")
            return onbellekte
        
        try:
            # Prepare request
            doc_request = BedestenDocumentRequest(
                data=BedestenDocumentRequestData(documentId=document_id)
            )
            
            # Get document
            response = await self._istek_gonder(self.DOCUMENT_ENDPOINT, doc_request.model_dump(), f"document {document_id}")
            response.raise_for_status()
            response_json = response.json()
            doc_response = BedestenDocumentResponse(**response_json)
            
            # Add null safety checks for document data
            if not hasattr(doc_response, 'data') or doc_response.data is None:
                raise ValueError("Document response does not contain data")
            
            if not hasattr(doc_response.data, 'content') or doc_response.data.content is None:
                raise ValueError("Document data does not contain content")
                
            if not hasattr(doc_response.data, 'mimeType') or doc_response.data.mimeType is None:
                raise ValueError("Document data does not contain mimeType")
            
            # Decode base64 content with error handling
            try:
                content_bytes = base64.b64decode(doc_response.data.content)
            except Exception as e:
                raise ValueError(f"Failed to decode base64 content: {str(e)}")
            
            mime_type = doc_response.data.mimeType
            
            logger.info(f"BedestenApiClient: Document mime type: {mime_type}")
            
            # Convert to markdown based on mime type. markitdown is sync and
            # PDF parsing in particular can block the event-loop for seconds,
            # which on a single-worker uvicorn deployment stalls every other
            # in-flight MCP request and new TLS handshakes. Offload to a
            # thread so the event-loop stays responsive.
            if mime_type == "text/html":
                html_content = content_bytes.decode('utf-8')
                markdown_content = await asyncio.to_thread(
                    self._convert_html_to_markdown, html_content
                )
            elif mime_type == "application/pdf":
                markdown_content = await asyncio.to_thread(
                    self._convert_pdf_to_markdown, content_bytes
                )
            else:
                logger.warning(f"Unsupported mime type: {mime_type}")
                markdown_content = f"Unsupported content type: {mime_type}. Unable to convert to markdown."
            
            sonuc = BedestenDocumentMarkdown(
                documentId=document_id,
                markdown_content=markdown_content,
                source_url=f"https://mevzuat.adalet.gov.tr/ictihat/{document_id}",
                mime_type=mime_type
            )
            if mime_type in ("text/html", "application/pdf") and markdown_content:
                self._onbellege_koy(document_id, sonuc)
            return sonuc
            
        except httpx.RequestError as e:
            logger.error(f"BedestenApiClient: HTTP error fetching document {document_id}: {e}")
            raise
        except Exception as e:
            logger.error(f"BedestenApiClient: Error processing document {document_id}: {e}")
            raise
    
    def _convert_html_to_markdown(self, html_content: str) -> Optional[str]:
        """Convert HTML to Markdown using MarkItDown"""
        if not html_content:
            return None
            
        try:
            # Convert HTML string to bytes and create BytesIO stream
            html_bytes = html_content.encode('utf-8')
            html_stream = io.BytesIO(html_bytes)
            
            # Pass BytesIO stream to MarkItDown to avoid temp file creation
            md_converter = MarkItDown()
            result = md_converter.convert(html_stream)
            markdown_content = result.text_content
            
            logger.info("Successfully converted HTML to Markdown")
            return markdown_content
            
        except Exception as e:
            logger.error(f"Error converting HTML to Markdown: {e}")
            return f"Error converting HTML content: {str(e)}"
    
    def _convert_pdf_to_markdown(self, pdf_bytes: bytes) -> Optional[str]:
        """Convert PDF to Markdown using MarkItDown"""
        if not pdf_bytes:
            return None
            
        try:
            # Create BytesIO stream from PDF bytes
            pdf_stream = io.BytesIO(pdf_bytes)
            
            # Pass BytesIO stream to MarkItDown to avoid temp file creation
            md_converter = MarkItDown()
            result = md_converter.convert(pdf_stream)
            markdown_content = result.text_content
            
            logger.info("Successfully converted PDF to Markdown")
            return markdown_content
            
        except Exception as e:
            logger.error(f"Error converting PDF to Markdown: {e}")
            return f"Error converting PDF content: {str(e)}. The document may be corrupted or in an unsupported format."
    
    async def close_client_session(self):
        """Close HTTP client session"""
        await self.http_client.aclose()
        logger.info("BedestenApiClient: HTTP client session closed.")