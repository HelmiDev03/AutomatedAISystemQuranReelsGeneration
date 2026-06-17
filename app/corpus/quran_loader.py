"""Quran Corpus Loader — fetches the complete Quran from Al-Quran Cloud API
and inserts all 6 236 verses into a ChromaDB collection for semantic search.

Each verse is stored with its Uthmanic Arabic text, English translation
(Sahih International), and rich metadata (surah, ayah, juz, page, revelation
type).  A character-level verification helper is provided to compare any
rendered text against the canonical Uthmanic script.
"""

from __future__ import annotations

import asyncio
import unicodedata
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

MAX_CONCURRENT_REQUESTS = 5
"""Ceiling on simultaneous HTTP requests to Al-Quran Cloud."""

REQUEST_TIMEOUT = 60.0
"""Per-request timeout in seconds."""


class QuranLoader:
    """Fetches all 6 236 Quran verses from Al-Quran Cloud API and loads
    them into a ChromaDB collection.

    Usage::

        import chromadb, asyncio
        from app.corpus.quran_loader import QuranLoader

        client = chromadb.HttpClient(host="localhost", port=8100)
        loader = QuranLoader()
        asyncio.run(loader.load_full_quran(client))
    """

    API_BASE = "https://api.alquran.cloud/v1"

    # ── Public API ────────────────────────────────────────────────────────

    async def load_full_quran(
        self,
        chroma_client: Any,
        collection_name: str = "quran_verses",
    ) -> int:
        """Fetch and embed the entire Quran into ChromaDB.

        Steps
        -----
        1. ``GET /v1/quran/quran-uthmani`` → all Arabic text (Uthmanic script)
        2. ``GET /v1/quran/en.sahih``      → all English translations
        3. Pair each Arabic verse with its English counterpart.
        4. Batch-insert (100 at a time) into the ChromaDB *collection_name*.

        Parameters
        ----------
        chroma_client:
            A ``chromadb.Client`` or ``chromadb.HttpClient`` instance.
        collection_name:
            Target collection (created automatically if absent).

        Returns
        -------
        int
            Total number of verses inserted.
        """
        logger.info(
            "quran_loader.start",
            collection=collection_name,
        )

        # Fetch both editions concurrently
        arabic_verses, english_verses = await asyncio.gather(
            self._fetch_edition("quran-uthmani"),
            self._fetch_edition("en.sahih"),
        )

        if not arabic_verses:
            raise RuntimeError("Failed to fetch Arabic (Uthmanic) edition")
        if not english_verses:
            raise RuntimeError("Failed to fetch English (Sahih International) edition")

        if len(arabic_verses) != len(english_verses):
            logger.warning(
                "quran_loader.verse_count_mismatch",
                arabic=len(arabic_verses),
                english=len(english_verses),
            )

        # Build a lookup for English verses keyed by (surah, ayah)
        en_lookup: dict[tuple[int, int], dict] = {
            (v["surah"]["number"], v["numberInSurah"]): v
            for v in english_verses
        }

        # Get or create the ChromaDB collection
        collection = chroma_client.get_or_create_collection(
            name=collection_name,
            metadata={"description": "Complete Quran — Uthmanic Arabic + Sahih Intl."},
        )

        # Prepare documents in batches
        ids: list[str] = []
        documents: list[str] = []
        metadatas: list[dict[str, Any]] = []

        for verse in arabic_verses:
            surah_num: int = verse["surah"]["number"]
            ayah_num: int = verse["numberInSurah"]
            arabic_text: str = verse["text"]

            en_verse = en_lookup.get((surah_num, ayah_num), {})
            english_text: str = en_verse.get("text", "")

            # Combined document text for embedding
            doc_text = f"{arabic_text}\n\n{english_text}"

            verse_id = f"quran_{surah_num:03d}_{ayah_num:03d}"

            metadata: dict[str, Any] = {
                "surah_number": surah_num,
                "surah_name_arabic": verse["surah"].get("name", ""),
                "surah_name_english": verse["surah"].get("englishName", ""),
                "surah_name_translation": verse["surah"].get(
                    "englishNameTranslation", ""
                ),
                "ayah_number": ayah_num,
                "ayah_number_global": verse.get("number", 0),
                "juz_number": verse.get("juz", 0),
                "page_number": verse.get("page", 0),
                "hizb_quarter": verse.get("hizbQuarter", 0),
                "revelation_type": verse["surah"].get("revelationType", ""),
                "arabic_text": arabic_text,
                "english_text": english_text,
            }

            ids.append(verse_id)
            documents.append(doc_text)
            metadatas.append(metadata)

        # Batch upsert
        total_inserted = 0
        for start in range(0, len(ids), BATCH_SIZE):
            end = start + BATCH_SIZE
            collection.upsert(
                ids=ids[start:end],
                documents=documents[start:end],
                metadatas=metadatas[start:end],
            )
            total_inserted += len(ids[start:end])
            if total_inserted % 500 == 0 or end >= len(ids):
                logger.info(
                    "quran_loader.batch_upserted",
                    inserted=total_inserted,
                    total=len(ids),
                )

        logger.info(
            "quran_loader.complete",
            total_verses=total_inserted,
            collection=collection_name,
        )
        return total_inserted

    # ── Internal helpers ──────────────────────────────────────────────────

    @retry(
        retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TransportError)),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        reraise=True,
    )
    async def _fetch_edition(self, edition: str) -> list[dict]:
        """Fetch all verses for a given Quran edition.

        Parameters
        ----------
        edition:
            Al-Quran Cloud edition identifier, e.g. ``"quran-uthmani"``
            or ``"en.sahih"``.

        Returns
        -------
        list[dict]
            A list of verse dicts as returned by the API, each containing
            at minimum ``text``, ``surah``, ``numberInSurah``, ``number``,
            ``juz``, ``page``.
        """
        url = f"{self.API_BASE}/quran/{edition}"
        logger.info("quran_loader.fetch_edition", edition=edition, url=url)

        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.get(url)
            response.raise_for_status()

        payload = response.json()

        if payload.get("code") != 200 or "data" not in payload:
            raise RuntimeError(
                f"Unexpected API response for edition '{edition}': "
                f"code={payload.get('code')}"
            )

        surahs = payload["data"].get("surahs", [])
        verses: list[dict] = []
        for surah in surahs:
            for ayah in surah.get("ayahs", []):
                # Attach surah-level metadata to each ayah for convenience
                ayah["surah"] = {
                    "number": surah["number"],
                    "name": surah.get("name", ""),
                    "englishName": surah.get("englishName", ""),
                    "englishNameTranslation": surah.get(
                        "englishNameTranslation", ""
                    ),
                    "revelationType": surah.get("revelationType", ""),
                }
                verses.append(ayah)

        logger.info(
            "quran_loader.fetched_edition",
            edition=edition,
            verse_count=len(verses),
        )
        return verses

    # ── Verification ──────────────────────────────────────────────────────

    async def verify_verse(
        self,
        surah: int,
        ayah: int,
        text: str,
    ) -> bool:
        """Verify a verse character-by-character against the Uthmanic text.

        Fetches the canonical verse from the API and performs a normalised
        comparison.  Diacritics and whitespace are normalised before
        comparison so that trivial formatting differences do not cause
        false negatives.

        Parameters
        ----------
        surah:
            Surah number (1–114).
        ayah:
            Ayah number within the surah.
        text:
            The text to verify.

        Returns
        -------
        bool
            ``True`` if the provided text matches the canonical Uthmanic
            text after normalisation.
        """
        canonical = await self._fetch_single_verse(surah, ayah)
        if canonical is None:
            logger.error(
                "quran_loader.verify_failed",
                surah=surah,
                ayah=ayah,
                reason="could_not_fetch_canonical",
            )
            return False

        normalised_canonical = self._normalise_arabic(canonical)
        normalised_input = self._normalise_arabic(text)

        match = normalised_canonical == normalised_input
        if not match:
            logger.warning(
                "quran_loader.verify_mismatch",
                surah=surah,
                ayah=ayah,
                canonical_len=len(normalised_canonical),
                input_len=len(normalised_input),
            )
        return match

    @retry(
        retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TransportError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def _fetch_single_verse(
        self, surah: int, ayah: int
    ) -> str | None:
        """Fetch a single verse's Uthmanic text from the API.

        Endpoint: ``GET /v1/ayah/{surah}:{ayah}/quran-uthmani``
        """
        url = f"{self.API_BASE}/ayah/{surah}:{ayah}/quran-uthmani"
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.get(url)
            if response.status_code == 404:
                return None
            response.raise_for_status()

        payload = response.json()
        if payload.get("code") != 200:
            return None
        return payload["data"].get("text")

    @staticmethod
    def _normalise_arabic(text: str) -> str:
        """Normalise Arabic text for comparison.

        * Strips leading/trailing whitespace.
        * Collapses multiple whitespace characters into a single space.
        * Applies NFC Unicode normalisation.
        * Removes common combining marks (tashkeel / diacritics) so that
          fully-vocalised and skeleton texts compare equal.
        """
        # NFC normalisation
        text = unicodedata.normalize("NFC", text.strip())
        # Remove Arabic diacritical marks (U+064B–U+065F, U+0670)
        diacritics = set(range(0x064B, 0x0660)) | {0x0670}
        text = "".join(ch for ch in text if ord(ch) not in diacritics)
        # Collapse whitespace
        return " ".join(text.split())
