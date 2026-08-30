# Single Agent Scraper

Agente profesional de web scraping en Python 3.11+: fetch híbrido (httpx → Playwright Stealth), poda de DOM, extracción estructurada con Groq o Gemini (Instructor + Pydantic), autocorrección ante errores de validación, y descarga opcional de fotos de producto.

## Pilares

| Pilar | Implementación |
|-------|----------------|
| Optimización de contexto | Selectolax + Trafilatura → Markdown podado (nunca HTML crudo al LLM) |
| Salida estructurada | Instructor + schemas Pydantic (`ProductListing`, `GenericPage`) |
| Autocorrección | Loop ≤ 3 reintentos alimentando `ValidationError` al LLM |
| Anti-bot | UA rotativos, proxies, Playwright Stealth, retries Tenacity |
| Fallback híbrido | HTTP estático primero; browser solo si hace falta |
| Portable | Docker / Docker Desktop en cualquier PC |

## Requisitos

- Docker Desktop **o** Python 3.11+
- API key de [Groq](https://console.groq.com) y/o [Gemini](https://aistudio.google.com/apikey)

## Configuración rápida

```bash
cp .env.example .env
# Editá GROQ_API_KEY y/o GEMINI_API_KEY
```

## Uso con Docker (recomendado)

```bash
docker compose build

docker compose run --rm scraper scrape "https://www.mercadolibre.com.ar/..." \
  --goal "productos con foto precio descripcion" \
  --provider groq \
  --download-images
```

El JSON y las imágenes quedan en `./output` del host.

Ayuda del CLI:

```bash
docker compose run --rm scraper --help
docker compose run --rm scraper scrape --help
```

## Uso local (sin Docker)

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/Mac:
# source .venv/bin/activate

pip install -r requirements.txt
playwright install chromium

pip install -e .

python -m scraper_agent.cli scrape "https://..." \
  --goal "productos con foto precio descripcion" \
  -p groq -i
```

Ejemplo programático:

```python
import asyncio
from scraper_agent import scrape, ProductListing

async def main():
    result = await scrape(
        "https://www.ebay.com/sch/i.html?_nkw=iphone",
        goal="productos con foto, precio y descripción",
        response_model=ProductListing,
        download_images=True,
        provider="gemini",
    )
    print(result.to_json())

asyncio.run(main())
```

## Estructura

```
src/scraper_agent/
  agent.py           # Orquestación scrape()
  scraper.py         # Fase 1 híbrida
  html_cleaner.py    # Poda + Markdown
  extractor.py       # Fase 2 LLM + autocorrección
  providers.py       # Groq / Gemini
  models.py          # Schemas Pydantic
  anti_bot.py        # Headers, proxies, heurísticas
  image_downloader.py
  cli.py
  config.py
```

## Informes (HTML + PDF)

Cada scrape de productos genera automáticamente:

- `output/reports/<nombre>/index.html` — catálogo web con fotos, precios y descripción
- `output/reports/<nombre>/informe.pdf` — PDF descargable (botón en la web)

Regenerar desde un JSON existente:

```bash
python -m scraper_agent.cli report output/books.toscrape.com_3060d727.json
```


- `ProductListing` / `ProductItem`: título, precio, moneda, descripción, imágenes (`url` + `local_path` si descargás).
- `GenericPage`: contenido general de cualquier sitio.
- CLI: `--schema products` (default) o `--schema generic`.

## Variables de entorno

Ver [`.env.example`](.env.example). Las más importantes:

- `LLM_PROVIDER=groq|gemini`
- `GROQ_API_KEY` / `GEMINI_API_KEY`
- `GROQ_MODEL=openai/gpt-oss-120b` (default actual en free tier)
- `GEMINI_MODEL=gemini-2.0-flash`
- `PROXY_LIST` (opcional, separados por coma)
- `DOWNLOAD_IMAGES` / `OUTPUT_DIR`
- `MAX_MARKDOWN_CHARS=12000` (importante para el TPM free de Groq)

## Tests

```bash
pip install -r requirements.txt
pip install -e ".[dev]"
pytest -q
```

## Notas legales / anti-bot

Respeta los términos de servicio y `robots.txt` de cada sitio. Ante Cloudflare/CAPTCHA el agente reporta `blocked=true` y sugiere configurar proxies; no resuelve CAPTCHAs automáticamente.
