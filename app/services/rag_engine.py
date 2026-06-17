"""ChromaDB-backed Retrieval-Augmented Generation engine for Islamic content.

Provides vector similarity search over verified Quran and hadith corpora,
character-level verse verification, hadith grade lookup, and content
deduplication via cosine similarity.
"""

from __future__ import annotations

import asyncio
import functools
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import chromadb
import numpy as np
import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import Settings

logger = structlog.get_logger(__name__)

# Shared thread pool for offloading synchronous ChromaDB I/O.
_chroma_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="chroma")


def _normalize_arabic(text: str) -> str:
    """Normalize Arabic text for comparison.

    Strips diacritics (tashkeel), normalizes Unicode forms, removes
    extraneous whitespace, and maps common letter variants so that
    two visually-identical strings compare equal.
    """
    # Unicode NFC normalization
    text = unicodedata.normalize("NFC", text)

    # Strip Arabic diacritics (U+064B–U+065F, U+0670, U+06D6–U+06ED)
    diacritics = set(range(0x064B, 0x0660)) | {0x0670} | set(range(0x06D6, 0x06EE))
    text = "".join(ch for ch in text if ord(ch) not in diacritics)

    # Common letter normalization
    replacements: dict[str, str] = {
        "\u0622": "\u0627",  # Alef with madda -> Alef
        "\u0623": "\u0627",  # Alef with hamza above -> Alef
        "\u0625": "\u0627",  # Alef with hamza below -> Alef
        "\u0629": "\u0647",  # Taa marbuta -> Haa
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)

    # Collapse whitespace
    return " ".join(text.split())


class RAGEngine:
    """Vector database interface for Islamic content retrieval.

    Connects to a remote ChromaDB instance and exposes async helpers
    for querying the Quran and hadith collections, verifying source
    material, and detecting duplicate content.

    Parameters
    ----------
    settings : Settings
        Application settings providing ChromaDB host/port and collection
        names.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._loop = asyncio.get_event_loop()

        # Connect to ChromaDB — embedded mode (no server) or client/server
        if settings.chroma_host:
            self._client = chromadb.HttpClient(
                host=settings.chroma_host,
                port=settings.chroma_port,
            )
            logger.info(
                "rag_engine.initialized",
                mode="client/server",
                chroma_host=settings.chroma_host,
                chroma_port=settings.chroma_port,
            )
        else:
            self._client = chromadb.PersistentClient(
                path=settings.chroma_persist_dir,
            )
            logger.info(
                "rag_engine.initialized",
                mode="embedded",
                persist_dir=settings.chroma_persist_dir,
            )

        # Obtain handles to the pre-populated collections
        self._quran_col = self._client.get_or_create_collection(
            name=settings.chroma_quran_collection,
            metadata={"hnsw:space": "cosine"},
        )
        self._hadith_col = self._client.get_or_create_collection(
            name=settings.chroma_hadith_collection,
            metadata={"hnsw:space": "cosine"},
        )

        # Lazy-loaded sentence-transformer model for manual similarity
        self._embedder: Any | None = None

    # ── Internal helpers ─────────────────────────────────────────────────

    def _get_embedder(self) -> Any:
        """Lazily load the sentence-transformer model (CPU-bound init)."""
        if self._embedder is None:
            from sentence_transformers import SentenceTransformer

            self._embedder = SentenceTransformer(
                "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
            )
            logger.info("rag_engine.embedder_loaded")
        return self._embedder

    async def _run_sync(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        """Run a synchronous function in the shared thread-pool executor."""
        loop = asyncio.get_running_loop()
        bound = functools.partial(func, *args, **kwargs)
        return await loop.run_in_executor(_chroma_executor, bound)

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=5),
        reraise=True,
    )
    async def _query_collection(
        self,
        collection: Any,
        query_text: str,
        top_k: int,
    ) -> dict[str, Any]:
        """Query a ChromaDB collection with retry logic.

        Parameters
        ----------
        collection : chromadb.Collection
            The ChromaDB collection to query.
        query_text : str
            Natural-language query text.
        top_k : int
            Number of results to return.

        Returns
        -------
        dict
            Raw ChromaDB query result containing ids, documents,
            metadatas, and distances.
        """
        result = await self._run_sync(
            collection.query,
            query_texts=[query_text],
            n_results=top_k,
            include=["documents", "metadatas", "distances"],
        )
        return result

    # ── Public API ───────────────────────────────────────────────────────

    async def query_quran(self, text: str, top_k: int = 5) -> list[dict]:
        """Retrieve the most relevant Quran verses for a query.

        Parameters
        ----------
        text : str
            Free-text search query (Arabic or English).
        top_k : int
            Maximum number of verses to return.

        Returns
        -------
        list[dict]
            Each dict has keys: ``surah``, ``ayah``, ``arabic_text``,
            ``english_text``, ``similarity_score``.
        """
        log = logger.bind(query=text[:80], top_k=top_k)
        log.debug("rag_engine.query_quran.start")

        raw = await self._query_collection(self._quran_col, text, top_k)

        results: list[dict] = []
        if not raw["ids"] or not raw["ids"][0]:
            log.debug("rag_engine.query_quran.no_results")
            return results

        for idx, doc_id in enumerate(raw["ids"][0]):
            meta = raw["metadatas"][0][idx] if raw["metadatas"] else {}
            document = raw["documents"][0][idx] if raw["documents"] else ""
            distance = raw["distances"][0][idx] if raw["distances"] else 1.0

            # ChromaDB cosine distance ∈ [0, 2]; convert to similarity ∈ [0, 1]
            similarity = 1.0 - (distance / 2.0)

            results.append(
                {
                    "surah": meta.get("surah_number", meta.get("surah", 0)),
                    "ayah": meta.get("ayah_number", meta.get("ayah", 0)),
                    "surah_name": meta.get("surah_name", ""),
                    "arabic_text": meta.get("arabic_text", document),
                    "english_text": meta.get("english_text", ""),
                    "similarity_score": round(similarity, 4),
                    "metadata": meta,
                }
            )

        log.info("rag_engine.query_quran.done", count=len(results))
        return results

    async def query_hadith(self, text: str, top_k: int = 5) -> list[dict]:
        """Retrieve the most relevant hadiths for a query.

        Parameters
        ----------
        text : str
            Free-text search query (Arabic or English).
        top_k : int
            Maximum number of hadiths to return.

        Returns
        -------
        list[dict]
            Each dict has keys: ``collection``, ``number``,
            ``arabic_text``, ``english_text``, ``grade``,
            ``similarity_score``.
        """
        log = logger.bind(query=text[:80], top_k=top_k)
        log.debug("rag_engine.query_hadith.start")

        raw = await self._query_collection(self._hadith_col, text, top_k)

        results: list[dict] = []
        if not raw["ids"] or not raw["ids"][0]:
            log.debug("rag_engine.query_hadith.no_results")
            return results

        for idx, doc_id in enumerate(raw["ids"][0]):
            meta = raw["metadatas"][0][idx] if raw["metadatas"] else {}
            document = raw["documents"][0][idx] if raw["documents"] else ""
            distance = raw["distances"][0][idx] if raw["distances"] else 1.0

            similarity = 1.0 - (distance / 2.0)

            results.append(
                {
                    "collection": meta.get("collection", "unknown"),
                    "number": meta.get("number", 0),
                    "arabic_text": meta.get("arabic_text", document),
                    "english_text": meta.get("english_text", ""),
                    "grade": meta.get("grade", "unknown"),
                    "similarity_score": round(similarity, 4),
                }
            )

        log.info("rag_engine.query_hadith.done", count=len(results))
        return results

    async def query_all(self, text: str, top_k: int = 5) -> dict[str, list[dict]]:
        """Query both Quran and hadith collections concurrently.

        Parameters
        ----------
        text : str
            Free-text search query.
        top_k : int
            Maximum results per collection.

        Returns
        -------
        dict
            ``{"quran": [...], "hadith": [...]}``
        """
        quran_results, hadith_results = await asyncio.gather(
            self.query_quran(text, top_k),
            self.query_hadith(text, top_k),
        )
        return {"quran": quran_results, "hadith": hadith_results}

    async def verify_quran_verse(
        self, surah: int, ayah: int, text: str
    ) -> bool:
        """Character-by-character verification of a Quran verse.

        Retrieves the canonical Uthmanic text from ChromaDB by surah/ayah
        and compares it against the supplied ``text`` after Arabic
        normalization.

        Parameters
        ----------
        surah : int
            Surah number (1–114).
        ayah : int
            Ayah number within the surah.
        text : str
            Arabic text to verify.

        Returns
        -------
        bool
            ``True`` if the normalized text matches the stored verse
            exactly, ``False`` otherwise.
        """
        log = logger.bind(surah=surah, ayah=ayah)
        log.debug("rag_engine.verify_quran_verse.start")

        # Look up by deterministic ID first since it is 100% reliable
        verse_id = f"quran_{surah:03d}_{ayah:03d}"
        try:
            result = await self._run_sync(
                self._quran_col.get,
                ids=[verse_id],
                include=["metadatas", "documents"],
            )
        except Exception:
            result = {"ids": []}

        # Fallback to metadata filter if ID lookup failed
        if not result.get("ids"):
            try:
                result = await self._run_sync(
                    self._quran_col.get,
                    where={"$and": [{"surah_number": surah}, {"ayah_number": ayah}]},
                    include=["metadatas", "documents"],
                )
            except Exception:
                result = {"ids": []}

        if not result.get("ids"):
            log.warning("rag_engine.verify_quran_verse.not_found")
            return False

        # Extract canonical text — prefer metadata field, fall back to
        # the stored document.
        meta = result["metadatas"][0] if result["metadatas"] else {}
        canonical = meta.get(
            "arabic_text",
            result["documents"][0] if result["documents"] else "",
        )

        if not canonical:
            log.warning("rag_engine.verify_quran_verse.empty_canonical")
            return False

        if not text:
            # Only checking for existence
            return True

        normalised_canonical = _normalize_arabic(canonical)
        normalised_input = _normalize_arabic(text)

        match = normalised_canonical == normalised_input
        log.info(
            "rag_engine.verify_quran_verse.result",
            match=match,
            canonical_len=len(normalised_canonical),
            input_len=len(normalised_input),
        )
        return match

    @staticmethod
    def _normalize_hadith_collection(collection: str) -> str:
        """Map common collection name variants to canonical database keys."""
        name_clean = collection.lower().replace(" ", "_").replace("-", "_").replace("'", "").replace("`", "")
        if "bukhari" in name_clean:
            return "bukhari"
        if "muslim" in name_clean:
            return "muslim"
        if "dawud" in name_clean:
            return "abudawud"
        if "tirmidhi" in name_clean:
            return "tirmidhi"
        if "nasai" in name_clean:
            return "nasai"
        if "majah" in name_clean:
            return "ibnmajah"
        return name_clean

    async def get_hadith_grade(
        self, collection: str, number: int
    ) -> str | None:
        """Look up the verified grade of a specific hadith.

        Parameters
        ----------
        collection : str
            Hadith collection name, e.g. ``"sahih_bukhari"``.
        number : int
            Hadith number within the collection.

        Returns
        -------
        str or None
            Grade string (``"sahih"``, ``"hasan"``, ``"daif"``, etc.)
            or ``None`` if the hadith is not found.
        """
        norm_collection = self._normalize_hadith_collection(collection)
        log = logger.bind(collection=collection, norm_collection=norm_collection, number=number)
        log.debug("rag_engine.get_hadith_grade.start")

        # Look up by deterministic ID first since it is 100% reliable
        hadith_id = f"hadith_{norm_collection}_{number:05d}"
        try:
            result = await self._run_sync(
                self._hadith_col.get,
                ids=[hadith_id],
                include=["metadatas"],
            )
        except Exception:
            result = {"ids": []}

        # Fallback to metadata filter if ID lookup failed
        if not result.get("ids"):
            try:
                result = await self._run_sync(
                    self._hadith_col.get,
                    where={
                        "$and": [
                            {"collection": norm_collection},
                            {"number": number},
                        ]
                    },
                    include=["metadatas"],
                )
            except Exception:
                result = {"ids": []}

        if not result.get("ids"):
            log.warning("rag_engine.get_hadith_grade.not_found")
            return None

        meta = result["metadatas"][0] if result["metadatas"] else {}
        grade = meta.get("grade")
        log.info("rag_engine.get_hadith_grade.found", grade=grade)
        return grade

    async def compute_similarity(self, text1: str, text2: str) -> float:
        """Compute cosine similarity between two texts.

        Uses a multilingual sentence-transformer model to embed both
        texts and returns the cosine similarity ∈ [-1, 1].

        Parameters
        ----------
        text1, text2 : str
            The two texts to compare.

        Returns
        -------
        float
            Cosine similarity score.
        """

        def _encode_and_compare() -> float:
            model = self._get_embedder()
            embeddings = model.encode([text1, text2], normalize_embeddings=True)
            # Dot product of L2-normalised vectors == cosine similarity
            sim: float = float(np.dot(embeddings[0], embeddings[1]))
            return sim

        similarity = await self._run_sync(_encode_and_compare)
        logger.debug(
            "rag_engine.compute_similarity",
            text1=text1[:60],
            text2=text2[:60],
            similarity=round(similarity, 4),
        )
        return round(similarity, 4)

    async def check_duplicate(
        self, text: str, threshold: float = 0.85
    ) -> bool:
        """Check if text is too similar to existing published content.

        Queries both the Quran and hadith collections and checks if any
        stored document exceeds the similarity ``threshold``.

        Parameters
        ----------
        text : str
            The candidate text to check.
        threshold : float
            Cosine similarity threshold above which content is
            considered a duplicate.  Defaults to 0.85.

        Returns
        -------
        bool
            ``True`` if a near-duplicate was found, ``False`` otherwise.
        """
        log = logger.bind(text=text[:60], threshold=threshold)
        log.debug("rag_engine.check_duplicate.start")

        # Query both collections for the closest match
        quran_results, hadith_results = await asyncio.gather(
            self.query_quran(text, top_k=1),
            self.query_hadith(text, top_k=1),
        )

        max_sim = 0.0
        for result in (*quran_results, *hadith_results):
            sim = result.get("similarity_score", 0.0)
            if sim > max_sim:
                max_sim = sim

        is_duplicate = max_sim >= threshold
        log.info(
            "rag_engine.check_duplicate.result",
            max_similarity=max_sim,
            is_duplicate=is_duplicate,
        )
        return is_duplicate
