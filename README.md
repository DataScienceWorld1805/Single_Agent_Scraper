# Single Agent Scraper

Agente de web scraping en Python 3.11+ orientado a **catálogos de productos** (foto, precio, descripción). Combina:

1. **Adapters de catálogo por plataforma** (VTEX, Shopify, WooCommerce, APIs propias, JSON-LD, AJAX HTML)
2. **Fetch híbrido** (httpx → Playwright Stealth) cuando no hay API
3. **Extracción LLM tipada** (Groq / Gemini + Instructor + Pydantic) solo si hace falta
4. **Informes HTML + PDF** y una **UI web** para pegar URL y scrapear

No es un scraper “mágico” universal: sitios con login, CAPTCHA o WAF fuerte pueden fallar sin proxy. Sí cubre la mayoría de e-commerces modernos vía APIs públicas o HTML estructurado.

---

## Qué hace

| Entrada | Salida |
|---------|--------|
| URL de listado / categoría / promociones | JSON tipado + informe web + PDF |
| Opcional: descargar fotos | Archivos en `output/images/` |

**Flujo interno (en orden):**

```
URL
 ├─ 1. Adapters de catálogo (API / AJAX / JSON-LD)
 │     → si hay productos: arma ProductListing sin LLM
 ├─ 2. httpx (HTML estático)
 │     → detecta plataforma en el HTML y reintenta adapters
 │     → o extrae JSON-LD Product/ItemList
 ├─ 3. Playwright Stealth (SPA / JS-heavy)
 │     → captura XHR de productos + DOM
 └─ 4. LLM (Groq/Gemini) solo si aún no hay productos estructurados
      → informe HTML + PDF en output/reports/
```

---

## Requisitos

- **Docker Desktop** (recomendado) **o** Python 3.11+
- API key de [Groq](https://console.groq.com) y/o [Gemini](https://aistudio.google.com/apikey)  
  (para listados con adapter de API el LLM se omite; igual conviene tener key para sitios sin API)

---

## Arranque rápido (Docker)

```bash
cp .env.example .env
# Editá GROQ_API_KEY y/o GEMINI_API_KEY

docker compose up -d --build
```

Abrí **[http://localhost:8000](http://localhost:8000)**:

- Pegá la URL → **Scrapear**
- Elegí proveedor LLM (Groq / Gemini)
- Activá o no “Descargar fotos”
- Revisá el informe web / PDF
- **Borrar** un informe o **Borrar informes** para limpiar HTML, PDF, JSON y fotos asociadas

Salida montada en `./output` del host.

CLI vía Docker:

```bash
docker compose run --rm --entrypoint python scraper -m scraper_agent.cli scrape "URL" -i --no-open
```

---

## Uso local (sin Docker)

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
# source .venv/bin/activate

pip install -r requirements.txt
playwright install chromium
pip install -e .

python -m scraper_agent.cli scrape "https://ejemplo.com/categoria" \
  --goal "productos con foto precio descripcion" \
  -p groq -i
```

UI local:

```bash
uvicorn scraper_agent.webapp:app --reload --port 8000
```

### API Python

```python
import asyncio
from scraper_agent import scrape, ProductListing

async def main():
    result = await scrape(
        "https://ciudad-muebles.com.ar/promociones/",
        goal="productos con foto, precio y descripción",
        response_model=ProductListing,
        download_images=True,
        provider="groq",
    )
    print(result.fetch_method)   # api | httpx | playwright
    print(len(result.data.items))
    print(result.to_json())

asyncio.run(main())
```

---

## Adapters de catálogo (sin LLM)

El módulo `site_adapters.py` auto-detecta la plataforma (no hace falta un adapter por dominio):

| Plataforma | Cómo |
|------------|------|
| **VTEX** | `catalog_system` por path, `productClusterIds`, Intelligent Search |
| **Shopify** | `/collections/.../products.json`, `/products.json` |
| **WooCommerce** | Store API `/wp-json/wc/store/v1/products` (`on_sale`, categorías) + fallback HTML |
| **JSON-LD** | `Product` / `ItemList` embebido en el HTML |
| **AJAX HTML** | Formularios `ajax-search` + cards `.shop-card` (p. ej. Wrangler) |
| **Coto** | API de ofertas (caso especial con key pública del sitio) |

Ejemplos que ya funcionan con este enfoque:

- Carrefour AR (VTEX cluster)
- Levi’s AR (VTEX categoría)
- Wrangler AR (ajax-search)
- Ciudad Muebles (WooCommerce promociones)
- Coto ofertas (API propia)

Sitios como Mercado Libre suelen bloquear sin proxy (`account-verification` / CAPTCHA).

---

## Informes

Cada scrape de productos genera:

```
output/
  <host>_<hash>.json              # resultado tipado
  reports/<host>_<hash>/
    index.html                    # catálogo web
    informe.pdf                   # PDF
  images/<host-slug>/...          # fotos si DOWNLOAD_IMAGES / -i
```

Regenerar informe desde un JSON:

```bash
python -m scraper_agent.cli report output/www.levi.com.ar_36970f1b.json
```

En la UI: botones **Borrar** (uno) y **Borrar informes** (todos + archivos relacionados).

---

## CLI

```bash
# Scrape
python -m scraper_agent.cli scrape "URL" [-g GOAL] [-p groq|gemini] [-i] [-s products|generic] [--no-open]

# Regenerar HTML/PDF desde JSON
python -m scraper_agent.cli report path/al/archivo.json [--no-pdf]
```

También: `scraper-agent scrape "URL"` si el package está instalado.

---

## Estructura del proyecto

```
src/scraper_agent/
  agent.py            # Orquestación scrape()
  scraper.py          # Fetch híbrido + merge de productos API/XHR/JSON-LD
  site_adapters.py    # Detección genérica VTEX / Shopify / Woo / AJAX / Coto
  product_json.py     # Normalización de payloads + JSON-LD
  html_cleaner.py     # Poda DOM (Selectolax) + Markdown (Trafilatura)
  extractor.py        # LLM tipado + autocorrección ≤ 3
  providers.py        # Clientes Groq / Gemini
  models.py           # ProductListing, ProductItem, GenericPage, ScrapedResult
  reporter.py         # Informe HTML + PDF (Playwright)
  webapp.py           # FastAPI UI + API REST
  templates/          # Interfaz web
  image_downloader.py
  anti_bot.py         # UA, proxies, heurísticas de bloqueo
  cli.py
  config.py           # Settings vía .env
examples/
tests/
Dockerfile
docker-compose.yml
.env.example
```

---

## Schemas

- **`ProductListing` / `ProductItem`**: título, precio, moneda, descripción, imágenes (`url`, `local_path`), URL del producto
- **`GenericPage`**: contenido general (`--schema generic`)
- **`ScrapedResult`**: `data` + `fetch_method` (`api` | `httpx` | `playwright`) + `warnings` + `blocked`

---

## Variables de entorno

Ver [`.env.example`](.env.example).

| Variable | Default | Descripción |
|----------|---------|-------------|
| `LLM_PROVIDER` | `groq` | `groq` \| `gemini` |
| `GROQ_API_KEY` | — | Key Groq |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | Modelo Groq |
| `GEMINI_API_KEY` | — | Key Gemini |
| `GEMINI_MODEL` | `gemini-3.6-flash` | Modelo Gemini |
| `MAX_PRODUCTS` | `40` | Tope de ítems por scrape (1–500) |
| `MAX_MARKDOWN_CHARS` | `12000` | Límite de contexto al LLM (TPM free Groq) |
| `HTTP_TIMEOUT` | `30` | Timeout httpx (s) |
| `PLAYWRIGHT_TIMEOUT` | `45000` | Timeout browser (ms) |
| `MAX_FETCH_RETRIES` | `3` | Reintentos de fetch |
| `MAX_EXTRACTION_RETRIES` | `3` | Reintentos LLM / validación |
| `PROXY_LIST` | vacío | Proxies separados por coma |
| `DOWNLOAD_IMAGES` | `false` | Descargar fotos a disco |
| `OUTPUT_DIR` | `./output` | Carpeta de salida |
| `IMAGE_DOWNLOAD_CONCURRENCY` | `5` | Descargas paralelas |
| `LOG_LEVEL` | `INFO` | Logging |

Tras cambiar `.env` con Docker: `docker compose up -d` (o `--build` si también cambió código).

---

## API HTTP (UI)

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/` | Interfaz web |
| `POST` | `/api/scrape` | Body: `{ "url", "goal?", "provider?", "download_images?" }` |
| `GET` | `/api/reports` | Lista informes recientes |
| `DELETE` | `/api/reports/{name}` | Borra un informe + JSON + fotos |
| `DELETE` | `/api/reports` | Borra todos los informes y archivos relacionados |
| `GET` | `/files/...` | Archivos estáticos de `output/` |

---

## Tests

```bash
pip install -r requirements.txt
pip install -e ".[dev]"
pytest -q
```

---

## Limitaciones

- **No resuelve CAPTCHA / Cloudflare** automáticamente; usá `PROXY_LIST` si el sitio bloquea.
- **SPAs sin API pública** dependen de Playwright + LLM y pueden devolver pocos ítems.
- El tope de productos es `MAX_PRODUCTS` (paginación VTEX/Woo/AJAX hasta ese límite).
- Respetá términos de uso y `robots.txt` de cada sitio.

---

## Stack

Python 3.11 · httpx · Playwright · Selectolax · Trafilatura · Instructor · Pydantic · Groq / Gemini · FastAPI · Docker
