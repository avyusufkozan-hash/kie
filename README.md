---
title: Kozan Ictihat Entegresi KIE
emoji: ⚖️
colorFrom: red
colorTo: gray
sdk: docker
app_port: 8000
pinned: false
---

# Kozan İçtihat Entegresi (KİE)

Türk hukuku içtihat ve karar arama MCP sunucusu. Açık kaynak
[yargi-mcp](https://github.com/saidsurucu/yargi-mcp) (MIT lisansı, Said Sürücü)
projesinin Kozan Hukuk için özelleştirilmiş kopyasıdır.

## Kapsanan veri kaynakları

Yargıtay, Danıştay, Anayasa Mahkemesi (norm denetimi + bireysel başvuru),
UYAP Emsal, KİK, Rekabet Kurumu, Sayıştay, KVKK, BDDK, BTK, GİB özelgeleri,
Sigorta Tahkim Komisyonu, Uyuşmazlık Mahkemesi, Bedesten birleşik arama.

## Claude'a bağlama

Ayarlar → Connectors → "Add custom connector":

- **İsim:** Kozan İçtihat Entegresi (KİE)
- **URL:** `https://KULLANICI-ADI-kie.hf.space/mcp/`

## Uç noktalar

- `/mcp/` — MCP (Streamable HTTP)
- `/health` — sağlık kontrolü
- `/status` — durum

Orijinal belgeler için `README_orijinal_yargi-mcp.md` dosyasına bakın.
