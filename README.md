# Single Agent Scraper

Agente de web scraping en Python 3.11+ orientado a **catálogos de productos** (foto, precio, descripción).

Combina:

1. **Adapters de catálogo por plataforma** (VTEX, Shopify, WooCommerce, AJAX HTML, JSON-LD, Coto)
2. **Fetch híbrido** (httpx → Playwright Stealth) cuando no hay API
3. **Extracción LLM tipada** con varios proveedores (Instructor + Pydantic), solo si hace falta
4. **UI web** + informes **HTML/PDF** y borrado de resultados

No es un scraper universal al 100%: login, CAPTCHA o WAF fuerte pueden fallar sin proxy. Sí cubre la mayoría de e-commerces modernos vía APIs públicas o HTML estructurado.

---

## Qué hace

| Entrada | Salida |
|---------|--------|
| URL de listado / categoría / promociones | JSON tipado + informe web + PDF |
| Opcional: descargar fotos | Archivos en `output/images/` |

### Flujo interno

```
URL
 ├─ 1. Adapters de catálogo (API / AJAX / JSON-LD)
 │     → si hay productos: ProductListing sin LLM
 ├─ 2. httpx (HTML estático)
 │     → reintenta adapters con hint del HTML
 │     → o extrae JSON-LD Product / ItemList
 ├─ 3. Playwright Stealth (SPA / JS-heavy)
 │     → captura XHR de productos + DOM
 └─ 4. LLM (Groq, Gemini, OpenAI, Anthropic, …)
      → solo si aún no hay productos estructurados
      → informe HTML + PDF en output/reports/
```

---

## Requisitos

- **Docker Desktop** (recomendado) **o** Python 3.11+
- Al menos una API key del proveedor LLM que vayas a usar  
  (en listados con adapter de API el LLM se omite)

Proveedores soportados:

| ID | Servicio | Key en `.env` |
|----|----------|---------------|
| `groq` | [Groq](https://console.groq.com) | `GROQ_API_KEY` |
| `gemini` | [Google Gemini](https://aistudio.google.com/apikey) | `GEMINI_API_KEY` |
| `openai` | [OpenAI](https://platform.openai.com/api-keys) | `OPENAI_API_KEY` |
| `anthropic` | [Anthropic Claude](https://console.anthropic.com) | `ANTHROPIC_API_KEY` |
| `mistral` | [Mistral](https://console.mistral.ai) | `MISTRAL_API_KEY` |
| `deepseek` | [DeepSeek](https://platform.deepseek.com) | `DEEPSEEK_API_KEY` |
| `openrouter` | [OpenRouter](https://openrouter.ai) | `OPENROUTER_API_KEY` |
| `ollama` | [Ollama](https://ollama.com) local | no requiere key real |

---

## Arranque rápido (Docker)

```bash
cp .env.example .env
# Pegá las API keys que uses (GROQ_API_KEY, GEMINI_API_KEY, OPENAI_API_KEY, etc.)

docker compose up -d --build
```

Abrí **[http://localhost:8000](http://localhost:8000)**:

- Pegá la URL → **Scrapear**
- Elegí el proveedor LLM en el selector
- Activá o no “Descargar fotos”
- Abrí el informe web / PDF
- **Borrar** (un informe) o **Borrar informes** (todos + JSON/fotos relacionados)

La carpeta `./output` del host se monta en el contenedor.

CLI vía Docker:

```bash
docker compose run --rm --entrypoint python scraper \
  -m scraper_agent.cli scrape "URL" -p groq -i --no-open
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
        provider="groq",  # o gemini, openai, anthropic, mistral, deepseek, openrouter, ollama
    )
    print(result.fetch_method)  # api | httpx | playwright
    print(len(result.data.items))
    print(result.to_json())

asyncio.run(main())
```

---

## Proveedores LLM

Configuración en `.env` (`LLM_PROVIDER` + `*_API_KEY` + `*_MODEL`).

Defaults de modelo:

| Proveedor | Modelo default |
|-----------|----------------|
| Groq | `openai/gpt-oss-120b` |
| Gemini | `gemini-3.6-flash` |
| OpenAI | `gpt-4.1-mini` |
| Anthropic | `claude-sonnet-4-5` |
| Mistral | `mistral-small-latest` |
| DeepSeek | `deepseek-chat` |
| OpenRouter | `openai/gpt-4.1-mini` |
| Ollama | `llama3.2` (`OLLAMA_BASE_URL=http://127.0.0.1:11434/v1`) |

La extracción tipada usa **Instructor** + validación Pydantic con hasta `MAX_EXTRACTION_RETRIES` autocorrecciones.

> En muchos e-commerces (VTEX / Woo / Shopify / etc.) el LLM **no se llama**: los productos salen directo de la API del sitio.

---

## Adapters de catálogo (sin LLM)

`site_adapters.py` auto-detecta la plataforma (no hace falta un adapter por dominio):

| Plataforma | Mecanismo |
|------------|-----------|
| **VTEX** | `catalog_system` por path, `productClusterIds`, Intelligent Search |
| **Shopify** | `/collections/.../products.json`, `/products.json` |
| **WooCommerce** | Store API `/wp-json/wc/store/v1/products` (`on_sale`, categorías) + fallback HTML |
| **JSON-LD** | `Product` / `ItemList` en el HTML |
| **AJAX HTML** | `ajax-search` + `.shop-card` (p. ej. Wrangler) |
| **Coto** | API de ofertas (caso especial) |

Ejemplos verificados:

- Carrefour AR — VTEX cluster  
- Levi’s AR — VTEX categoría  
- Wrangler AR — ajax-search  
- Ciudad Muebles — WooCommerce promociones  
- Coto — API de ofertas  

Mercado Libre y sitios con WAF fuerte suelen requerir `PROXY_LIST`.

Tope de ítems: `MAX_PRODUCTS` (default `40`, rango 1–500), con paginación en VTEX / Woo / AJAX.

---

## Informes

```
output/
  <host>_<hash>.json
  reports/<host>_<hash>/
    index.html
    informe.pdf
  images/<host-slug>/...          # si DOWNLOAD_IMAGES / -i
```

Regenerar desde JSON:

```bash
python -m scraper_agent.cli report output/www.levi.com.ar_36970f1b.json
```

---

## CLI

```bash
# Scrape
python -m scraper_agent.cli scrape "URL" \
  [-g GOAL] \
  [-p groq|gemini|openai|anthropic|mistral|deepseek|openrouter|ollama] \
  [-i] \
  [-s products|generic] \
  [--no-open]

# Regenerar HTML/PDF
python -m scraper_agent.cli report path/al/archivo.json [--no-pdf]
```

También: `scraper-agent scrape "URL"` si el package está instalado.

---

## Estructura del proyecto

```
src/scraper_agent/
  agent.py            # Orquestación scrape()
  scraper.py          # Fetch híbrido + merge API/XHR/JSON-LD
  site_adapters.py    # VTEX / Shopify / Woo / AJAX / Coto
  product_json.py     # Normalización de payloads + JSON-LD
  html_cleaner.py     # Poda DOM + Markdown
  extractor.py        # LLM tipado + autocorrección
  providers.py        # Factory Instructor multi-proveedor
  models.py           # Schemas Pydantic
  reporter.py         # HTML + PDF
  webapp.py           # FastAPI UI + REST
  templates/          # Interfaz web
  image_downloader.py
  anti_bot.py
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

- **`ProductListing` / `ProductItem`**: título, precio, moneda, descripción, imágenes (`url`, `local_path`), URL
- **`GenericPage`**: contenido general (`--schema generic`)
- **`ScrapedResult`**: `data` + `fetch_method` (`api` \| `httpx` \| `playwright`) + `warnings` + `blocked`

---

## Variables de entorno

Ver [`.env.example`](.env.example).

| Variable | Default | Descripción |
|----------|---------|-------------|
| `LLM_PROVIDER` | `groq` | Proveedor activo |
| `GROQ_API_KEY` / `GROQ_MODEL` | `openai/gpt-oss-120b` | Groq |
| `GEMINI_API_KEY` / `GEMINI_MODEL` | `gemini-3.6-flash` | Gemini |
| `OPENAI_API_KEY` / `OPENAI_MODEL` | `gpt-4.1-mini` | OpenAI |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` | `claude-sonnet-4-5` | Claude |
| `MISTRAL_API_KEY` / `MISTRAL_MODEL` | `mistral-small-latest` | Mistral |
| `DEEPSEEK_API_KEY` / `DEEPSEEK_MODEL` | `deepseek-chat` | DeepSeek |
| `OPENROUTER_API_KEY` / `OPENROUTER_MODEL` | `openai/gpt-4.1-mini` | OpenRouter |
| `OLLAMA_API_KEY` / `OLLAMA_MODEL` / `OLLAMA_BASE_URL` | `llama3.2` | Ollama local |
| `MAX_PRODUCTS` | `40` | Tope de productos (1–500) |
| `MAX_MARKDOWN_CHARS` | `12000` | Límite de contexto al LLM |
| `HTTP_TIMEOUT` | `30` | Timeout httpx (s) |
| `PLAYWRIGHT_TIMEOUT` | `45000` | Timeout browser (ms) |
| `MAX_FETCH_RETRIES` | `3` | Reintentos de fetch |
| `MAX_EXTRACTION_RETRIES` | `3` | Reintentos LLM / validación |
| `PROXY_LIST` | vacío | Proxies separados por coma |
| `DOWNLOAD_IMAGES` | `false` | Descargar fotos |
| `OUTPUT_DIR` | `./output` | Carpeta de salida |
| `IMAGE_DOWNLOAD_CONCURRENCY` | `5` | Descargas en paralelo |
| `LOG_LEVEL` | `INFO` | Logging |

Tras editar `.env` en Docker: `docker compose up -d` (o `--build` si cambió código).

---

## API HTTP

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/` | Interfaz web |
| `POST` | `/api/scrape` | `{ "url", "goal?", "provider?", "download_images?" }` |
| `GET` | `/api/reports` | Lista informes recientes |
| `DELETE` | `/api/reports/{name}` | Borra un informe + JSON + fotos |
| `DELETE` | `/api/reports` | Borra todos los informes relacionados |
| `GET` | `/files/...` | Estáticos de `output/` |

---

## Tests

```bash
pip install -r requirements.txt
pip install -e ".[dev]"
pytest -q
```

---

## Limitaciones

- No resuelve CAPTCHA / Cloudflare solo; usá `PROXY_LIST` si hace falta.
- SPAs sin API pública dependen de Playwright + LLM.
- `MAX_PRODUCTS` limita cuántos ítems se piden/paginan.
- Respetá términos de uso y `robots.txt` de cada sitio.

---

## Stack

Python 3.11 · httpx · Playwright · Selectolax · Trafilatura · Instructor · Pydantic · FastAPI · Docker  

LLMs: Groq · Gemini · OpenAI · Anthropic · Mistral · DeepSeek · OpenRouter · Ollama
