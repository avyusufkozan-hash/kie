"""KİE tasarruf paketi testleri (kie-tasarruf dalı · 09.11.2026)
Çalıştırma: python tests/test_kie_tasarruf.py   (pytest gerekmez · ağ YOK · Bedesten sahte)

Kilitler:
  0. Var olan 28 aracın adı + girdi şeması + çıktı şeması main (1de4d39) ile birebir · yalnız 1 yeni araç.
  1. Reklam cümlesi kaynakta 0 · 429 cevabı kısa Türkçe cümle (öneki ve alanları aynen).
  2. 429'da bekle + EN ÇOK 1 yeniden deneme (Retry-After / yerel kova ≤ 8 sn).
  3. Boş aramada Türkçe tek cümle + ipucu.
  4. search_bedesten_coklu: sıra · tekilleştirme · boş/yoğun satırı · 1–5 ifade · var olan aramayla aynı ifade işleme.
  5. sade çıktı biçimi.
  6. Tam metin önbelleği: tekrar istekte Bedesten'e gidilmez · hata saklanmaz · sınır · süre.
Her kilidin pozitif kontrolü var.
"""
import asyncio
import base64
import hashlib
import json
import logging
import pathlib
import sys
import time

KOK = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KOK))
logging.disable(logging.CRITICAL)

import httpx  # noqa: E402
import mcp_server_main as m  # noqa: E402
from fastmcp import Client  # noqa: E402
from bedesten_mcp_module.client import BedestenApiClient, BedestenRateLimited, _TokenBucket  # noqa: E402
from bedesten_mcp_module.models import BedestenSearchRequest, BedestenSearchData, BedestenSearchResponse  # noqa: E402

GECEN, KALAN = 0, []


def dogrula(ad, kosul):
    global GECEN
    if kosul:
        GECEN += 1
        print("✓", ad)
    else:
        KALAN.append(ad)
        print("✗", ad)


def karar(i):
    return {"documentId": str(i), "itemType": {"name": "YARGITAYKARARI", "description": "Yargıtay Kararı"}, "birimId": None,
            "birimAdi": "2. Hukuk Dairesi", "esasNoYil": 2025, "esasNoSira": i, "kararNoYil": 2026, "kararNoSira": i,
            "kararTuru": None, "kararTarihi": "2026-06-08T21:00:00.000+00:00", "kararTarihiStr": "09.06.2026",
            "kesinlesmeDurumu": None, "kararNo": f"2026/{i}", "esasNo": f"2025/{i}"}


ARAMA_200 = {"data": {"emsalKararList": [karar(1)], "total": 1, "start": 0}, "metadata": {}}
HTML = "<p><b>2. Hukuk Dairesi 2025/1 E. , 2026/1 K.</b></p><p>Gerekçe metni.</p>"
BELGE_200 = {"data": {"content": base64.b64encode(HTML.encode("utf-8")).decode(), "mimeType": "text/html", "version": 1}, "metadata": {}}


def sahte_istemci(cevaplar, hizli_kova=True):
    """cevaplar: [(kod, başlık, gövde)] sırayla; son cevap tekrarlanır. sayac['n'] = HTTP istek sayısı."""
    sayac = {"n": 0}

    def isle(req):
        i = sayac["n"]
        sayac["n"] += 1
        kod, bas, govde = cevaplar[min(i, len(cevaplar) - 1)]
        return httpx.Response(kod, json=govde, headers=bas)

    c = BedestenApiClient()
    c.http_client = httpx.AsyncClient(base_url=c.BASE_URL, transport=httpx.MockTransport(isle))
    if hizli_kova:
        c._bucket = _TokenBucket(capacity=100, refill_per_s=100)
    return c, sayac


def arama_istegi(ifade="x"):
    return BedestenSearchRequest(data=BedestenSearchData(pageSize=10, pageNumber=1, itemTypeList=["YARGITAYKARARI"],
                                                        phrase=ifade, birimAdi="ALL", kararTarihiStart="", kararTarihiEnd=""))


# ---------------------------------------------------------------- 0 · imza kilidi
async def t0_imza(c):
    imza = {}
    for t in await c.list_tools():
        g = json.dumps({"girdi": t.inputSchema, "cikti": t.outputSchema}, sort_keys=True, ensure_ascii=False)
        imza[t.name] = hashlib.sha256(g.encode("utf-8")).hexdigest()
    kilit = json.loads((KOK / "tests" / "arac-imzalari-main-1de4d39.json").read_text(encoding="utf-8"))
    dogrula("0 · mevcut %d araç adı+girdi+çıktı main ile aynı" % len(kilit), all(imza.get(k) == v for k, v in kilit.items()))
    dogrula("0 · yalnız bir yeni araç (search_bedesten_coklu)", sorted(set(imza) - set(kilit)) == ["search_bedesten_coklu"])
    bozuk = dict(kilit)
    bozuk[sorted(bozuk)[0]] = "0" * 64
    dogrula("0 · POZİTİF · bozuk kilit yakalanır", not all(imza.get(k) == v for k, v in bozuk.items()))


# ---------------------------------------------------------------- 1 · reklam + 429 metni
async def t1_reklam(c):
    kaynak = "".join(p.read_text(encoding="utf-8") for p in KOK.rglob("*.py") if "tests" not in p.parts)
    dogrula("1 · kaynakta reklam bağlantısı 0", "betaspacestudio" not in kaynak and "beta sürümüne" not in kaynak)
    dogrula("1 · POZİTİF · tarayıcı reklam metnini görür", "betaspacestudio" in "x https://yargi.betaspacestudio.com x")
    eski_ara, eski_belge = m.bedesten_client_instance.search_documents, m.bedesten_client_instance.get_document_as_markdown

    async def kova_dolu(*a, **k):
        raise BedestenRateLimited(20.0)

    try:
        m.bedesten_client_instance.search_documents = kova_dolu
        m.bedesten_client_instance.get_document_as_markdown = kova_dolu
        a = (await c.call_tool("search_bedesten_unified", {"phrase": "nafaka"})).structured_content
        dogrula("1 · arama 429 · mesaj kısa Türkçe", a.get("message") == m.KIE_YOGUN_MESAJI)
        dogrula("1 · arama 429 · alanlar aynen", a.get("error") == "rate_limit_exceeded" and a.get("status_code") == 429 and a.get("retry_after") == "20.0")
        b = (await c.call_tool("get_bedesten_document_markdown", {"documentId": "42"})).structured_content
        dogrula("1 · tam metin 429 · önek + Türkçe cümle", b["markdown_content"] == "ERROR (rate_limit_exceeded, HTTP 429): " + m.KIE_YOGUN_MESAJI + " Retry-After: 20.0")
        istemci, _ = sahte_istemci([(429, {"Retry-After": "30"}, {})])
        m.bedesten_client_instance.search_documents = istemci.search_documents
        m.bedesten_client_instance.get_document_as_markdown = istemci.get_document_as_markdown
        a2 = (await c.call_tool("search_bedesten_unified", {"phrase": "nafaka"})).structured_content
        dogrula("1 · arama HTTP 429 · mesaj kısa Türkçe", a2.get("message") == m.KIE_YOGUN_MESAJI and a2.get("retry_after") == "30")
        istemci2, _ = sahte_istemci([(429, {"Retry-After": "30"}, {})])  # ayrı istemci: ilk 429 kovayı 30 sn kilitler
        m.bedesten_client_instance.get_document_as_markdown = istemci2.get_document_as_markdown
        b2 = (await c.call_tool("get_bedesten_document_markdown", {"documentId": "42"})).structured_content
        dogrula("1 · tam metin HTTP 429 · mesaj", b2["markdown_content"] == "ERROR (rate_limit_exceeded, HTTP 429): " + m.KIE_YOGUN_MESAJI + " Retry-After: 30")
    finally:
        m.bedesten_client_instance.search_documents, m.bedesten_client_instance.get_document_as_markdown = eski_ara, eski_belge


# ---------------------------------------------------------------- 2 · bekle + tek yeniden deneme
async def t2_yeniden():
    c, s = sahte_istemci([(429, {"Retry-After": "1"}, {}), (200, {}, ARAMA_200)])
    t = time.monotonic()
    r = await c.search_documents(arama_istegi())
    dogrula("2 · 429 (1 sn) → bekle → başarılı · 2 istek", s["n"] == 2 and len(r.data.emsalKararList) == 1 and time.monotonic() - t >= 1.0)
    c, s = sahte_istemci([(429, {"Retry-After": "30"}, {}), (200, {}, ARAMA_200)])
    try:
        await c.search_documents(arama_istegi())
        dogrula("2 · 429 (30 sn) → yeniden denenmez", False)
    except httpx.HTTPStatusError as e:
        dogrula("2 · 429 (30 sn) → yeniden denenmez · 1 istek", s["n"] == 1 and e.response.status_code == 429)
    c, s = sahte_istemci([(429, {"Retry-After": "1"}, {}), (429, {"Retry-After": "1"}, {}), (200, {}, ARAMA_200)])
    try:
        await c.search_documents(arama_istegi())
        dogrula("2 · iki 429 → en çok bir yeniden deneme", False)
    except httpx.HTTPStatusError:
        dogrula("2 · iki 429 → en çok bir yeniden deneme · 2 istek", s["n"] == 2)
    c, s = sahte_istemci([(429, {}, {}), (200, {}, ARAMA_200)])
    try:
        await c.search_documents(arama_istegi())
        dogrula("2 · Retry-After yok → yeniden denenmez", False)
    except httpx.HTTPStatusError:
        dogrula("2 · Retry-After yok → yeniden denenmez · 1 istek", s["n"] == 1)
    c, s = sahte_istemci([(200, {}, BELGE_200)], hizli_kova=False)
    c._bucket = _TokenBucket(capacity=1, refill_per_s=0.5)
    c._bucket._tokens = 0.0
    c._DEFAULT_MAX_WAIT_S = 0.1
    t = time.monotonic()
    await c.get_document_as_markdown("7")
    dogrula("2 · yerel kova dolu (2 sn) → bekledi → tam metin geldi", s["n"] == 1 and time.monotonic() - t >= 1.5)
    c, s = sahte_istemci([(200, {}, BELGE_200)], hizli_kova=False)
    c._bucket = _TokenBucket(capacity=1, refill_per_s=0.05)
    c._bucket._tokens = 0.0
    c._DEFAULT_MAX_WAIT_S = 0.1
    try:
        await c.get_document_as_markdown("7")
        dogrula("2 · yerel kova 20 sn > 8 sn → beklemez", False)
    except BedestenRateLimited:
        dogrula("2 · yerel kova 20 sn > 8 sn → beklemez · 0 istek", s["n"] == 0)
    # Pozitif kontrol: yeniden deneme sınırı 0 yapılınca 1 sn'lik 429 da yeniden denenmez.
    c, s = sahte_istemci([(429, {"Retry-After": "1"}, {}), (200, {}, ARAMA_200)])
    c._RETRY_MAX_S = 0.0
    try:
        await c.search_documents(arama_istegi())
        dogrula("2 · POZİTİF · sınır 0 → yeniden deneme yok", False)
    except httpx.HTTPStatusError:
        dogrula("2 · POZİTİF · sınır 0 → yeniden deneme yok · 1 istek", s["n"] == 1)


# ---------------------------------------------------------------- 3 · boş arama
async def t3_bos(c):
    eski = m.bedesten_client_instance.search_documents
    plan = {}

    async def sahte(req):
        return BedestenSearchResponse(**plan["cevap"])

    try:
        m.bedesten_client_instance.search_documents = sahte
        plan["cevap"] = {"data": None, "metadata": {}}
        a = (await c.call_tool("search_bedesten_unified", {"phrase": "nafaka"})).structured_content
        dogrula("3 · data:null → Türkçe hata + ipucu", a["error"] == "Karar kaynağı bu arama için sonuç döndürmedi." and a["mesaj"] == m.KIE_BOS_ARAMA_MESAJI and a["decisions"] == [])
        dogrula("3 · ipucu cümlesi 'çok dar olabilir' içerir", "çok dar olabilir" in m.KIE_BOS_ARAMA_MESAJI and "No data" not in json.dumps(a))
        plan["cevap"] = {"data": {"emsalKararList": [], "total": 0, "start": 0}, "metadata": {}}
        b = (await c.call_tool("search_bedesten_unified", {"phrase": "nafaka"})).structured_content
        dogrula("3 · boş liste → mesaj", b.get("mesaj") == m.KIE_BOS_ARAMA_MESAJI and "error" not in b)
        plan["cevap"] = ARAMA_200
        d = (await c.call_tool("search_bedesten_unified", {"phrase": "nafaka"})).structured_content
        dogrula("3 · POZİTİF · dolu sonuçta mesaj YOK", "mesaj" not in d and len(d["decisions"]) == 1)
    finally:
        m.bedesten_client_instance.search_documents = eski


# ---------------------------------------------------------------- 4–5 · çoklu arama + sade
async def t4_coklu(c):
    eski = m.bedesten_client_instance.search_documents
    plan = {"+a": [1, 2, 3], "+b": [3, 4], "+c": [], "+d": "429"}
    gonderilen = []

    async def sahte(req):
        p = req.data.phrase
        gonderilen.append(p)
        v = plan.get(p, [9])
        if v == "429":
            raise BedestenRateLimited(20.0)
        return BedestenSearchResponse(data={"emsalKararList": [karar(i) for i in v], "total": len(v) * 10, "start": 0}, metadata={})

    try:
        m.bedesten_client_instance.search_documents = sahte
        d = (await c.call_tool("search_bedesten_coklu", {"ifadeler": ["+a", "+b", "+c", "+d", "+a"], "sade": False})).structured_content
        dogrula("4 · sırayla arar · tekrar eden ifade bir kez", gonderilen == ["+a", "+b", "+c", "+d"])
        dogrula("4 · kimliğe göre tekilleştirir", d["tekil_karar"] == 4 and [x["documentId"] for x in d["decisions"]] == ["1", "2", "3", "4"])
        dogrula("4 · kararın bulunduğu ifadeler", [x["ifadeler"] for x in d["decisions"]] == [[1], [1], [1, 2], [2]])
        dogrula("4 · boş ifadeler tek satır", d["bos_ifadeler"].startswith("Sonuç vermeyen ifade(ler): 3 —") and "\n" not in d["bos_ifadeler"])
        dogrula("4 · yoğun ifadeler tek satır", d["yogun_ifadeler"].startswith("Karar kaynağı yoğun olduğu için aranamayan ifade(ler): 4 —"))
        dogrula("4 · sade=false tam alanları taşır", set(karar(1)) <= set(d["decisions"][0]))
        gonderilen.clear()
        s = (await c.call_tool("search_bedesten_coklu", {"ifadeler": ["+a", "+b"]})).structured_content
        dogrula("5 · varsayılan sade · satır biçimi", s["satirlar"].split("\n")[0] == "1 · 2. Hukuk Dairesi · E. 2025/1 · K. 2026/1 · 09.06.2026")
        dogrula("5 · sade · 4 satır · tam alan yok", len(s["satirlar"].split("\n")) == 4 and "decisions" not in s)
        dogrula("5 · sade · ifade özeti", s["ifade_ozeti"] == "1) +a → 3 karar · 3 yeni · toplam 30\n2) +b → 2 karar · 1 yeni · toplam 20")
        tam = json.dumps((await c.call_tool("search_bedesten_coklu", {"ifadeler": ["+a", "+b"], "sade": False})).structured_content, ensure_ascii=False)
        dogrula("5 · sade çıktı tam çıktıdan kısa", len(json.dumps(s, ensure_ascii=False)) < len(tam) / 3)
        # Var olan aramayla aynı ifade işleme (operatörsüz ifade otomatik '+')
        gonderilen.clear()
        await c.call_tool("search_bedesten_unified", {"phrase": "eşit kusur \"yoksulluk nafakası\""})
        await c.call_tool("search_bedesten_coklu", {"ifadeler": ["eşit kusur \"yoksulluk nafakası\""]})
        dogrula("4 · ifade işleme var olan aramayla aynı", len(gonderilen) == 2 and gonderilen[0] == gonderilen[1] == "+eşit +kusur +\"yoksulluk nafakası\"")
        for girdi, ad in [({"ifadeler": ["1", "2", "3", "4", "5", "6"]}, "6 ifade reddedilir"), ({"ifadeler": []}, "0 ifade reddedilir"),
                          ({"ifadeler": ["  ", ""]}, "yalnız boş ifade reddedilir")]:
            try:
                await c.call_tool("search_bedesten_coklu", girdi)
                dogrula("4 · " + ad, False)
            except Exception:
                dogrula("4 · " + ad, True)
    finally:
        m.bedesten_client_instance.search_documents = eski


# ---------------------------------------------------------------- 6 · tam metin önbelleği
async def t6_onbellek():
    c, s = sahte_istemci([(200, {}, BELGE_200)])
    a = await c.get_document_as_markdown("1")
    b = await c.get_document_as_markdown("1")
    dogrula("6 · aynı karar ikinci kez → Bedesten'e gidilmez (1 istek)", s["n"] == 1 and a.markdown_content == b.markdown_content and "Gerekçe metni" in b.markdown_content)
    b.markdown_content = "değiştirildi"
    c2 = await c.get_document_as_markdown("1")
    dogrula("6 · dönen nesneyi değiştirmek önbelleği bozmaz", "Gerekçe metni" in c2.markdown_content)
    c, s = sahte_istemci([(429, {"Retry-After": "30"}, {}), (200, {}, BELGE_200)])
    try:
        await c.get_document_as_markdown("1")
    except httpx.HTTPStatusError:
        pass
    c._bucket = _TokenBucket(capacity=100, refill_per_s=100)  # 429 kovayı 30 sn kilitler; sınama hızlı sürsün
    await c.get_document_as_markdown("1")
    await c.get_document_as_markdown("1")
    dogrula("6 · hata saklanmaz · sonra gelen başarı saklanır (2 istek)", s["n"] == 2)
    c, s = sahte_istemci([(200, {}, BELGE_200)])
    c._ONBELLEK_ADET = 2
    for k in ["1", "2", "3", "1"]:
        await c.get_document_as_markdown(k)
    dogrula("6 · adet sınırı · en eski atılır (1 yeniden istenir · 4 istek)", s["n"] == 4 and len(c._onbellek) == 2)
    c, s = sahte_istemci([(200, {}, BELGE_200)])
    c._ONBELLEK_SURE_S = 0.0
    await c.get_document_as_markdown("1")
    time.sleep(0.05)
    await c.get_document_as_markdown("1")
    dogrula("6 · süre dolunca yeniden getirilir (2 istek)", s["n"] == 2)
    c, s = sahte_istemci([(200, {}, BELGE_200)])
    c._ONBELLEK_KRKT = 10
    await c.get_document_as_markdown("1")
    await c.get_document_as_markdown("1")
    dogrula("6 · karakter sınırını aşan belge saklanmaz (2 istek)", s["n"] == 2 and c._onbellek_krkt == 0)
    c, s = sahte_istemci([(200, {}, BELGE_200)])
    c._ONBELLEK_ADET = 0
    await c.get_document_as_markdown("1")
    await c.get_document_as_markdown("1")
    dogrula("6 · POZİTİF · önbellek kapalıyken 2 istek", s["n"] == 2)


async def ana():
    async with Client(m.app) as c:
        await t0_imza(c)
        await t1_reklam(c)
        await t2_yeniden()
        await t3_bos(c)
        await t4_coklu(c)
    await t6_onbellek()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(ana())
    print(f"\n{GECEN} geçti · {len(KALAN)} kaldı")
    if KALAN:
        print("KALAN:", KALAN)
    sys.exit(1 if KALAN else 0)
