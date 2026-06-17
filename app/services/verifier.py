"""Five-step Islamic content verification pipeline.

Every generated post passes through this pipeline before publication:

1. **Source grounding** — verify all cited sources exist in the corpus.
2. **Hadith grade check** — confirm grades match the verified database.
3. **Quran verification** — character-by-character verse comparison.
4. **Sensitivity check** — flag controversial fiqh / sectarian topics.
5. **Confidence scoring** — compute a composite score from all steps.

Posts scoring below ``settings.confidence_threshold`` (default 0.85) are
held for manual Telegram review.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import structlog
from pydantic import BaseModel, Field

from app.config import Settings
from app.services.rag_engine import RAGEngine

logger = structlog.get_logger(__name__)


# ── Result models ────────────────────────────────────────────────────────────


class StepStatus(str, Enum):
    """Outcome of a single verification step."""

    PASSED = "passed"
    FAILED = "failed"
    WARNING = "warning"
    SKIPPED = "skipped"


class StepResult(BaseModel):
    """Result of one verification step."""

    step_name: str = Field(..., description="Human-readable step name")
    step_number: int = Field(..., ge=1, le=5)
    status: StepStatus
    score: float = Field(
        ..., ge=0.0, le=1.0, description="Step-level score (1.0 = perfect)"
    )
    issues: list[str] = Field(
        default_factory=list,
        description="List of issues found in this step",
    )
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary metadata for debugging",
    )


class VerificationResult(BaseModel):
    """Aggregate result from all five verification steps."""

    passed: bool = Field(
        ..., description="Overall pass/fail based on composite score"
    )
    composite_score: float = Field(
        ..., ge=0.0, le=1.0, description="Weighted composite confidence"
    )
    step_results: list[StepResult] = Field(default_factory=list)
    issues: list[str] = Field(
        default_factory=list,
        description="Consolidated list of every issue from all steps",
    )
    requires_human_review: bool = Field(
        False,
        description="True if the post should be routed to Telegram review",
    )
    review_reasons: list[str] = Field(
        default_factory=list,
        description="Reasons why human review is required",
    )
    verified_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


# ── Sensitivity keyword sets ────────────────────────────────────────────────

# Topics that require human review because they touch on areas of
# scholarly disagreement, political Islam, or potentially divisive
# sectarian subjects.
_SENSITIVE_TOPICS: set[str] = {
    # Inter-madhab disputes
    "hanafi",
    "maliki",
    "shafi'i",
    "shafii",
    "hanbali",
    "madhab",
    "madhhab",
    "ikhtilaf",
    # Sectarian / political
    "shia",
    "sunni",
    "wahabi",
    "wahhabi",
    "salafi",
    "sufi",
    "deobandi",
    "barelvi",
    "ikhwan",
    "caliphate",
    "khilafah",
    # Takfir / extremism
    "takfir",
    "kufr",
    "kafir",
    "murtad",
    "apostate",
    "apostasy",
    "riddah",
    "jihad",
    # Controversial fiqh
    "music",
    "images",
    "taswir",
    "niqab",
    "hijab ruling",
    "interest",
    "riba",
    "insurance",
    "cryptocurrency",
    "democracy",
    "voting",
    "gender mixing",
    "ikhtilat",
    "bid'ah",
    "bidah",
    "tawassul",
    "istiwa",
    "mawlid",
    "milad",
}

_BLOCKED_TOPICS: set[str] = {
    # Fabricated / Mawdu' indicators
    "mawdu",
    "fabricated hadith",
    # Explicit takfir
    "takfir of muslims",
    "declaring kafir",
    # Calls to violence
    "kill",
    "attack",
    "bomb",
    "terror",
}

# Regex patterns for source reference extraction
_SOURCE_REF_PATTERN = re.compile(
    r"(Sahih\s+(?:al-)?Bukhari|Sahih\s+Muslim|Sunan\s+(?:Abu\s+Dawud|"
    r"al-Tirmidhi|an-Nasa'i|Ibn\s+Majah)|Musnad\s+Ahmad|"
    r"Muwatta\s+Malik|Riyad\s+al-Salihin|Bulugh\s+al-Maram|"
    r"Jami'\s+al-Tirmidhi|Al-Adab\s+Al-Mufrad)"
    r"[\s,:#]*(\d+)",
    re.IGNORECASE,
)

_QURAN_REF_PATTERN = re.compile(
    r"(?:Surah|Quran|Q\.?)[^\d]*(\d+)[^\d]*(\d+)",
    re.IGNORECASE,
)


# ── Helper: extract references from text ─────────────────────────────────


def _extract_hadith_refs(text: str) -> list[dict[str, Any]]:
    """Parse hadith references from free text.

    Returns
    -------
    list[dict]
        Each dict has ``collection`` (normalised slug) and ``number``.
    """
    _collection_slug_map: dict[str, str] = {
        "sahih al-bukhari": "sahih_bukhari",
        "sahih bukhari": "sahih_bukhari",
        "sahih muslim": "sahih_muslim",
        "sunan abu dawud": "sunan_abu_dawud",
        "sunan al-tirmidhi": "jami_tirmidhi",
        "jami' al-tirmidhi": "jami_tirmidhi",
        "sunan an-nasa'i": "sunan_nasai",
        "sunan ibn majah": "sunan_ibn_majah",
        "musnad ahmad": "musnad_ahmad",
        "muwatta malik": "muwatta_malik",
        "riyad al-salihin": "riyad_salihin",
        "bulugh al-maram": "bulugh_al_maram",
        "al-adab al-mufrad": "adab_al_mufrad",
    }

    refs: list[dict[str, Any]] = []
    for match in _SOURCE_REF_PATTERN.finditer(text):
        raw_name = match.group(1).strip().lower()
        number = int(match.group(2))
        slug = _collection_slug_map.get(raw_name, raw_name.replace(" ", "_"))
        refs.append({"collection": slug, "number": number})
    return refs


def _extract_quran_refs(text: str) -> list[dict[str, int]]:
    """Parse Quran surah:ayah references from free text."""
    refs: list[dict[str, int]] = []
    for match in _QURAN_REF_PATTERN.finditer(text):
        try:
            surah = int(match.group(1))
            ayah = int(match.group(2))
            refs.append({"surah": surah, "ayah": ayah})
        except (ValueError, IndexError):
            continue
    return refs


# ── Content Verifier ─────────────────────────────────────────────────────────


class ContentVerifier:
    """Five-step verification pipeline for generated Islamic content.

    Parameters
    ----------
    settings : Settings
        Application settings (confidence thresholds, etc.).
    rag_engine : RAGEngine
        Initialized RAG engine for corpus lookups.
    """

    # Weights for each step in the composite score computation.
    _STEP_WEIGHTS: dict[int, float] = {
        1: 0.30,  # Source grounding
        2: 0.25,  # Hadith grade
        3: 0.25,  # Quran verification
        4: 0.10,  # Sensitivity
        5: 0.10,  # Confidence self-assessment
    }

    def __init__(self, settings: Settings, rag_engine: RAGEngine) -> None:
        self._settings = settings
        self._rag = rag_engine
        self._threshold = settings.confidence_threshold

        logger.info(
            "content_verifier.initialized",
            confidence_threshold=self._threshold,
        )

    # ── Step 1: Source grounding ─────────────────────────────────────────

    async def step1_source_grounding(self, post_data: dict) -> StepResult:
        """Verify all cited sources exist in the verified corpus.

        Extracts hadith references from ``source_ref``, ``caption``, and
        ``english_text``, then looks each one up in ChromaDB.

        Parameters
        ----------
        post_data : dict
            Generated post data (must contain at least ``source_ref``).

        Returns
        -------
        StepResult
        """
        log = logger.bind(step="source_grounding")
        log.debug("verifier.step1.start")

        issues: list[str] = []
        details: dict[str, Any] = {}

        # Gather text fields to scan for references
        combined_text = " ".join(
            str(post_data.get(field, ""))
            for field in ("source_ref", "caption", "english_text", "arabic_text")
        )

        hadith_refs = _extract_hadith_refs(combined_text)
        quran_refs = _extract_quran_refs(combined_text)
        details["hadith_refs_found"] = len(hadith_refs)
        details["quran_refs_found"] = len(quran_refs)

        if not hadith_refs and not quran_refs:
            # Content with no references at all is suspicious
            content_type = post_data.get("content_category", "")
            if content_type in ("hadith", "quran_verse"):
                issues.append(
                    f"No verifiable source references found for "
                    f"content_type={content_type}"
                )
                log.warning("verifier.step1.no_refs", content_type=content_type)
                return StepResult(
                    step_name="Source Grounding",
                    step_number=1,
                    status=StepStatus.FAILED,
                    score=0.0,
                    issues=issues,
                    details=details,
                )

        verified_count = 0
        total_refs = len(hadith_refs) + len(quran_refs)

        # Check hadith references
        for ref in hadith_refs:
            grade = await self._rag.get_hadith_grade(
                ref["collection"], ref["number"]
            )
            if grade is not None:
                verified_count += 1
                details[f"{ref['collection']}_{ref['number']}"] = "verified"
            else:
                issues.append(
                    f"Hadith not found in corpus: "
                    f"{ref['collection']} #{ref['number']}"
                )
                details[f"{ref['collection']}_{ref['number']}"] = "not_found"

        # Check Quran references (existence only; text verified in step 3)
        for ref in quran_refs:
            found = await self._rag.verify_quran_verse(
                ref["surah"], ref["ayah"], ""
            )
            # We pass empty text here — we just want to know if the verse
            # exists.  Full text matching happens in step 3.
            # verify_quran_verse with empty string will return False, so
            # we do a simple existence check via query instead.
            quran_hits = await self._rag.query_quran(
                f"surah {ref['surah']} ayah {ref['ayah']}", top_k=1
            )
            if quran_hits and quran_hits[0].get("surah") == ref["surah"]:
                verified_count += 1
                details[f"quran_{ref['surah']}:{ref['ayah']}"] = "verified"
            else:
                issues.append(
                    f"Quran verse not found: {ref['surah']}:{ref['ayah']}"
                )
                details[f"quran_{ref['surah']}:{ref['ayah']}"] = "not_found"

        score = verified_count / total_refs if total_refs > 0 else 1.0
        status = (
            StepStatus.PASSED
            if score >= 0.8
            else StepStatus.WARNING if score >= 0.5 else StepStatus.FAILED
        )

        log.info("verifier.step1.done", score=score, issues=len(issues))
        return StepResult(
            step_name="Source Grounding",
            step_number=1,
            status=status,
            score=round(score, 4),
            issues=issues,
            details=details,
        )

    # ── Step 2: Hadith grade check ───────────────────────────────────────

    async def step2_hadith_grade_check(self, post_data: dict) -> StepResult:
        """Verify hadith grades match the verified database.

        Also enforces the Mawdu' (fabricated) block rule: any post citing
        a Mawdu' hadith is automatically failed.

        Parameters
        ----------
        post_data : dict
            Must contain ``source_ref`` and optionally ``hadith_grade``.

        Returns
        -------
        StepResult
        """
        log = logger.bind(step="hadith_grade_check")
        log.debug("verifier.step2.start")

        issues: list[str] = []
        details: dict[str, Any] = {}

        claimed_grade = (post_data.get("hadith_grade") or "").lower().strip()
        content_category = post_data.get("content_category", "")

        # Skip grade check for non-hadith content
        if content_category not in ("hadith",) and not claimed_grade:
            log.debug("verifier.step2.skipped_non_hadith")
            return StepResult(
                step_name="Hadith Grade Check",
                step_number=2,
                status=StepStatus.SKIPPED,
                score=1.0,
                issues=[],
                details={"reason": "Not hadith content"},
            )

        combined_text = " ".join(
            str(post_data.get(f, "")) for f in ("source_ref", "english_text")
        )
        hadith_refs = _extract_hadith_refs(combined_text)

        if not hadith_refs:
            if content_category == "hadith":
                issues.append("Hadith content has no parseable hadith reference")
                return StepResult(
                    step_name="Hadith Grade Check",
                    step_number=2,
                    status=StepStatus.FAILED,
                    score=0.0,
                    issues=issues,
                    details=details,
                )
            return StepResult(
                step_name="Hadith Grade Check",
                step_number=2,
                status=StepStatus.SKIPPED,
                score=1.0,
                issues=[],
                details={"reason": "No hadith references to check"},
            )

        score = 1.0
        for ref in hadith_refs:
            verified_grade = await self._rag.get_hadith_grade(
                ref["collection"], ref["number"]
            )
            ref_label = f"{ref['collection']} #{ref['number']}"

            if verified_grade is None:
                issues.append(f"Grade not found for {ref_label}")
                score -= 0.3
                details[ref_label] = "grade_not_found"
                continue

            details[ref_label] = {
                "verified_grade": verified_grade,
                "claimed_grade": claimed_grade,
            }

            # BLOCK: Mawdu' (fabricated) hadiths must never be published
            if verified_grade.lower() == "mawdu":
                issues.append(
                    f"BLOCKED: {ref_label} is graded Mawdu' (fabricated) — "
                    f"publication is forbidden"
                )
                log.error(
                    "verifier.step2.mawdu_blocked",
                    ref=ref_label,
                    grade=verified_grade,
                )
                return StepResult(
                    step_name="Hadith Grade Check",
                    step_number=2,
                    status=StepStatus.FAILED,
                    score=0.0,
                    issues=issues,
                    details=details,
                )

            # Grade mismatch check
            if claimed_grade and verified_grade.lower() != claimed_grade:
                issues.append(
                    f"Grade mismatch for {ref_label}: "
                    f"claimed '{claimed_grade}', verified '{verified_grade}'"
                )
                score -= 0.4

            # Da'if warning — must be labelled clearly
            if verified_grade.lower() == "daif":
                caption = post_data.get("caption", "")
                daif_labels = {"da'if", "daif", "weak", "ضعيف"}
                if not any(label in caption.lower() for label in daif_labels):
                    issues.append(
                        f"Da'if hadith {ref_label} is not clearly labelled "
                        f"as weak in the caption"
                    )
                    score -= 0.2

        score = max(0.0, min(1.0, score))
        status = (
            StepStatus.PASSED
            if score >= 0.8
            else StepStatus.WARNING if score >= 0.5 else StepStatus.FAILED
        )

        log.info("verifier.step2.done", score=score, issues=len(issues))
        return StepResult(
            step_name="Hadith Grade Check",
            step_number=2,
            status=status,
            score=round(score, 4),
            issues=issues,
            details=details,
        )

    # ── Step 3: Quran verification ───────────────────────────────────────

    async def step3_quran_verification(self, post_data: dict) -> StepResult:
        """Character-by-character Quran verse verification.

        Extracts any Quran reference from the post and compares the
        Arabic text against the canonical Uthmanic text stored in ChromaDB.

        Parameters
        ----------
        post_data : dict
            Must contain ``arabic_text`` and may contain Quran references
            in ``source_ref`` or ``english_text``.

        Returns
        -------
        StepResult
        """
        log = logger.bind(step="quran_verification")
        log.debug("verifier.step3.start")

        issues: list[str] = []
        details: dict[str, Any] = {}

        combined_text = " ".join(
            str(post_data.get(f, ""))
            for f in ("source_ref", "english_text", "caption")
        )
        quran_refs = _extract_quran_refs(combined_text)
        content_category = post_data.get("content_category", "")

        # Skip if not Quran content and no Quran refs
        if not quran_refs and content_category != "quran_verse":
            log.debug("verifier.step3.skipped")
            return StepResult(
                step_name="Quran Verification",
                step_number=3,
                status=StepStatus.SKIPPED,
                score=1.0,
                issues=[],
                details={"reason": "No Quran references to verify"},
            )

        arabic_text = post_data.get("arabic_text", "")

        if not quran_refs and content_category == "quran_verse":
            # Quran content but no parseable reference
            issues.append(
                "Quran verse content has no parseable surah:ayah reference"
            )
            return StepResult(
                step_name="Quran Verification",
                step_number=3,
                status=StepStatus.FAILED,
                score=0.0,
                issues=issues,
                details=details,
            )

        verified_count = 0
        total = len(quran_refs)

        for ref in quran_refs:
            ref_label = f"{ref['surah']}:{ref['ayah']}"
            log.debug("verifier.step3.checking", ref=ref_label)

            match = await self._rag.verify_quran_verse(
                ref["surah"], ref["ayah"], arabic_text
            )

            if match:
                verified_count += 1
                details[ref_label] = "exact_match"
                log.debug("verifier.step3.match", ref=ref_label)
            else:
                # Try a similarity-based fallback — the arabic_text may
                # contain the verse embedded within a longer passage.
                quran_hits = await self._rag.query_quran(arabic_text, top_k=1)
                if quran_hits:
                    best = quran_hits[0]
                    if (
                        best.get("surah") == ref["surah"]
                        and best.get("ayah") == ref["ayah"]
                        and best.get("similarity_score", 0) >= 0.90
                    ):
                        verified_count += 1
                        details[ref_label] = "high_similarity_match"
                        log.debug(
                            "verifier.step3.similarity_match",
                            ref=ref_label,
                            sim=best["similarity_score"],
                        )
                    else:
                        issues.append(
                            f"Quran verse {ref_label}: Arabic text does not "
                            f"match canonical Uthmanic text (best similarity: "
                            f"{best.get('similarity_score', 0):.2f})"
                        )
                        details[ref_label] = {
                            "status": "mismatch",
                            "best_similarity": best.get("similarity_score", 0),
                        }
                else:
                    issues.append(
                        f"Quran verse {ref_label}: could not retrieve "
                        f"canonical text for comparison"
                    )
                    details[ref_label] = "retrieval_failed"

        score = verified_count / total if total > 0 else 1.0
        status = (
            StepStatus.PASSED
            if score >= 0.8
            else StepStatus.WARNING if score >= 0.5 else StepStatus.FAILED
        )

        log.info("verifier.step3.done", score=score, issues=len(issues))
        return StepResult(
            step_name="Quran Verification",
            step_number=3,
            status=status,
            score=round(score, 4),
            issues=issues,
            details=details,
        )

    # ── Step 4: Sensitivity check ────────────────────────────────────────

    async def step4_sensitivity_check(self, post_data: dict) -> StepResult:
        """Check for controversial fiqh topics and sensitive content.

        Scans all text fields for keywords from the sensitivity lists.
        Posts touching blocked topics are automatically failed; posts
        touching sensitive topics are flagged for human review.

        Parameters
        ----------
        post_data : dict
            Full post data dict.

        Returns
        -------
        StepResult
        """
        log = logger.bind(step="sensitivity_check")
        log.debug("verifier.step4.start")

        issues: list[str] = []
        details: dict[str, Any] = {
            "sensitive_matches": [],
            "blocked_matches": [],
        }

        # Build searchable text blob from all relevant fields
        text_fields = (
            "arabic_text",
            "english_text",
            "caption",
            "source_ref",
            "content_category",
        )
        searchable = " ".join(
            str(post_data.get(f, "")) for f in text_fields
        ).lower()

        # Also check carousel slides and reel narration
        if "slides" in post_data:
            for slide in post_data["slides"]:
                if isinstance(slide, dict):
                    searchable += " " + " ".join(
                        str(slide.get(k, ""))
                        for k in ("arabic_text", "english_text", "slide_title")
                    ).lower()
        if "narration_segments" in post_data:
            searchable += " " + " ".join(
                str(s) for s in post_data["narration_segments"]
            ).lower()

        # Check blocked topics first
        for keyword in _BLOCKED_TOPICS:
            if keyword in searchable:
                details["blocked_matches"].append(keyword)
                issues.append(
                    f"BLOCKED topic detected: '{keyword}' — "
                    f"this content cannot be published"
                )

        if details["blocked_matches"]:
            log.error(
                "verifier.step4.blocked",
                keywords=details["blocked_matches"],
            )
            return StepResult(
                step_name="Sensitivity Check",
                step_number=4,
                status=StepStatus.FAILED,
                score=0.0,
                issues=issues,
                details=details,
            )

        # Check sensitive topics (warning, not block)
        for keyword in _SENSITIVE_TOPICS:
            if keyword in searchable:
                details["sensitive_matches"].append(keyword)

        if details["sensitive_matches"]:
            issues.append(
                f"Sensitive topics detected: "
                f"{', '.join(details['sensitive_matches'])}. "
                f"Routing to human review."
            )
            score = 0.5
            status = StepStatus.WARNING
        else:
            score = 1.0
            status = StepStatus.PASSED

        log.info(
            "verifier.step4.done",
            score=score,
            sensitive=len(details["sensitive_matches"]),
        )
        return StepResult(
            step_name="Sensitivity Check",
            step_number=4,
            status=status,
            score=round(score, 4),
            issues=issues,
            details=details,
        )

    # ── Step 5: Confidence scoring ───────────────────────────────────────

    async def step5_confidence_score(
        self,
        post_data: dict,
        step_results: list[StepResult],
    ) -> float:
        """Compute composite confidence score from all prior steps.

        The composite score is a weighted average of each step score,
        combined with the LLM's self-assessed confidence (if present).

        Parameters
        ----------
        post_data : dict
            Must contain optional ``confidence`` key (LLM self-assessment).
        step_results : list[StepResult]
            Results from steps 1–4.

        Returns
        -------
        float
            Final composite confidence ∈ [0, 1].
        """
        log = logger.bind(step="confidence_scoring")
        log.debug("verifier.step5.start")

        llm_confidence = post_data.get("confidence", 0.5)

        # Weighted sum of step scores
        weighted_sum = 0.0
        weight_total = 0.0

        for sr in step_results:
            w = self._STEP_WEIGHTS.get(sr.step_number, 0.1)
            weighted_sum += sr.score * w
            weight_total += w

        # Add the LLM self-assessment with the step-5 weight
        step5_weight = self._STEP_WEIGHTS.get(5, 0.10)
        weighted_sum += llm_confidence * step5_weight
        weight_total += step5_weight

        composite = weighted_sum / weight_total if weight_total > 0 else 0.0
        composite = max(0.0, min(1.0, composite))

        # Apply penalties for any hard failures
        for sr in step_results:
            if sr.status == StepStatus.FAILED:
                # Each failed step applies a 25 % multiplicative penalty
                composite *= 0.75

        composite = round(max(0.0, min(1.0, composite)), 4)

        log.info(
            "verifier.step5.done",
            composite=composite,
            llm_confidence=llm_confidence,
        )
        return composite

    # ── Main orchestrator ────────────────────────────────────────────────

    async def verify(self, post_data: dict) -> VerificationResult:
        """Run the full five-step verification pipeline.

        Parameters
        ----------
        post_data : dict
            Generated post data from ``ContentGenerator.generate()``.
            Expected keys include ``arabic_text``, ``english_text``,
            ``source_ref``, ``hadith_grade``, ``caption``,
            ``content_category``, ``confidence``.

        Returns
        -------
        VerificationResult
            Aggregate result with pass/fail, composite score, all step
            results, and whether human review is needed.
        """
        log = logger.bind(
            content_category=post_data.get("content_category", "unknown"),
            source_ref=post_data.get("source_ref", ""),
        )
        log.info("verifier.verify.start")

        # Execute steps 1–4
        step1 = await self.step1_source_grounding(post_data)
        step2 = await self.step2_hadith_grade_check(post_data)
        step3 = await self.step3_quran_verification(post_data)
        step4 = await self.step4_sensitivity_check(post_data)

        step_results = [step1, step2, step3, step4]

        # Step 5: composite scoring
        composite = await self.step5_confidence_score(post_data, step_results)

        # Build a step-5 result for inclusion in the list
        step5 = StepResult(
            step_name="Confidence Scoring",
            step_number=5,
            status=(
                StepStatus.PASSED
                if composite >= self._threshold
                else StepStatus.FAILED
            ),
            score=composite,
            issues=[],
            details={
                "composite_score": composite,
                "llm_confidence": post_data.get("confidence", 0.5),
                "threshold": self._threshold,
            },
        )
        step_results.append(step5)

        # Consolidate issues
        all_issues: list[str] = []
        for sr in step_results:
            all_issues.extend(sr.issues)

        # Determine human review requirements
        review_reasons: list[str] = []
        requires_review = False

        if composite < self._threshold:
            requires_review = True
            review_reasons.append(
                f"Composite score {composite:.2f} is below threshold "
                f"{self._threshold:.2f}"
            )

        if step4.status == StepStatus.WARNING:
            requires_review = True
            review_reasons.append("Sensitive topics detected")

        if step4.status == StepStatus.FAILED:
            requires_review = True
            review_reasons.append("Blocked topics detected — must not publish")

        if step2.status == StepStatus.FAILED:
            requires_review = True
            review_reasons.append("Hadith grade verification failed")

        if step3.status == StepStatus.FAILED:
            requires_review = True
            review_reasons.append("Quran text verification failed")

        passed = composite >= self._threshold and not any(
            sr.status == StepStatus.FAILED
            for sr in step_results
            if sr.step_number != 5  # step 5 failure = low score, already handled
        )

        result = VerificationResult(
            passed=passed,
            composite_score=composite,
            step_results=step_results,
            issues=all_issues,
            requires_human_review=requires_review,
            review_reasons=review_reasons,
        )

        log.info(
            "verifier.verify.done",
            passed=passed,
            composite=composite,
            issues=len(all_issues),
            requires_review=requires_review,
        )
        return result
