"""Tests del podador HTML / extracción de imágenes."""

from scraper_agent.html_cleaner import extract_images, html_to_clean_document, prune_html

SAMPLE_HTML = """
<html>
<head>
  <title>Demo Shop</title>
  <script>window.tracker = true;</script>
  <style>.x{color:red}</style>
</head>
<body>
  <nav>Home | Cart</nav>
  <main>
    <h1>Auriculares Bluetooth</h1>
    <p>Precio: $ 45.990 ARS. Excelente sonido.</p>
    <img src="/img/product-main.jpg" alt="Auriculares" />
    <img data-src="https://cdn.example.com/product-2.webp" />
    <img src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7" />
  </main>
  <footer>Copyright</footer>
  <script>analytics();</script>
</body>
</html>
"""


def test_prune_removes_noise_tags() -> None:
    pruned = prune_html(SAMPLE_HTML)
    assert "<script" not in pruned.lower()
    assert "<style" not in pruned.lower()
    assert "<nav" not in pruned.lower()
    assert "<footer" not in pruned.lower()
    assert "Auriculares" in pruned


def test_extract_images_absolutizes_and_skips_data_uri() -> None:
    urls = extract_images(SAMPLE_HTML, "https://shop.example.com/item/1")
    assert "https://shop.example.com/img/product-main.jpg" in urls
    assert "https://cdn.example.com/product-2.webp" in urls
    assert all(not u.startswith("data:") for u in urls)


def test_clean_document_includes_markdown_and_images() -> None:
    doc = html_to_clean_document(SAMPLE_HTML, "https://shop.example.com/item/1", max_chars=5000)
    assert "Auriculares" in doc.markdown or len(doc.markdown) > 0
    assert len(doc.image_urls) >= 1
    assert "Detected product images" in doc.markdown
