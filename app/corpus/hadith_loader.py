"""Hadith Corpus Loader — fetches major hadith collections from
hadithapi.pages.dev and stores them in a ChromaDB collection for
semantic search.

Features
--------
* Semaphore-based concurrency (max 5 simultaneous requests).
* Progress logging every 100 hadiths.
* Checkpoint file per collection so loading can resume after interruption.
* Graceful handling of gaps (missing hadith numbers).
* Batch-upsert into ChromaDB (100 at a time).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BATCH_SIZE = 100
"""Number of documents per ChromaDB upsert batch."""

MAX_CONCURRENT = 5
"""Maximum simultaneous HTTP requests to the hadith API."""

REQUEST_TIMEOUT = 30.0
"""Per-request timeout in seconds."""

CHECKPOINT_DIR = Path("data/corpus_checkpoints")
"""Directory for resume checkpoint files."""


class HadithLoader:
    """Fetches hadith collections and loads them into ChromaDB.

    Primary API
    -----------
    ``https://hadithapi.pages.dev/api/{collection}/{number}``

    Supported collections: bukhari, muslim, abudawud, tirmidhi, nasai,
    ibnmajah.

    Usage::

        import chromadb, asyncio
        from app.corpus.hadith_loader import HadithLoader

        client = chromadb.HttpClient(host="localhost", port=8100)
        loader = HadithLoader()
        asyncio.run(loader.load_all_collections(client))
    """

    API_BASE = "https://hadithapi.pages.dev/api"

    COLLECTIONS: dict[str, int] = {
        "bukhari": 7563,
        "muslim": 7453,
        "abudawud": 5274,
        "tirmidhi": 3956,
        "nasai": 5758,
        "ibnmajah": 4341,
    }

    COLLECTION_DISPLAY_NAMES: dict[str, str] = {
        "bukhari": "Sahih al-Bukhari",
        "muslim": "Sahih Muslim",
        "abudawud": "Sunan Abu Dawud",
        "tirmidhi": "Jami` at-Tirmidhi",
        "nasai": "Sunan an-Nasa'i",
        "ibnmajah": "Sunan Ibn Majah",
    }

    # ── Public API ────────────────────────────────────────────────────────

    async def load_collection(
        self,
        collection: str,
        chroma_client: Any,
        collection_name: str = "hadith_collection",
        *,
        resume: bool = True,
    ) -> int:
        """Fetch and embed an entire hadith collection into ChromaDB.

        Parameters
        ----------
        collection:
            The hadith collection key (e.g. ``"bukhari"``).
        chroma_client:
            A ``chromadb.Client`` or ``chromadb.HttpClient``.
        collection_name:
            Target ChromaDB collection name.
        resume:
            If ``True``, skip hadith numbers already in the checkpoint.

        Returns
        -------
        int
            Number of hadiths successfully loaded in this run.
        """
        if collection not in self.COLLECTIONS:
            raise ValueError(
                f"Unknown collection '{collection}'. "
                f"Valid: {list(self.COLLECTIONS)}"
            )

        total_count = self.COLLECTIONS[collection]
        display_name = self.COLLECTION_DISPLAY_NAMES.get(collection, collection)

        logger.info(
            "hadith_loader.start_collection",
            collection=display_name,
            expected_count=total_count,
        )

        # Load checkpoint (already-fetched numbers)
        checkpoint = self._load_checkpoint(collection) if resume else set()

        # Determine which numbers still need fetching
        numbers_to_fetch = [
            n for n in range(1, total_count + 1) if n not in checkpoint
        ]

        if not numbers_to_fetch:
            logger.info(
                "hadith_loader.collection_already_complete",
                collection=display_name,
            )
            return 0

        logger.info(
            "hadith_loader.fetching",
            collection=display_name,
            remaining=len(numbers_to_fetch),
            already_done=len(checkpoint),
        )

        # Get or create ChromaDB collection
        chroma_col = chroma_client.get_or_create_collection(
            name=collection_name,
            metadata={"description": "Major hadith collections corpus"},
        )

        semaphore = asyncio.Semaphore(MAX_CONCURRENT)
        fetched_hadiths: list[dict[str, Any]] = []
        fetched_numbers: set[int] = set()
        errors = 0

        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            # Process in chunks so we can batch-upsert and checkpoint
            for chunk_start in range(0, len(numbers_to_fetch), BATCH_SIZE):
                chunk = numbers_to_fetch[chunk_start : chunk_start + BATCH_SIZE]

                tasks = [
                    self._fetch_with_semaphore(
                        semaphore, client, collection, num
                    )
                    for num in chunk
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                batch_docs: list[dict[str, Any]] = []
                for num, result in zip(chunk, results):
                    if isinstance(result, Exception):
                        errors += 1
                        continue
                    if result is None:
                        # Missing hadith number (gap)
                        fetched_numbers.add(num)
                        continue

                    batch_docs.append(result)
                    fetched_numbers.add(num)

                # Upsert batch into ChromaDB
                if batch_docs:
                    self._upsert_batch(chroma_col, batch_docs, collection)
                    fetched_hadiths.extend(batch_docs)

                # Save checkpoint
                self._save_checkpoint(collection, checkpoint | fetched_numbers)

                total_done = len(checkpoint) + len(fetched_numbers)
                if total_done % 100 < BATCH_SIZE or chunk_start + BATCH_SIZE >= len(
                    numbers_to_fetch
                ):
                    logger.info(
                        "hadith_loader.progress",
                        collection=display_name,
                        fetched=total_done,
                        total=total_count,
                        errors=errors,
                        pct=round(total_done / total_count * 100, 1),
                    )

        logger.info(
            "hadith_loader.collection_complete",
            collection=display_name,
            new_hadiths=len(fetched_hadiths),
            errors=errors,
        )
        return len(fetched_hadiths)

    async def load_all_collections(
        self,
        chroma_client: Any,
        collection_name: str = "hadith_collection",
    ) -> dict[str, int]:
        """Load all six major hadith collections sequentially.

        Parameters
        ----------
        chroma_client:
            A ``chromadb.Client`` or ``chromadb.HttpClient``.
        collection_name:
            Target ChromaDB collection.

        Returns
        -------
        dict[str, int]
            Mapping of ``collection_key → hadiths_loaded``.
        """
        results: dict[str, int] = {}
        for coll_key in self.COLLECTIONS:
            count = await self.load_collection(
                coll_key, chroma_client, collection_name
            )
            results[coll_key] = count
        logger.info("hadith_loader.all_collections_done", results=results)
        return results

    # ── Internal helpers ──────────────────────────────────────────────────

    async def _fetch_with_semaphore(
        self,
        semaphore: asyncio.Semaphore,
        client: httpx.AsyncClient,
        collection: str,
        number: int,
    ) -> dict[str, Any] | None:
        """Acquire semaphore, then fetch a single hadith."""
        async with semaphore:
            return await self._fetch_hadith(client, collection, number)

    @retry(
        retry=retry_if_exception_type(
            (httpx.HTTPStatusError, httpx.TransportError)
        ),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=15),
        reraise=True,
    )
    async def _fetch_hadith(
        self,
        client: httpx.AsyncClient,
        collection: str,
        number: int,
    ) -> dict[str, Any] | None:
        """Fetch a single hadith from the API.

        Parameters
        ----------
        client:
            Reusable ``httpx.AsyncClient``.
        collection:
            Collection key (e.g. ``"bukhari"``).
        number:
            Hadith number.

        Returns
        -------
        dict | None
            Parsed hadith data, or ``None`` if the number is a gap.
        """
        url = f"{self.API_BASE}/{collection}/{number}"

        response = await client.get(url)

        # Some numbers are gaps — the API may return 404 or an empty body
        if response.status_code == 404:
            return None

        response.raise_for_status()

        try:
            data = response.json()
        except Exception:
            logger.warning(
                "hadith_loader.json_parse_error",
                collection=collection,
                number=number,
            )
            return None

        # The API wraps the hadith in a 'hadiths' list
        hadiths_list = data.get("hadiths", [])
        if not hadiths_list:
            return None

        hadith = hadiths_list[0] if isinstance(hadiths_list, list) else None
        if hadith is None:
            return None

        return {
            "collection": collection,
            "number": number,
            "arabic_text": hadith.get("arabic", "") or hadith.get("text", ""),
            "english_text": hadith.get("english", "") or hadith.get("text", ""),
            "book_name": hadith.get("bookName", ""),
            "chapter_name": hadith.get("chapterName", ""),
            "hadith_number": hadith.get("hadithNumber", str(number)),
            "grade": hadith.get("grade", "unknown"),
            "narrator": hadith.get("header", ""),
            "reference": hadith.get("reference", ""),
        }

    # ── ChromaDB batch upsert ─────────────────────────────────────────────

    @staticmethod
    def _upsert_batch(
        chroma_collection: Any,
        hadiths: list[dict[str, Any]],
        collection_key: str,
    ) -> None:
        """Batch-upsert a list of parsed hadiths into ChromaDB."""
        ids: list[str] = []
        documents: list[str] = []
        metadatas: list[dict[str, Any]] = []

        for h in hadiths:
            hadith_id = f"hadith_{collection_key}_{h['number']:05d}"
            arabic = h.get("arabic_text", "")
            english = h.get("english_text", "")
            doc_text = f"{arabic}\n\n{english}" if arabic else english

            if not doc_text.strip():
                continue

            metadata = {
                "collection": collection_key,
                "hadith_number": h["number"],
                "book_name": h.get("book_name", ""),
                "chapter_name": h.get("chapter_name", ""),
                "grade": h.get("grade", "unknown"),
                "narrator": h.get("narrator", ""),
                "reference": h.get("reference", ""),
                "arabic_text": arabic[:1000],   # ChromaDB metadata size limit
                "english_text": english[:1000],
            }

            ids.append(hadith_id)
            documents.append(doc_text)
            metadatas.append(metadata)

        if ids:
            chroma_collection.upsert(
                ids=ids,
                documents=documents,
                metadatas=metadatas,
            )

    # ── Checkpoint management ─────────────────────────────────────────────

    @staticmethod
    def _checkpoint_path(collection: str) -> Path:
        """Return the checkpoint file path for a collection."""
        CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
        return CHECKPOINT_DIR / f"{collection}_checkpoint.json"

    @classmethod
    def _load_checkpoint(cls, collection: str) -> set[int]:
        """Load previously-fetched hadith numbers from the checkpoint file."""
        path = cls._checkpoint_path(collection)
        if not path.exists():
            return set()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return set(data.get("fetched_numbers", []))
        except Exception:
            logger.warning(
                "hadith_loader.checkpoint_load_error",
                collection=collection,
            )
            return set()

    @classmethod
    def _save_checkpoint(cls, collection: str, fetched: set[int]) -> None:
        """Persist fetched hadith numbers to the checkpoint file."""
        path = cls._checkpoint_path(collection)
        path.write_text(
            json.dumps(
                {"collection": collection, "fetched_numbers": sorted(fetched)},
                indent=2,
            ),
            encoding="utf-8",
        )
