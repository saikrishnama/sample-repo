"""
Confluence ingestion pipeline for the Databricks Onboarding Chatbot.

Confluence is the source of truth.

Flow:
    Confluence API
        -> page/version/hash detection
        -> HTML cleanup + section extraction
        -> WIP metadata detection
        -> heading-aware chunks
        -> Delta Lake
        -> Databricks AI Search

WIP pages are retained for audit/search purposes but are marked with:
    document_status = WIP
    is_wip = True
    wip_detected_from = title/content/both

The chatbot should normally filter WIP documents out of
authoritative onboarding answers.
"""

import hashlib
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List

import requests
from bs4 import BeautifulSoup

from pyspark.sql import SparkSession
from pyspark.sql.types import (
    BooleanType,
    LongType,
    StringType,
    StructField,
    StructType,
)


CONFLUENCE_URL = os.getenv("CONFLUENCE_URL")
CONFLUENCE_TOKEN = os.getenv("CONFLUENCE_TOKEN")
CONFLUENCE_SPACE = os.getenv("CONFLUENCE_SPACE")

DELTA_TABLE = os.getenv(
    "CONFLUENCE_DELTA_TABLE",
    "main.unified_platform.confluence_chunks",
)

CHUNK_SIZE = int(os.getenv("CONFLUENCE_CHUNK_SIZE", "1200"))
CHUNK_OVERLAP = int(os.getenv("CONFLUENCE_CHUNK_OVERLAP", "200"))
REQUEST_TIMEOUT = int(os.getenv("CONFLUENCE_REQUEST_TIMEOUT", "30"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger("confluence_ingestion")


WIP_PATTERNS = [
    r"\bw\.?i\.?p\.?\b",
    r"\bwork\s+in\s+progress\b",
]


def validate_configuration() -> None:
    required = {
        "CONFLUENCE_URL": CONFLUENCE_URL,
        "CONFLUENCE_TOKEN": CONFLUENCE_TOKEN,
        "CONFLUENCE_SPACE": CONFLUENCE_SPACE,
    }

    missing = [name for name, value in required.items() if not value]

    if missing:
        raise RuntimeError(
            f"Missing required configuration: {', '.join(missing)}"
        )


class ConfluenceClient:
    """Lightweight Confluence Cloud REST API client."""

    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )

    def search_pages(
        self,
        space_key: str,
        limit: int = 50,
        start: int = 0,
    ) -> Dict[str, Any]:
        cql = (
            f'space = "{space_key}" '
            f"AND type = page "
            f"AND status = current "
            f"ORDER BY lastmodified DESC"
        )

        url = f"{self.base_url}/wiki/rest/api/search"

        response = self.session.get(
            url,
            params={"cql": cql, "limit": limit, "start": start},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()

    def get_page(self, page_id: str) -> Dict[str, Any]:
        url = f"{self.base_url}/wiki/rest/api/content/{page_id}"

        response = self.session.get(
            url,
            params={
                "expand": "body.storage,version,space,ancestors",
            },
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()

    def get_all_pages(
        self,
        space_key: str,
        page_size: int = 50,
    ) -> List[Dict[str, Any]]:
        pages = []
        start = 0

        while True:
            logger.info(
                "Fetching Confluence pages: start=%s limit=%s",
                start,
                page_size,
            )

            result = self.search_pages(
                space_key=space_key,
                limit=page_size,
                start=start,
            )

            results = result.get("results", [])

            if not results:
                break

            pages.extend(results)

            if len(results) < page_size:
                break

            start += page_size

        logger.info("Found %s Confluence pages", len(pages))
        return pages


def clean_confluence_html(html: str) -> str:
    """Convert Confluence storage HTML into readable text."""
    if not html:
        return ""

    soup = BeautifulSoup(html, "html.parser")

    for element in soup(["script", "style", "noscript"]):
        element.decompose()

    for tag in soup.find_all(
        [
            "h1", "h2", "h3", "h4", "h5", "h6",
            "p", "li", "pre", "blockquote", "tr",
        ]
    ):
        tag.insert_before("\n")

    text = soup.get_text(separator="\n", strip=True)

    lines = []
    for line in text.splitlines():
        line = " ".join(line.split())
        if line:
            lines.append(line)

    return "\n".join(lines)


def extract_sections(html: str) -> List[Dict[str, str]]:
    """
    Extract content while preserving heading context.

    Example:
        Heading: Cluster Access
        Content: Request access through ...

    becomes:
        {"section": "Cluster Access", "content": "..."}
    """
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    sections = []

    current_heading = "Introduction"
    current_content = []

    for element in soup.find_all(
        [
            "h1", "h2", "h3", "h4", "h5", "h6",
            "p", "li", "pre", "blockquote",
        ]
    ):
        text = element.get_text(" ", strip=True)

        if not text:
            continue

        if element.name.startswith("h"):
            if current_content:
                sections.append(
                    {
                        "section": current_heading,
                        "content": "\n".join(current_content),
                    }
                )

            current_heading = text
            current_content = []
        else:
            current_content.append(text)

    if current_content:
        sections.append(
            {
                "section": current_heading,
                "content": "\n".join(current_content),
            }
        )

    return sections


def detect_wip(title: str, content: str) -> Dict[str, Any]:
    """
    Detect WIP markers in title or page content.

    Supported examples:
        WIP
        W.I.P
        W.I.P.
        Work in Progress
        work in progress
    """
    title = title or ""
    content = content or ""

    title_is_wip = any(
        re.search(pattern, title, flags=re.IGNORECASE)
        for pattern in WIP_PATTERNS
    )

    content_is_wip = any(
        re.search(pattern, content, flags=re.IGNORECASE)
        for pattern in WIP_PATTERNS
    )

    if title_is_wip and content_is_wip:
        detected_from = "both"
    elif title_is_wip:
        detected_from = "title"
    elif content_is_wip:
        detected_from = "content"
    else:
        detected_from = None

    return {
        "is_wip": title_is_wip or content_is_wip,
        "document_status": (
            "WIP" if title_is_wip or content_is_wip else "APPROVED"
        ),
        "wip_detected_from": detected_from,
    }


def calculate_content_hash(content: str) -> str:
    normalized = content.strip()
    return hashlib.sha256(
        normalized.encode("utf-8")
    ).hexdigest()


def chunk_text(
    text: str,
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> List[str]:
    """Simple overlapping chunker for the initial implementation."""
    text = text.strip()

    if not text:
        return []

    if len(text) <= chunk_size:
        return [text]

    if overlap >= chunk_size:
        raise ValueError("CHUNK_OVERLAP must be smaller than CHUNK_SIZE")

    chunks = []
    start = 0
    text_length = len(text)

    while start < text_length:
        end = min(start + chunk_size, text_length)
        chunk = text[start:end].strip()

        if chunk:
            chunks.append(chunk)

        if end >= text_length:
            break

        start = end - overlap

    return chunks


def build_page_url(base_url: str, page_id: str) -> str:
    return f"{base_url.rstrip('/')}/wiki/pages/{page_id}"


def process_page(
    client: ConfluenceClient,
    page_summary: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Fetch, classify, chunk, and enrich one Confluence page."""

    page_id = str(
        page_summary.get(
            "content", {}
        ).get(
            "id",
            page_summary.get("id", ""),
        )
    )

    if not page_id:
        logger.warning("Skipping page without ID")
        return []

    page = client.get_page(page_id)

    title = page.get("title", "Untitled")

    body = (
        page.get("body", {})
        .get("storage", {})
        .get("value", "")
    )

    version = page.get("version", {}).get("number")
    last_modified = page.get("version", {}).get("when")
    space_key = page.get("space", {}).get("key")

    page_url = build_page_url(CONFLUENCE_URL, page_id)

    cleaned_page_content = clean_confluence_html(body)

    wip_metadata = detect_wip(
        title=title,
        content=cleaned_page_content,
    )

    logger.info(
        "Page '%s' (%s): status=%s detected_from=%s",
        title,
        page_id,
        wip_metadata["document_status"],
        wip_metadata["wip_detected_from"],
    )

    sections = extract_sections(body)

    if not sections:
        sections = [
            {
                "section": "Content",
                "content": cleaned_page_content,
            }
        ]

    chunks = []

    for section in sections:
        section_name = section["section"]
        section_content = section["content"]

        section_chunks = chunk_text(section_content)

        for index, content in enumerate(section_chunks):
            chunk_id = f"{page_id}-{version}-{index}"
            content_hash = calculate_content_hash(content)

            chunks.append(
                {
                    "chunk_id": chunk_id,
                    "page_id": page_id,
                    "page_title": title,
                    "space_key": space_key,
                    "section": section_name,
                    "content": content,
                    "page_url": page_url,
                    "version": int(version) if version is not None else None,
                    "last_modified": last_modified,
                    "content_hash": content_hash,
                    "status": "active",

                    # WIP metadata
                    "document_status": wip_metadata["document_status"],
                    "is_wip": wip_metadata["is_wip"],
                    "wip_detected_from": wip_metadata[
                        "wip_detected_from"
                    ],

                    "ingested_at": datetime.now(
                        timezone.utc
                    ).isoformat(),
                }
            )

    logger.info(
        "Processed page '%s' (%s): %s chunks",
        title,
        page_id,
        len(chunks),
    )

    return chunks


def get_delta_schema() -> StructType:
    return StructType(
        [
            StructField("chunk_id", StringType(), False),
            StructField("page_id", StringType(), False),
            StructField("page_title", StringType(), True),
            StructField("space_key", StringType(), True),
            StructField("section", StringType(), True),
            StructField("content", StringType(), True),
            StructField("page_url", StringType(), True),
            StructField("version", LongType(), True),
            StructField("last_modified", StringType(), True),
            StructField("content_hash", StringType(), True),
            StructField("status", StringType(), True),

            StructField("document_status", StringType(), True),
            StructField("is_wip", BooleanType(), True),
            StructField("wip_detected_from", StringType(), True),

            StructField("ingested_at", StringType(), True),
        ]
    )


class DeltaWriter:
    """Write Confluence chunks into a Unity Catalog Delta table."""

    def __init__(self, spark: SparkSession, table_name: str):
        self.spark = spark
        self.table_name = table_name

    def ensure_table(self) -> None:
        self.spark.sql(
            f"""
            CREATE TABLE IF NOT EXISTS {self.table_name}
            (
                chunk_id STRING,
                page_id STRING,
                page_title STRING,
                space_key STRING,
                section STRING,
                content STRING,
                page_url STRING,
                version BIGINT,
                last_modified STRING,
                content_hash STRING,
                status STRING,

                document_status STRING,
                is_wip BOOLEAN,
                wip_detected_from STRING,

                ingested_at STRING
            )
            USING DELTA
            """
        )

    def write_chunks(
        self,
        chunks: List[Dict[str, Any]],
    ) -> None:
        if not chunks:
            logger.info("No chunks to write.")
            return

        df = self.spark.createDataFrame(
            chunks,
            schema=get_delta_schema(),
        )

        df.createOrReplaceTempView(
            "confluence_chunks_staging"
        )

        self.spark.sql(
            f"""
            MERGE INTO {self.table_name} AS target
            USING confluence_chunks_staging AS source
            ON target.chunk_id = source.chunk_id

            WHEN MATCHED THEN UPDATE SET
                target.page_id = source.page_id,
                target.page_title = source.page_title,
                target.space_key = source.space_key,
                target.section = source.section,
                target.content = source.content,
                target.page_url = source.page_url,
                target.version = source.version,
                target.last_modified = source.last_modified,
                target.content_hash = source.content_hash,
                target.status = source.status,
                target.document_status = source.document_status,
                target.is_wip = source.is_wip,
                target.wip_detected_from = source.wip_detected_from,
                target.ingested_at = source.ingested_at

            WHEN NOT MATCHED THEN INSERT *
            """
        )

        logger.info(
            "Merged %s chunks into %s",
            len(chunks),
            self.table_name,
        )

    def deactivate_page(self, page_id: str) -> None:
        """
        Mark a page as deleted rather than physically removing it.
        """
        self.spark.sql(
            f"""
            UPDATE {self.table_name}
            SET status = 'deleted'
            WHERE page_id = '{page_id}'
            """
        )

        logger.info("Marked page %s as deleted", page_id)


def run_full_ingestion() -> None:
    """
    Run a full Confluence ingestion.

    Use this for initial indexing and periodic reconciliation.
    """
    validate_configuration()

    spark = SparkSession.builder.getOrCreate()

    client = ConfluenceClient(
        base_url=CONFLUENCE_URL,
        token=CONFLUENCE_TOKEN,
    )

    writer = DeltaWriter(
        spark=spark,
        table_name=DELTA_TABLE,
    )

    writer.ensure_table()

    pages = client.get_all_pages(
        space_key=CONFLUENCE_SPACE
    )

    total_chunks = 0

    for page in pages:
        try:
            chunks = process_page(
                client=client,
                page_summary=page,
            )

            writer.write_chunks(chunks)
            total_chunks += len(chunks)

        except Exception:
            page_id = page.get("id")

            logger.exception(
                "Failed to process Confluence page %s",
                page_id,
            )

    logger.info(
        "Confluence ingestion completed. Pages=%s Chunks=%s",
        len(pages),
        total_chunks,
    )


if __name__ == "__main__":
    run_full_ingestion()
