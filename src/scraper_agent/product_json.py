"""Normalización de JSON de catálogos/APIs a Markdown usable por el LLM."""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _pick_price(data: dict[str, Any]) -> tuple[float | None, str | None]:
    for key in (
        "product_list_price",
        "listPrice",
        "price",
        "salePrice",
        "final_price",
        "amount",
    ):
        raw = data.get(key)
        if isinstance(raw, (int, float)):
            return float(raw), "ARS"
        if isinstance(raw, str):
            cleaned = raw.replace("$", "").replace(".", "").replace(",", ".").strip()
            try:
                return float(cleaned), "ARS"
            except ValueError:
                pass
        if isinstance(raw, list) and raw:
            first = raw[0]
            if isinstance(first, dict):
                for pk in ("listPrice", "formatPrice", "price", "salePrice"):
                    if isinstance(first.get(pk), (int, float)):
                        return float(first[pk]), "ARS"
            if isinstance(first, (int, float)):
                return float(first), "ARS"
        if isinstance(raw, dict):
            for pk in ("listPrice", "formatPrice", "price", "salePrice", "value"):
                if isinstance(raw.get(pk), (int, float)):
                    return float(raw[pk]), "ARS"
    return None, None


def _pick_image(data: dict[str, Any]) -> str | None:
    for key in (
        "product_large_image_url",
        "product_medium_image_url",
        "image_url",
        "image",
        "thumbnail",
        "img",
        "picture",
    ):
        val = data.get(key)
        if isinstance(val, str) and val.startswith("http"):
            return val
        if isinstance(val, list) and val:
            first = val[0]
            if isinstance(first, str) and first.startswith("http"):
                return first
            if isinstance(first, dict):
                for ik in ("url", "src", "large", "medium"):
                    if isinstance(first.get(ik), str) and first[ik].startswith("http"):
                        return first[ik]
    return None


def _pick_title(data: dict[str, Any]) -> str | None:
    for key in (
        "sku_display_name",
        "sku_description",
        "productName",
        "productTitle",
        "name",
        "title",
        "product_name",
        "displayName",
    ):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _pick_url(data: dict[str, Any], base_url: str) -> str | None:
    raw = data.get("url") or data.get("product_url") or data.get("link") or data.get("linkText")
    if not isinstance(raw, str) or not raw.strip():
        return None
    raw = raw.strip()
    if raw.startswith("http"):
        return raw
    # VTEX linkText → /{linkText}/p
    if "linkText" in data and raw == str(data.get("linkText")).strip() and not raw.endswith("/p"):
        raw = f"{raw}/p"
    return urljoin(base_url if base_url.endswith("/") else base_url + "/", raw.lstrip("/"))


def extract_products_from_jsonld(html: str, *, base_url: str, limit: int = 40) -> list[dict[str, Any]]:
    """Extrae Product / ItemList embebidos en JSON-LD (schema.org)."""
    import json
    import re

    blocks = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    found: list[dict[str, Any]] = []

    def from_product(node: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(node, dict):
            return None
        types = node.get("@type")
        type_names = {types} if isinstance(types, str) else set(types or [])
        if "Product" not in type_names and "ProductGroup" not in type_names:
            return None
        title = node.get("name")
        if not isinstance(title, str) or not title.strip():
            return None
        image = node.get("image")
        if isinstance(image, list) and image:
            image = image[0]
        if isinstance(image, dict):
            image = image.get("url") or image.get("contentUrl")
        if not isinstance(image, str):
            image = None
        price: float | None = None
        currency: str | None = None
        offers = node.get("offers")
        offer_nodes: list[Any] = []
        if isinstance(offers, dict):
            offer_nodes = [offers]
            nested = offers.get("offers")
            if isinstance(nested, list):
                offer_nodes.extend(nested)
            elif isinstance(nested, dict):
                offer_nodes.append(nested)
        elif isinstance(offers, list):
            offer_nodes = offers
        for offer in offer_nodes:
            if not isinstance(offer, dict):
                continue
            raw_price = offer.get("price") or offer.get("lowPrice")
            if isinstance(raw_price, (int, float)):
                price = float(raw_price)
                cur = offer.get("priceCurrency")
                currency = cur if isinstance(cur, str) else "ARS"
                break
            if isinstance(raw_price, str):
                try:
                    price = float(raw_price.replace(",", "."))
                    cur = offer.get("priceCurrency")
                    currency = cur if isinstance(cur, str) else "ARS"
                    break
                except ValueError:
                    pass
        brand = node.get("brand")
        if isinstance(brand, dict):
            brand = brand.get("name")
        url = node.get("@id") or node.get("url")
        if isinstance(url, str) and not url.startswith("http"):
            url = urljoin(base_url if base_url.endswith("/") else base_url + "/", url.lstrip("/"))
        return {
            "title": title.strip(),
            "price": price,
            "currency": currency,
            "description": (str(node["description"]) if node.get("description") else title.strip()),
            "image": image if isinstance(image, str) and image.startswith("http") else None,
            "url": url if isinstance(url, str) else None,
            "brand": brand if isinstance(brand, str) else None,
        }

    def walk(node: Any) -> None:
        if len(found) >= limit:
            return
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return
        types = node.get("@type")
        type_names = {types} if isinstance(types, str) else set(types or [])
        if "ItemList" in type_names:
            for entry in node.get("itemListElement") or []:
                if len(found) >= limit:
                    return
                if isinstance(entry, dict):
                    item = entry.get("item") if isinstance(entry.get("item"), dict) else entry
                    prod = from_product(item) if isinstance(item, dict) else None
                    if prod:
                        found.append(prod)
            return
        prod = from_product(node)
        if prod:
            found.append(prod)
            return
        graph = node.get("@graph")
        if graph is not None:
            walk(graph)

    for block in blocks:
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        walk(data)

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for prod in found:
        key = f"{prod.get('title')}|{prod.get('price')}|{prod.get('image')}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(prod)
    return deduped[:limit]


def _normalize_vtex_product(data: dict[str, Any], *, base_url: str) -> dict[str, Any] | None:
    """Formato típico VTEX catalog_system / products/search."""
    if "productName" not in data and "items" not in data:
        return None
    title = _pick_title(data)
    if not title:
        return None
    price: float | None = None
    image: str | None = None
    items = data.get("items")
    if isinstance(items, list) and items:
        sku = items[0] if isinstance(items[0], dict) else {}
        images = sku.get("images") if isinstance(sku, dict) else None
        if isinstance(images, list) and images and isinstance(images[0], dict):
            img = images[0].get("imageUrl")
            if isinstance(img, str) and img.startswith("http"):
                image = img
        sellers = sku.get("sellers") if isinstance(sku, dict) else None
        if isinstance(sellers, list) and sellers and isinstance(sellers[0], dict):
            offer = sellers[0].get("commertialOffer") or sellers[0].get("commercialOffer") or {}
            if isinstance(offer, dict):
                for pk in ("Price", "price", "ListPrice"):
                    if isinstance(offer.get(pk), (int, float)):
                        price = float(offer[pk])
                        break
    brand = data.get("brand")
    description = (
        data.get("description")
        or data.get("metaTagDescription")
        or data.get("productTitle")
        or title
    )
    if isinstance(brand, str) and brand and isinstance(description, str) and brand not in description:
        description = f"{brand} — {description}"
    return {
        "title": title,
        "price": price,
        "currency": "ARS",
        "description": description if isinstance(description, str) else title,
        "image": image,
        "url": _pick_url(data, base_url),
        "brand": brand if isinstance(brand, str) else None,
    }


def normalize_product_dict(raw: dict[str, Any], *, base_url: str) -> dict[str, Any] | None:
    data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    if not isinstance(data, dict):
        return None

    vtex = _normalize_vtex_product(data, base_url=base_url)
    if vtex:
        return vtex

    title = _pick_title(data)
    if not title:
        return None
    price, currency = _pick_price(data)
    image = _pick_image(data)
    # VTEX images sometimes only under items
    if not image and isinstance(data.get("items"), list) and data["items"]:
        sku = data["items"][0]
        if isinstance(sku, dict):
            images = sku.get("images")
            if isinstance(images, list) and images and isinstance(images[0], dict):
                img = images[0].get("imageUrl")
                if isinstance(img, str):
                    image = img
    brand = data.get("product_brand") or data.get("brand")
    description = data.get("sku_description") or data.get("description") or title
    if isinstance(brand, str) and brand and isinstance(description, str):
        if brand not in description:
            description = f"{brand} — {description}"
    return {
        "title": title,
        "price": price,
        "currency": currency,
        "description": description if isinstance(description, str) else title,
        "image": image,
        "url": _pick_url(data, base_url),
        "brand": brand if isinstance(brand, str) else None,
    }


def extract_products_from_payload(payload: Any, *, base_url: str, limit: int = 40) -> list[dict[str, Any]]:
    """Extrae productos de estructuras típicas (Constructor.io / Coto / VTEX / genéricas)."""
    found: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if len(found) >= limit:
            return
        if isinstance(node, list):
            # Lista VTEX de productos
            if node and isinstance(node[0], dict) and (
                "productName" in node[0] or "productId" in node[0]
            ):
                for item in node:
                    if len(found) >= limit:
                        return
                    if isinstance(item, dict):
                        prod = normalize_product_dict(item, base_url=base_url)
                        if prod:
                            found.append(prod)
                return
            for item in node:
                walk(item)
            return
        if isinstance(node, dict):
            if "results" in node and isinstance(node["results"], list):
                for item in node["results"]:
                    if len(found) >= limit:
                        return
                    if isinstance(item, dict):
                        prod = normalize_product_dict(item, base_url=base_url)
                        if prod:
                            found.append(prod)
                return
            if "hits" in node and isinstance(node["hits"], list):
                for item in node["hits"]:
                    if len(found) >= limit:
                        return
                    if isinstance(item, dict):
                        prod = normalize_product_dict(item, base_url=base_url)
                        if prod:
                            found.append(prod)
                return
            if "products" in node and isinstance(node["products"], list):
                for item in node["products"]:
                    if len(found) >= limit:
                        return
                    if isinstance(item, dict):
                        prod = normalize_product_dict(item, base_url=base_url)
                        if prod:
                            found.append(prod)
                return
            prod = normalize_product_dict(node, base_url=base_url)
            if prod and (prod.get("price") is not None or prod.get("image")):
                found.append(prod)
                return
            for value in node.values():
                walk(value)

    walk(payload)
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for prod in found:
        key = f"{prod.get('title')}|{prod.get('price')}|{prod.get('image')}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(prod)
    return deduped[:limit]


def products_to_markdown(products: list[dict[str, Any]]) -> str:
    if not products:
        return ""
    lines = ["## Productos detectados vía API/JSON", ""]
    for idx, prod in enumerate(products, start=1):
        lines.append(f"### {idx}. {prod.get('title')}")
        if prod.get("price") is not None:
            cur = prod.get("currency") or ""
            lines.append(f"- Precio: {prod['price']} {cur}".rstrip())
        if prod.get("description"):
            lines.append(f"- Descripción: {prod['description']}")
        if prod.get("brand"):
            lines.append(f"- Marca: {prod['brand']}")
        if prod.get("url"):
            lines.append(f"- URL: {prod['url']}")
        if prod.get("image"):
            lines.append(f"- Imagen: {prod['image']}")
        lines.append("")
    return "\n".join(lines)


def products_image_urls(products: list[dict[str, Any]]) -> list[str]:
    urls: list[str] = []
    for prod in products:
        img = prod.get("image")
        if isinstance(img, str) and img.startswith("http") and img not in urls:
            urls.append(img)
    return urls


def looks_like_product_api(url: str) -> bool:
    low = url.lower()
    return any(
        token in low
        for token in (
            "/products/",
            "/offers/",
            "catalog",
            "catalog_system",
            "search",
            "constructor",
            "cnstrc.com",
            "api.coto.com.ar",
            "intelligent-search",
            "plp",
            "sku",
            "productcluster",
        )
    )
