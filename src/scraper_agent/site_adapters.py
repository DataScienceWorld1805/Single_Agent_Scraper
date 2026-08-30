"""Adaptadores de catálogo: sitios especiales + detector genérico por plataforma."""

from __future__ import annotations

import html as html_lib
import re
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import httpx
from selectolax.parser import HTMLParser

from scraper_agent.anti_bot import build_headers
from scraper_agent.config import Settings, get_settings
from scraper_agent.logging_setup import get_logger
from scraper_agent.product_json import extract_products_from_payload

log = get_logger(__name__)

COTO_OFFERS_RE = re.compile(
    r"https?://(?:www\.)?coto\.com\.ar/productos/ofertas/([^/?#]+)",
    re.IGNORECASE,
)
COTO_API_KEY = "key_r6xzz4IAoTWcipni"

# VTEX search permite como máximo 50 ítems por request (_from/_to inclusivos).
VTEX_PAGE_SIZE = 50

_SKIP_PATH_PREFIXES = (
    "/account",
    "/login",
    "/cart",
    "/checkout",
    "/wishlist",
    "/api/",
    "/_v/",
    "/files/",
)


async def try_fetch_catalog_products(
    url: str,
    *,
    limit: int | None = None,
    settings: Settings | None = None,
    html_hint: str | None = None,
) -> list[dict[str, Any]]:
    """
    Intenta obtener productos sin scrapear el DOM.

    Orden:
      1) Adapters especiales (Coto API key, etc.)
      2) Señales en la URL (cluster VTEX, colección Shopify)
      3) Detector genérico VTEX/Shopify/WooCommerce
      4) Catálogo HTML AJAX (ajax-search + shop-card, Wrangler y similares)
    """
    cfg = settings or get_settings()
    max_products = limit if limit is not None else cfg.max_products
    cleaned = url.strip()

    match = COTO_OFFERS_RE.match(cleaned)
    if match:
        return await _fetch_coto_offer(match.group(1), page_url=cleaned, limit=max_products)

    cluster = _vtex_cluster_id(cleaned)
    if cluster:
        origin = _origin(cleaned)
        products = await _fetch_vtex_cluster(
            origin, cluster, page_url=cleaned, limit=max_products
        )
        if products:
            return products

    shopify = await _try_shopify(cleaned, limit=max_products, html_hint=html_hint)
    if shopify:
        return shopify

    woo = await _try_woocommerce(cleaned, limit=max_products, html_hint=html_hint)
    if woo:
        return woo

    if _should_try_vtex(cleaned, html_hint):
        products = await _try_vtex_category(cleaned, limit=max_products)
        if products:
            return products

    ajax_products = await _try_html_ajax_catalog(
        cleaned, limit=max_products, html_hint=html_hint
    )
    if ajax_products:
        return ajax_products

    return []


def _origin(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme or 'https'}://{parsed.netloc}"


def _vtex_cluster_id(url: str) -> str | None:
    """productClusterIds en query/path — válido en cualquier tienda VTEX."""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    map_vals = qs.get("map", [])
    path = parsed.path.rstrip("/")

    if map_vals and "productClusterIds" in map_vals[0]:
        m = re.fullmatch(r"/(\d+)", path)
        if m:
            return m.group(1)
        for key in ("productClusterIds", "fq"):
            if key in qs and qs[key]:
                found = re.search(r"(\d{3,})", qs[key][0])
                if found:
                    return found.group(1)

    if "productClusterIds" in url:
        m = re.fullmatch(r"/(\d+)", path)
        if m:
            return m.group(1)
        for key in ("productClusterIds", "fq"):
            if key in qs and qs[key]:
                found = re.search(r"(\d{3,})", qs[key][0])
                if found:
                    return found.group(1)
    return None


def _looks_like_plp_path(path: str) -> bool:
    if not path or path == "/":
        return False
    low = path.lower()
    if low.endswith("/p") or re.search(r"/p$", low):
        return False
    if any(low.startswith(p) for p in _SKIP_PATH_PREFIXES):
        return False
    # un solo segmento numérico sin map=cluster ya se maneja aparte
    if re.fullmatch(r"/\d+", path):
        return False
    segments = [s for s in path.split("/") if s]
    return len(segments) >= 1


def _should_try_vtex(url: str, html_hint: str | None) -> bool:
    if html_hint:
        low = html_hint.lower()
        return any(
            token in low
            for token in (
                "vtex",
                "vtexassets",
                "vteximg",
                "vtexcommercestable",
                "__runtime__",
                "io.vtex.com.br",
            )
        )
    # Sin HTML: prueba especulativa en paths tipo listado
    path = urlparse(url).path.rstrip("/")
    return _looks_like_plp_path(path)


def _parse_ars_price(raw: str | None) -> float | None:
    if not raw:
        return None
    cleaned = raw.replace("$", "").replace(" ", "").strip()
    if not cleaned:
        return None
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "." in cleaned:
        parts = cleaned.split(".")
        if len(parts[-1]) == 3 and all(p.isdigit() for p in parts):
            cleaned = "".join(parts)
    try:
        return float(cleaned)
    except ValueError:
        return None


def _extract_ajax_search_config(html: str, page_url: str) -> dict[str, str] | None:
    """Detecta formularios tipo filter-form → /ajax-search?category=ID (Wrangler et al.)."""
    if not html:
        return None
    tree = HTMLParser(html)
    forms = tree.css("form")
    origin = _origin(page_url)
    for form in forms:
        action = (form.attributes.get("action") or "").strip()
        if not action:
            continue
        action_low = action.lower()
        if "ajax-search" not in action_low and "filter-form" not in (form.attributes.get("class") or ""):
            # permitir filter-form aunque action sea relativa rara
            classes = form.attributes.get("class") or ""
            if "filter-form" not in classes:
                continue
        category = None
        for inp in form.css('input[name="category"]'):
            category = (inp.attributes.get("value") or "").strip()
            if category:
                break
        if not category:
            continue
        if action.startswith("http"):
            endpoint = action
        else:
            endpoint = urljoin(origin + "/", action.lstrip("/"))
        if "ajax-search" not in endpoint.lower():
            endpoint = urljoin(origin + "/", "ajax-search")
        return {"endpoint": endpoint, "category": category, "origin": origin}
    # Fallback: category hidden + page-catalog
    if 'id="page-catalog"' in html or "page-catalog" in html:
        m = re.search(
            r'<input[^>]+name=["\']category["\'][^>]+value=["\'](\d+)["\']',
            html,
            flags=re.I,
        )
        if not m:
            m = re.search(
                r'<input[^>]+value=["\'](\d+)["\'][^>]+name=["\']category["\']',
                html,
                flags=re.I,
            )
        if m:
            return {
                "endpoint": urljoin(origin + "/", "ajax-search"),
                "category": m.group(1),
                "origin": origin,
            }
    return None


def _parse_shop_cards(html: str, *, base_url: str, limit: int) -> list[dict[str, Any]]:
    tree = HTMLParser(html)
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for card in tree.css(".shop-card"):
        if len(found) >= limit:
            break
        title = (card.attributes.get("data-name") or "").strip()
        if not title:
            title_el = card.css_first(".shop-card-title, .product-title, h2, h3")
            title = title_el.text(strip=True) if title_el else ""
        if not title:
            continue
        price = _parse_ars_price(card.attributes.get("data-price"))
        link_el = card.css_first("a.product-link") or card.css_first("a[href]")
        href = link_el.attributes.get("href") if link_el else None
        prod_url = urljoin(base_url, href) if isinstance(href, str) and href else None
        img = None
        img_el = card.css_first("img[src], img[data-src], source[srcset]")
        if img_el:
            img = (
                img_el.attributes.get("src")
                or img_el.attributes.get("data-src")
                or img_el.attributes.get("srcset")
            )
            if isinstance(img, str) and "," in img:
                img = img.split(",")[0].strip().split(" ")[0]
            if isinstance(img, str) and not img.startswith("http"):
                img = urljoin(base_url, img)
        key = f"{title}|{prod_url or ''}|{img or ''}"
        if key in seen:
            continue
        seen.add(key)
        found.append(
            {
                "title": title,
                "price": price,
                "currency": "ARS",
                "description": title,
                "image": img if isinstance(img, str) and img.startswith("http") else None,
                "url": prod_url,
                "brand": None,
            }
        )
    return found


async def _try_html_ajax_catalog(
    url: str, *, limit: int, html_hint: str | None
) -> list[dict[str, Any]]:
    html = html_hint
    if not html:
        # Sin hint: bajar la página una vez si parece listado
        path = urlparse(url).path.rstrip("/")
        if not _looks_like_plp_path(path):
            return []
        headers = build_headers()
        try:
            async with httpx.AsyncClient(timeout=30.0, headers=headers, follow_redirects=True) as client:
                response = await client.get(url)
                if response.status_code != 200:
                    return []
                html = response.text
        except Exception as exc:  # noqa: BLE001
            log.debug("ajax_catalog_page_failed", error=str(exc), url=url)
            return []

    config = _extract_ajax_search_config(html, url)
    if not config:
        # A veces los cards ya vienen en el HTML
        return _parse_shop_cards(html, base_url=_origin(url) + "/", limit=limit)
    headers = build_headers()
    headers.update(
        {
            "Accept": "text/html, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": url,
            "Origin": config["origin"],
        }
    )
    all_products: list[dict[str, Any]] = []
    seen: set[str] = set()
    page = 1
    base_url = config["origin"] + "/"
    try:
        async with httpx.AsyncClient(timeout=40.0, headers=headers, follow_redirects=True) as client:
            while len(all_products) < limit and page <= 30:
                response = await client.get(
                    config["endpoint"],
                    params={
                        "category": config["category"],
                        "q": "",
                        "page": str(page),
                    },
                )
                if response.status_code != 200:
                    break
                batch = _parse_shop_cards(
                    response.text,
                    base_url=base_url,
                    limit=limit - len(all_products),
                )
                if not batch:
                    break
                new_count = 0
                for prod in batch:
                    key = f"{prod.get('title')}|{prod.get('url')}|{prod.get('image')}"
                    if key in seen:
                        continue
                    seen.add(key)
                    all_products.append(prod)
                    new_count += 1
                    if len(all_products) >= limit:
                        break
                if new_count == 0:
                    break
                page += 1
    except Exception as exc:  # noqa: BLE001
        log.warning("ajax_catalog_failed", error=str(exc), host=urlparse(url).netloc)
        return all_products[:limit]

    if all_products:
        log.info(
            "ajax_catalog_products",
            count=len(all_products),
            limit=limit,
            category=config["category"],
            host=urlparse(url).netloc,
        )
    return all_products[:limit]


def _should_try_shopify(url: str, html_hint: str | None) -> bool:
    if html_hint:
        low = html_hint.lower()
        if any(
            token in low
            for token in (
                "cdn.shopify.com",
                "shopify.theme",
                "shopify-section",
                "myshopify.com",
                "shopify.routes",
            )
        ):
            return True
    path = urlparse(url).path.lower()
    return "/collections/" in path or path.rstrip("/").endswith("/products")


async def _try_vtex_category(url: str, *, limit: int) -> list[dict[str, Any]]:
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    if not _looks_like_plp_path(path):
        return []
    origin = _origin(url)
    products = await _fetch_vtex_category_path(
        origin, path, page_url=url, limit=limit
    )
    if products:
        return products

    # Intelligent Search: /category-1/a/category-2/b/...
    segments = [s for s in path.split("/") if s]
    if not segments:
        return []
    parts: list[str] = []
    for idx, seg in enumerate(segments, start=1):
        parts.append(f"category-{idx}/{seg}")
    is_path = "/".join(parts)
    return await _fetch_vtex_intelligent_search(
        origin, is_path, page_url=url, limit=limit
    )


async def _try_shopify(
    url: str, *, limit: int, html_hint: str | None
) -> list[dict[str, Any]]:
    if not _should_try_shopify(url, html_hint):
        # Sin hint HTML solo intentamos si el path parece colección Shopify
        path = urlparse(url).path.lower()
        if "/collections/" not in path:
            return []

    origin = _origin(url)
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    headers = build_headers()
    headers.update({"Accept": "application/json", "Referer": url})

    candidates: list[str] = []
    coll = re.search(r"/collections/([^/]+)", path, re.I)
    if coll:
        handle = coll.group(1)
        candidates.append(f"{origin}/collections/{handle}/products.json")
    candidates.append(f"{origin}/products.json")

    try:
        async with httpx.AsyncClient(timeout=30.0, headers=headers, follow_redirects=True) as client:
            for api in candidates:
                try:
                    response = await client.get(api, params={"limit": str(min(limit, 250))})
                    if response.status_code != 200:
                        continue
                    ctype = (response.headers.get("content-type") or "").lower()
                    if "json" not in ctype:
                        continue
                    payload = response.json()
                except Exception:  # noqa: BLE001
                    continue
                products = _normalize_shopify_payload(payload, base_url=origin + "/", limit=limit)
                if products:
                    log.info("shopify_products", api=api, count=len(products), limit=limit)
                    return products
    except Exception as exc:  # noqa: BLE001
        log.warning("shopify_probe_failed", error=str(exc), host=urlparse(url).netloc)
    return []


def _normalize_shopify_payload(
    payload: Any, *, base_url: str, limit: int
) -> list[dict[str, Any]]:
    raw_products: list[Any] = []
    if isinstance(payload, dict) and isinstance(payload.get("products"), list):
        raw_products = payload["products"]
    elif isinstance(payload, list):
        raw_products = payload

    found: list[dict[str, Any]] = []
    for item in raw_products:
        if len(found) >= limit:
            break
        if not isinstance(item, dict):
            continue
        title = item.get("title")
        if not isinstance(title, str) or not title.strip():
            continue
        image = None
        images = item.get("images")
        if isinstance(images, list) and images:
            first = images[0]
            if isinstance(first, dict) and isinstance(first.get("src"), str):
                image = first["src"]
            elif isinstance(first, str):
                image = first
        if not image and isinstance(item.get("image"), dict):
            src = item["image"].get("src")
            if isinstance(src, str):
                image = src
        price: float | None = None
        variants = item.get("variants")
        if isinstance(variants, list) and variants and isinstance(variants[0], dict):
            raw_price = variants[0].get("price")
            try:
                if raw_price is not None:
                    price = float(raw_price)
            except (TypeError, ValueError):
                price = None
        handle = item.get("handle")
        prod_url = None
        if isinstance(handle, str) and handle:
            prod_url = f"{base_url.rstrip('/')}/products/{handle}"
        found.append(
            {
                "title": title.strip(),
                "price": price,
                "currency": "ARS",
                "description": (
                    str(item["body_html"])[:500]
                    if isinstance(item.get("body_html"), str)
                    else title.strip()
                ),
                "image": image if isinstance(image, str) else None,
                "url": prod_url,
                "brand": item.get("vendor") if isinstance(item.get("vendor"), str) else None,
            }
        )
    return found


def _should_try_woocommerce(url: str, html_hint: str | None) -> bool:
    if html_hint:
        low = html_hint.lower()
        return any(
            token in low
            for token in (
                "woocommerce",
                "wp-content/plugins/woocommerce",
                "wc-block",
                "wc-ajax",
                "wp-json/wc/",
            )
        )
    path = urlparse(url).path.lower()
    return any(
        token in path
        for token in (
            "/promociones",
            "/product-category/",
            "/productos/",
            "/tienda",
            "/shop",
            "/ofertas",
            "/categoria-producto/",
        )
    )


def _wc_price_from_store_item(item: dict[str, Any]) -> tuple[float | None, str | None]:
    prices = item.get("prices")
    if not isinstance(prices, dict):
        return None, None
    raw = prices.get("sale_price") or prices.get("price") or prices.get("regular_price")
    currency = prices.get("currency_code") if isinstance(prices.get("currency_code"), str) else "ARS"
    minor = prices.get("currency_minor_unit")
    try:
        value = float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None, currency
    if value is None:
        return None, currency
    if isinstance(minor, int) and minor > 0:
        value = value / (10**minor)
    return value, currency


def _normalize_woocommerce_store_payload(
    payload: Any, *, limit: int
) -> list[dict[str, Any]]:
    items = payload if isinstance(payload, list) else []
    if isinstance(payload, dict) and isinstance(payload.get("products"), list):
        items = payload["products"]

    found: list[dict[str, Any]] = []
    for item in items:
        if len(found) >= limit:
            break
        if not isinstance(item, dict):
            continue
        title = item.get("name") or item.get("title")
        if isinstance(title, dict):
            title = title.get("rendered")
        if not isinstance(title, str) or not title.strip():
            continue
        title = html_lib.unescape(title.strip())
        price, currency = _wc_price_from_store_item(item)
        image = None
        images = item.get("images")
        if isinstance(images, list) and images:
            first = images[0]
            if isinstance(first, dict):
                image = first.get("src") or first.get("thumbnail")
            elif isinstance(first, str):
                image = first
        permalink = item.get("permalink") or item.get("link")
        description = item.get("short_description") or item.get("description") or title
        if isinstance(description, str):
            description = re.sub(r"<[^>]+>", " ", html_lib.unescape(description))
            description = re.sub(r"\s+", " ", description).strip()[:500] or title
        else:
            description = title
        found.append(
            {
                "title": title,
                "price": price,
                "currency": currency or "ARS",
                "description": description,
                "image": image if isinstance(image, str) and image.startswith("http") else None,
                "url": permalink if isinstance(permalink, str) else None,
                "brand": None,
            }
        )
    return found


async def _woocommerce_category_id(
    client: httpx.AsyncClient, origin: str, slug: str
) -> int | None:
    try:
        response = await client.get(
            f"{origin}/wp-json/wc/store/v1/products/categories",
            params={"per_page": 100, "slug": slug},
        )
        if response.status_code != 200:
            response = await client.get(
                f"{origin}/wp-json/wc/store/v1/products/categories",
                params={"per_page": 100},
            )
        if response.status_code != 200:
            return None
        data = response.json()
        if not isinstance(data, list):
            return None
        for cat in data:
            if isinstance(cat, dict) and cat.get("slug") == slug:
                cid = cat.get("id")
                return int(cid) if cid is not None else None
    except Exception:  # noqa: BLE001
        return None
    return None


async def _try_woocommerce(
    url: str, *, limit: int, html_hint: str | None
) -> list[dict[str, Any]]:
    if not _should_try_woocommerce(url, html_hint):
        return []

    origin = _origin(url)
    path = urlparse(url).path.rstrip("/").lower()
    headers = build_headers()
    headers.update(
        {
            "Accept": "application/json",
            "Referer": url,
        }
    )

    params: dict[str, str] = {
        "per_page": str(min(100, max(limit, 1))),
        "page": "1",
    }
    if any(token in path for token in ("/promociones", "/ofertas", "/sale", "/on-sale")):
        params["on_sale"] = "true"

    cat_match = re.search(
        r"/(?:product-category|categoria-producto|productos/categoria)/([^/]+)",
        path,
    )
    endpoints = [
        f"{origin}/wp-json/wc/store/v1/products",
        f"{origin}/wp-json/wc/store/products",
    ]

    try:
        async with httpx.AsyncClient(timeout=40.0, headers=headers, follow_redirects=True) as client:
            if cat_match:
                cat_id = await _woocommerce_category_id(client, origin, cat_match.group(1))
                if cat_id is not None:
                    params["category"] = str(cat_id)

            for api in endpoints:
                all_products: list[dict[str, Any]] = []
                page = 1
                while len(all_products) < limit and page <= 20:
                    page_params = {**params, "page": str(page), "per_page": str(min(100, limit))}
                    try:
                        response = await client.get(api, params=page_params)
                    except Exception:  # noqa: BLE001
                        break
                    if response.status_code != 200:
                        break
                    ctype = (response.headers.get("content-type") or "").lower()
                    if "json" not in ctype:
                        break
                    try:
                        payload = response.json()
                    except Exception:  # noqa: BLE001
                        break
                    batch = _normalize_woocommerce_store_payload(
                        payload, limit=limit - len(all_products)
                    )
                    if not batch:
                        break
                    all_products.extend(batch)
                    if len(batch) < int(page_params["per_page"]):
                        break
                    page += 1

                if all_products:
                    # Si pedimos on_sale y vino vacío en otro endpoint, no aplica
                    log.info(
                        "woocommerce_store_products",
                        count=len(all_products),
                        limit=limit,
                        host=urlparse(url).netloc,
                        on_sale=params.get("on_sale"),
                        category=params.get("category"),
                    )
                    return all_products[:limit]
    except Exception as exc:  # noqa: BLE001
        log.warning("woocommerce_probe_failed", error=str(exc), host=urlparse(url).netloc)

    # Fallback: parsear cards Woo del HTML
    if html_hint and "product" in html_hint.lower():
        html_products = _parse_woocommerce_html_cards(
            html_hint, base_url=origin + "/", limit=limit
        )
        if html_products:
            log.info(
                "woocommerce_html_products",
                count=len(html_products),
                limit=limit,
                host=urlparse(url).netloc,
            )
            return html_products
    return []


def _parse_woocommerce_html_cards(
    html: str, *, base_url: str, limit: int
) -> list[dict[str, Any]]:
    tree = HTMLParser(html)
    cards = tree.css("li.product, li.product-small, .products .product")
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for card in cards:
        if len(found) >= limit:
            break
        title_el = (
            card.css_first(".woocommerce-loop-product__title")
            or card.css_first(".product-title")
            or card.css_first("h2")
            or card.css_first("h3")
        )
        title = title_el.text(strip=True) if title_el else ""
        if not title:
            continue
        link_el = (
            card.css_first("a.woocommerce-LoopProduct-link")
            or card.css_first("a.woocommerce-loop-product__link")
            or card.css_first("a[href*='/product']")
            or card.css_first("a[href]")
        )
        href = link_el.attributes.get("href") if link_el else None
        prod_url = urljoin(base_url, href) if isinstance(href, str) else None
        price_el = (
            card.css_first("ins .amount")
            or card.css_first(".price ins .woocommerce-Price-amount")
            or card.css_first(".price .amount")
            or card.css_first(".amount")
        )
        price_raw = price_el.text(strip=True) if price_el else None
        price = _parse_ars_price(price_raw)
        img_el = card.css_first("img")
        image = None
        if img_el:
            image = (
                img_el.attributes.get("data-src")
                or img_el.attributes.get("src")
                or img_el.attributes.get("data-lazy-src")
            )
            if isinstance(image, str) and not image.startswith("http"):
                image = urljoin(base_url, image)
        key = f"{title}|{prod_url or ''}"
        if key in seen:
            continue
        seen.add(key)
        found.append(
            {
                "title": title,
                "price": price,
                "currency": "ARS",
                "description": title,
                "image": image if isinstance(image, str) and image.startswith("http") else None,
                "url": prod_url,
                "brand": None,
            }
        )
    return found


async def _fetch_coto_offer(
    offer_slug: str, *, page_url: str, limit: int
) -> list[dict[str, Any]]:
    api = (
        "https://api.coto.com.ar/api/v1/ms-digital-sitio-bff-web/"
        f"api/v1/products/offers/{offer_slug}"
    )
    params = {
        "key": COTO_API_KEY,
        "num_results_per_page": str(limit),
        "pre_filter_expression": '{"name":"store_availability","value":"200"}',
        "c": "cio-fe-web-coto-4.2.0",
    }
    headers = build_headers()
    headers.update(
        {
            "Accept": "application/json",
            "Origin": "https://www.coto.com.ar",
            "Referer": page_url,
        }
    )
    try:
        async with httpx.AsyncClient(timeout=30.0, headers=headers, follow_redirects=True) as client:
            response = await client.get(api, params=params)
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:  # noqa: BLE001
        log.warning("coto_api_failed", offer=offer_slug, error=str(exc))
        return []

    products = extract_products_from_payload(
        payload,
        base_url="https://www.coto.com.ar/",
        limit=limit,
    )
    for prod in products:
        link = prod.get("url")
        if isinstance(link, str) and link.startswith("_/"):
            prod["url"] = f"https://www.coto.com.ar/{link.lstrip('/')}"
        elif isinstance(link, str) and not link.startswith("http"):
            host = urlparse(page_url).scheme + "://" + urlparse(page_url).netloc
            prod["url"] = host + "/" + link.lstrip("/")
    log.info("coto_api_products", offer=offer_slug, count=len(products), limit=limit)
    return products


async def _fetch_vtex_category_path(
    origin: str,
    category_path: str,
    *,
    page_url: str,
    limit: int,
) -> list[dict[str, Any]]:
    api = f"{origin.rstrip('/')}/api/catalog_system/pub/products/search{category_path}"
    return await _fetch_vtex_paginated(
        api,
        params_base={},
        page_url=page_url,
        origin=origin,
        limit=limit,
        log_event="vtex_category_products",
        log_extra={"path": category_path, "host": urlparse(origin).netloc},
    )


async def _fetch_vtex_cluster(
    origin: str, cluster_id: str, *, page_url: str, limit: int
) -> list[dict[str, Any]]:
    api = f"{origin.rstrip('/')}/api/catalog_system/pub/products/search"
    return await _fetch_vtex_paginated(
        api,
        params_base={"fq": f"productClusterIds:{cluster_id}"},
        page_url=page_url,
        origin=origin,
        limit=limit,
        log_event="vtex_cluster_products",
        log_extra={"cluster": cluster_id, "host": urlparse(origin).netloc},
    )


async def _fetch_vtex_intelligent_search(
    origin: str, category_facets: str, *, page_url: str, limit: int
) -> list[dict[str, Any]]:
    """VTEX Intelligent Search por facets de categoría."""
    api = (
        f"{origin.rstrip('/')}/api/io/_v/api/intelligent-search/"
        f"product_search/{category_facets}"
    )
    headers = build_headers()
    headers.update(
        {
            "Accept": "application/json",
            "Origin": origin,
            "Referer": page_url,
        }
    )
    all_products: list[dict[str, Any]] = []
    page = 1
    page_size = min(50, limit)
    base_url = origin if origin.endswith("/") else origin + "/"
    try:
        async with httpx.AsyncClient(timeout=40.0, headers=headers, follow_redirects=True) as client:
            while len(all_products) < limit:
                response = await client.get(
                    api,
                    params={"page": str(page), "count": str(page_size)},
                )
                if response.status_code != 200:
                    break
                ctype = (response.headers.get("content-type") or "").lower()
                if "json" not in ctype:
                    break
                payload = response.json()
                batch = extract_products_from_payload(
                    payload,
                    base_url=base_url,
                    limit=limit - len(all_products),
                )
                if not batch:
                    break
                all_products.extend(batch)
                if len(batch) < page_size:
                    break
                page += 1
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "vtex_intelligent_search_failed",
            error=str(exc),
            host=urlparse(origin).netloc,
        )
        return all_products[:limit]

    if all_products:
        log.info(
            "vtex_intelligent_search_products",
            count=len(all_products),
            limit=limit,
            host=urlparse(origin).netloc,
            facets=category_facets,
        )
    return all_products[:limit]


async def _fetch_vtex_paginated(
    api: str,
    *,
    params_base: dict[str, str],
    page_url: str,
    origin: str,
    limit: int,
    log_event: str,
    log_extra: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    headers = build_headers()
    headers.update(
        {
            "Accept": "application/json",
            "Origin": origin,
            "Referer": page_url,
        }
    )
    base_url = origin if origin.endswith("/") else origin + "/"
    all_products: list[dict[str, Any]] = []
    offset = 0
    try:
        async with httpx.AsyncClient(timeout=40.0, headers=headers, follow_redirects=True) as client:
            while len(all_products) < limit:
                page_end = min(offset + VTEX_PAGE_SIZE, limit) - 1
                if page_end < offset:
                    break
                params = {
                    **params_base,
                    "_from": str(offset),
                    "_to": str(page_end),
                }
                response = await client.get(api, params=params)
                if response.status_code not in {200, 206}:
                    break
                ctype = (response.headers.get("content-type") or "").lower()
                if "json" not in ctype:
                    break
                try:
                    payload = response.json()
                except Exception:  # noqa: BLE001
                    break
                # Evitar falsos positivos (HTML/JSON genérico sin productos VTEX)
                if isinstance(payload, list) and payload and not isinstance(payload[0], dict):
                    break
                if isinstance(payload, list) and payload and "productName" not in payload[0] and "productId" not in payload[0]:
                    # Puede ser otra forma; igual intentamos normalizar
                    pass
                batch = extract_products_from_payload(
                    payload,
                    base_url=base_url,
                    limit=limit - len(all_products),
                )
                if not batch:
                    break
                all_products.extend(batch)
                expected = page_end - offset + 1
                if len(batch) < expected:
                    break
                offset = page_end + 1
    except Exception as exc:  # noqa: BLE001
        log.debug(f"{log_event}_failed", error=str(exc), **(log_extra or {}))
        return all_products[:limit]

    if all_products:
        log.info(log_event, count=len(all_products), limit=limit, **(log_extra or {}))
    return all_products[:limit]
