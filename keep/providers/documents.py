"""
Document providers for fetching content from various URI schemes.
"""

from pathlib import Path

from .base import Document, DocumentProvider, get_registry


def extract_html_text(html_content: str) -> str:
    """
    Extract readable text from HTML, removing scripts and styles.

    Used by both FileDocumentProvider and HttpDocumentProvider to ensure
    consistent content regularization for embedding and summarization.

    Args:
        html_content: Raw HTML string

    Returns:
        Extracted text with whitespace normalized

    Raises:
        ImportError: If beautifulsoup4 is not installed
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html_content, "html.parser")

    # Remove script and style elements
    for script in soup(["script", "style"]):
        script.decompose()

    # Get text
    text = soup.get_text()

    # Clean up whitespace
    lines = (line.strip() for line in text.splitlines())
    chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
    return '\n'.join(chunk for chunk in chunks if chunk)


class FileDocumentProvider:
    """
    Fetches documents from the local filesystem.

    Supports file:// URIs and attempts to detect content type from extension.
    Performs text extraction for PDF and HTML files.
    """

    EXTENSION_TYPES = {
        ".md": "text/markdown",
        ".markdown": "text/markdown",
        ".txt": "text/plain",
        ".py": "text/x-python",
        ".js": "text/javascript",
        ".ts": "text/typescript",
        ".json": "application/json",
        ".yaml": "text/yaml",
        ".yml": "text/yaml",
        ".html": "text/html",
        ".htm": "text/html",
        ".css": "text/css",
        ".xml": "application/xml",
        ".rst": "text/x-rst",
        ".pdf": "application/pdf",
    }
    
    def supports(self, uri: str) -> bool:
        """Check if this is a file:// URI or bare path."""
        return uri.startswith("file://") or uri.startswith("/")
    
    def fetch(self, uri: str) -> Document:
        """Read file content from the filesystem with text extraction for PDF/HTML."""
        # Normalize to path
        if uri.startswith("file://"):
            path_str = uri.removeprefix("file://")
        else:
            path_str = uri

        path = Path(path_str).resolve()

        if not path.exists():
            raise IOError(f"File not found: {path}")

        if not path.is_file():
            raise IOError(f"Not a file: {path}")

        # Reject paths outside user's home directory as a safety boundary
        home = Path.home().resolve()
        if not path.is_relative_to(home):
            raise IOError(f"Path traversal blocked: {path} is outside home directory")

        # Detect content type
        suffix = path.suffix.lower()
        content_type = self.EXTENSION_TYPES.get(suffix, "text/plain")

        # Extract text based on file type
        if suffix == ".pdf":
            content = self._extract_pdf_text(path)
        elif suffix in (".html", ".htm"):
            content = self._extract_html_text(path)
        else:
            # Read as plain text
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                raise IOError(f"Cannot read file as text: {path}")

        # Gather metadata
        stat = path.stat()
        metadata = {
            "size": stat.st_size,
            "modified": stat.st_mtime,
            "name": path.name,
        }

        return Document(
            uri=f"file://{path.resolve()}",  # Normalize to absolute
            content=content,
            content_type=content_type,
            metadata=metadata,
        )

    def _extract_pdf_text(self, path: Path) -> str:
        """Extract text from PDF file."""
        try:
            from pypdf import PdfReader
        except ImportError:
            raise IOError(
                f"PDF support requires 'pypdf' library. "
                f"Install with: pip install pypdf\n"
                f"Cannot read PDF: {path}"
            )

        try:
            reader = PdfReader(path)
            text_parts = []
            for page in reader.pages:
                text = page.extract_text()
                if text:
                    text_parts.append(text)

            if not text_parts:
                raise IOError(f"No text extracted from PDF: {path}")

            return "\n\n".join(text_parts)
        except Exception as e:
            raise IOError(f"Failed to extract text from PDF {path}: {e}")

    def _extract_html_text(self, path: Path) -> str:
        """Extract text from HTML file."""
        try:
            html_content = path.read_text(encoding="utf-8")
            return extract_html_text(html_content)
        except ImportError:
            raise IOError(
                f"HTML text extraction requires 'beautifulsoup4' library. "
                f"Install with: pip install beautifulsoup4\n"
                f"Cannot extract text from HTML: {path}"
            )
        except Exception as e:
            raise IOError(f"Failed to extract text from HTML {path}: {e}")


class HttpDocumentProvider:
    """
    Fetches documents from HTTP/HTTPS URLs.
    
    Requires the `requests` library (optional dependency).
    """
    
    def __init__(self, timeout: int = 30, max_size: int = 10_000_000):
        """
        Args:
            timeout: Request timeout in seconds
            max_size: Maximum content size in bytes
        """
        self.timeout = timeout
        self.max_size = max_size
    
    def supports(self, uri: str) -> bool:
        """Check if this is an HTTP(S) URL."""
        return uri.startswith("http://") or uri.startswith("https://")
    
    @staticmethod
    def _is_private_url(uri: str) -> bool:
        """Check if URL targets a private/internal network address."""
        from urllib.parse import urlparse
        import ipaddress
        import socket

        parsed = urlparse(uri)
        hostname = parsed.hostname
        if not hostname:
            return True

        # Block known metadata endpoints and localhost
        if hostname in ("metadata.google.internal",):
            return True

        try:
            addr = ipaddress.ip_address(hostname)
            return (addr.is_private or addr.is_loopback or addr.is_link_local
                    or addr.is_reserved or addr.is_unspecified or addr.is_multicast)
        except ValueError:
            pass  # Not an IP literal — resolve it

        try:
            for _, _, _, _, sockaddr in socket.getaddrinfo(hostname, None):
                addr = ipaddress.ip_address(sockaddr[0])
                if (addr.is_private or addr.is_loopback or addr.is_link_local
                        or addr.is_reserved or addr.is_unspecified or addr.is_multicast):
                    return True
        except socket.gaierror:
            pass  # DNS failure will be caught by requests

        return False

    _MAX_REDIRECTS = 5

    def fetch(self, uri: str) -> Document:
        """Fetch content from HTTP URL with text extraction for HTML."""
        if self._is_private_url(uri):
            raise IOError(f"Blocked request to private/internal address: {uri}")

        try:
            import requests
        except ImportError:
            raise RuntimeError("HTTP document fetching requires 'requests' library")

        from keep import __version__

        # Follow redirects manually so each hop is validated against SSRF
        target = uri
        for _ in range(self._MAX_REDIRECTS):
            resp = requests.get(
                target,
                timeout=self.timeout,
                headers={"User-Agent": f"keep/{__version__}"},
                stream=True,
                allow_redirects=False,
            )
            if resp.is_redirect:
                target = resp.headers.get("Location", "")
                if not target.startswith(("http://", "https://")):
                    raise IOError(f"Redirect to unsupported scheme: {target}")
                if self._is_private_url(target):
                    raise IOError(f"Redirect to private/internal address blocked: {target}")
                resp.close()
                continue
            break
        else:
            raise IOError(f"Too many redirects fetching {uri}")

        try:
            with resp:
                resp.raise_for_status()

                # Check declared size
                content_length = resp.headers.get("content-length")
                if content_length:
                    try:
                        if int(content_length) > self.max_size:
                            raise IOError(f"Content too large: {content_length} bytes")
                    except ValueError:
                        pass  # Malformed header — enforce via iter_content below

                # Read content in chunks with enforced size limit
                chunks: list[bytes] = []
                downloaded = 0
                for chunk in resp.iter_content(chunk_size=65536):
                    downloaded += len(chunk)
                    if downloaded > self.max_size:
                        chunks.append(chunk[:self.max_size - (downloaded - len(chunk))])
                        break
                    chunks.append(chunk)
                raw = b"".join(chunks)

                # Decode using the response's detected encoding
                encoding = resp.encoding or "utf-8"
                content = raw.decode(encoding, errors="replace")

                # Get content type
                content_type = resp.headers.get("content-type", "text/plain")
                if ";" in content_type:
                    content_type = content_type.split(";")[0].strip()

                # Extract text from HTML content
                if content_type == "text/html":
                    try:
                        content = extract_html_text(content)
                    except ImportError:
                        # Graceful fallback: use raw HTML if bs4 not installed
                        pass

                return Document(
                    uri=uri,
                    content=content,
                    content_type=content_type,
                    metadata={
                        "status_code": resp.status_code,
                        "headers": dict(resp.headers),
                    },
                )
        except requests.RequestException as e:
            raise IOError(f"Failed to fetch {uri}: {e}")


class CompositeDocumentProvider:
    """
    Combines multiple document providers, delegating to the appropriate one.
    
    This is the default provider used by Keeper.
    """
    
    def __init__(self, providers: list[DocumentProvider] | None = None):
        """
        Args:
            providers: List of providers to try. If None, uses defaults.
        """
        if providers is None:
            self._providers = [
                FileDocumentProvider(),
                HttpDocumentProvider(),
            ]
        else:
            self._providers = list(providers)
    
    def supports(self, uri: str) -> bool:
        """Check if any provider supports this URI."""
        return any(p.supports(uri) for p in self._providers)
    
    def fetch(self, uri: str) -> Document:
        """Fetch using the first provider that supports this URI."""
        for provider in self._providers:
            if provider.supports(uri):
                return provider.fetch(uri)
        
        raise ValueError(f"No provider supports URI: {uri}")
    
    def add_provider(self, provider: DocumentProvider) -> None:
        """Add a provider to the list (checked first)."""
        self._providers.insert(0, provider)


# Register providers
_registry = get_registry()
_registry.register_document("file", FileDocumentProvider)
_registry.register_document("http", HttpDocumentProvider)
_registry.register_document("composite", CompositeDocumentProvider)
