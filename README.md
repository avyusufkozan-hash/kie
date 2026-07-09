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

Kozan Hukuk için geliştirilmiş Türk hukuku içtihat ve karar arama MCP sunucusu.

## Kapsanan veri kaynakları

Yargıtay, Danıştay, Anayasa Mahkemesi (norm denetimi + bireysel başvuru),
UYAP Emsal, KİK, Rekabet Kurumu, Sayıştay, KVKK, BDDK, BTK, GİB özelgeleri,
Sigorta Tahkim Komisyonu, Uyuşmazlık Mahkemesi, Bedesten birleşik arama.

## Claude'a bağlama

Ayarlar → Connectors → "Add custom connector":

- **İsim:** Kozan İçtihat Entegresi (KİE)
- **URL:** `https://kozan-ictihat-entegresi-kie.onrender.com/mcp/`

## Uç noktalar

- `/mcp/` — MCP (Streamable HTTP)
- `/health` — sağlık kontrolü
- `/status` — durum
