"""LLM-powered content generation with RAG grounding for Islamic content.

Uses OpenAI's structured output (``response_format``) together with
retrieved Quran/hadith context from ChromaDB to produce verified,
citation-rich Islamic posts for Instagram.
"""

from __future__ import annotations

import json
from typing import Any, Literal

import openai
import structlog
from pydantic import BaseModel, Field, field_validator
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import Settings
from app.services.rag_engine import RAGEngine

logger = structlog.get_logger(__name__)


# ── Structured output models ────────────────────────────────────────────────


class IslamicPostOutput(BaseModel):
    """Structured response expected from the LLM for a single post."""

    arabic_text: str = Field(
        ..., description="Primary Arabic text (Uthmanic script for Quran)"
    )
    english_text: str = Field(
        ..., description="English translation or explanation"
    )
    source_ref: str = Field(
        ...,
        description="Full source citation, e.g. 'Sahih al-Bukhari 6018'",
    )
    hadith_grade: str | None = Field(
        None,
        description="Hadith grading: sahih, hasan, or daif. None for Quran.",
    )
    caption_arabic: str = Field(
        ...,
        description="A beautiful Arabic-only reflection/caption paragraph using Arabic letters (حروف عربية). Must be pure Arabic, no English at all.",
    )
    caption_english: str = Field(
        ...,
        description="A beautiful English reflection/caption paragraph. Must be pure English, no Arabic at all.",
    )
    hashtags_arabic: list[str] = Field(
        ..., description="Relevant Islamic hashtags in Arabic script (e.g. قرآن, إسلام, تقوى, إيمان, ذكر_الله). Without leading #."
    )
    hashtags_english: list[str] = Field(
        ..., description="Relevant Islamic hashtags in English (e.g. quran, islam, faith, muslimreminder, islamicquotes). Without leading #."
    )

    @field_validator("hashtags_arabic", "hashtags_english", mode="before")
    @classmethod
    def parse_hashtags(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            return [h.strip().lstrip('#') for h in v.replace(',', ' ').split() if h.strip()]
        return v

    content_category: str = Field(
        ..., description="Category such as hadith, quran_verse, dua, etc."
    )
    visual_theme: Literal[
        "dark nature",
        "blue nature",
        "stars night sky",
        "dark forest night",
        "dark ocean waves",
        "dark mountains night",
        "blue forest mist",
        "night rain",
        "deep space nebula",
        "dark sky clouds",
        "northern lights aurora",
        "blue waterfall night",
    ] = Field(
        "dark nature",
        description="Choose the visual theme for the background video from this list of pre-approved nature scenes.",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Self-assessed confidence 0-1 on accuracy of the post",
    )


class CarouselSlide(BaseModel):
    """A single slide in a carousel post."""

    arabic_text: str
    english_text: str
    source_ref: str = ""
    slide_title: str = ""


class CarouselPostOutput(BaseModel):
    """Structured response for a carousel (multi-slide) post."""

    slides: list[CarouselSlide] = Field(
        ..., min_length=2, max_length=10
    )
    caption_arabic: str = Field(
        ...,
        description="A beautiful Arabic-only reflection/caption paragraph using Arabic letters (حروف عربية). Must be pure Arabic, no English at all.",
    )
    caption_english: str = Field(
        ...,
        description="A beautiful English reflection/caption paragraph. Must be pure English, no Arabic at all.",
    )
    hashtags_arabic: list[str] = Field(
        ..., description="Relevant Islamic hashtags in Arabic script. Without leading #."
    )
    hashtags_english: list[str] = Field(
        ..., description="Relevant Islamic hashtags in English. Without leading #."
    )

    @field_validator("hashtags_arabic", "hashtags_english", mode="before")
    @classmethod
    def parse_hashtags(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            return [h.strip().lstrip('#') for h in v.replace(',', ' ').split() if h.strip()]
        return v

    source_ref: str
    hadith_grade: str | None = None
    content_category: str
    confidence: float = Field(..., ge=0.0, le=1.0)


class ReelScriptOutput(BaseModel):
    """Structured response for a reel/short-form video."""

    narration_segments: list[str] = Field(
        ...,
        description="Ordered narration segments for text-to-speech",
    )
    arabic_text: str
    english_text: str
    source_ref: str
    hadith_grade: str | None = None
    caption_arabic: str = Field(
        ...,
        description="A beautiful Arabic-only reflection/caption paragraph using Arabic letters (حروف عربية). Must be pure Arabic, no English at all.",
    )
    caption_english: str = Field(
        ...,
        description="A beautiful English reflection/caption paragraph. Must be pure English, no Arabic at all.",
    )
    hashtags_arabic: list[str] = Field(
        ..., description="Relevant Islamic hashtags in Arabic script. Without leading #."
    )
    hashtags_english: list[str] = Field(
        ..., description="Relevant Islamic hashtags in English. Without leading #."
    )

    @field_validator("hashtags_arabic", "hashtags_english", mode="before")
    @classmethod
    def parse_hashtags(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            return [h.strip().lstrip('#') for h in v.replace(',', ' ').split() if h.strip()]
        return v

    on_screen_text: list[str] = Field(
        ...,
        description="Short on-screen text overlays matching narration",
    )
    content_category: str
    visual_theme: Literal[
        "dark nature",
        "blue nature",
        "stars night sky",
        "dark forest night",
        "dark ocean waves",
        "dark mountains night",
        "blue forest mist",
        "night rain",
        "deep space nebula",
        "dark sky clouds",
        "northern lights aurora",
        "blue waterfall night",
    ] = Field(
        "dark nature",
        description="Choose the visual theme for the background video from this list of pre-approved nature scenes.",
    )
    confidence: float = Field(..., ge=0.0, le=1.0)


# ── System prompt ────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are an expert Islamic content creator for Instagram.  Your role is to
produce accurate, beautifully-written posts that educate and inspire.

STRICT RULES — violating any of these is unacceptable:

1. **Greeting**: Every caption MUST begin with "As-Salamu Alaykum" (or its
   full form "As-Salamu Alaykum wa Rahmatullahi wa Barakatuh").

2. **Never invent hadith**: You may ONLY cite hadiths that appear in the
   CONTEXT block below.  If no relevant hadith is provided, say
   "No relevant hadith found in the verified corpus" and do NOT fabricate.

3. **Source citation**: ALWAYS cite the full source — book name, chapter
   (if available), and hadith number.  Example: "Sahih al-Bukhari 6018".

4. **Hadith grade**: Include the grading (sahih / hasan / da'if) for every
   hadith you cite.  Never omit it.

5. **Quran text**: Use Uthmanic Arabic script exactly as provided.  Do NOT
   retype or paraphrase Quran in Arabic.

6. **Transliteration**: Always include a transliteration line for Arabic
   content so non-Arabic readers can benefit.

7. **No fiqh rulings**: Stick to universally agreed-upon teachings.  Do NOT
   issue legal rulings (fatawa) or recommend a specific madhab.

8. **No madhab mixing**: If a topic touches on fiqh differences,
   acknowledge the diversity of scholarly opinion without taking sides.

9. **Da'if hadiths**: If the only available hadith is graded da'if, you MUST
   clearly label it as da'if in the caption and explain that scholars differ
   on its authenticity.

10. **Confidence**: Honestly self-assess your confidence (0-1) that the
    post is factually accurate and properly sourced.

11. **Tone**: Warm, welcoming, educational.  Avoid divisiveness, political
    commentary, or anything that could cause sectarian discord.

12. **Caption Structure (MANDATORY)**:
    - `caption_arabic`: Write a deep, beautiful reflection paragraph ENTIRELY in Arabic script. No English words at all.
    - `caption_english`: Write a deep, beautiful reflection paragraph ENTIRELY in English. No Arabic words at all.
    - `hashtags_arabic`: Provide many relevant Islamic hashtags in Arabic (e.g. قرآن, إسلام, تقوى, إيمان).
    - `hashtags_english`: Provide many relevant Islamic hashtags in English (e.g. quran, islam, faith, islamicquotes).

OUTPUT FORMAT: Respond with valid JSON matching the requested schema.
"""


# ── Content Generator ────────────────────────────────────────────────────────


class ContentGenerator:
    """Generates Islamic content posts using OpenAI GPT with RAG grounding.

    Parameters
    ----------
    settings : Settings
        Application settings (API keys, model names).
    rag_engine : RAGEngine
        Initialized RAG engine for context retrieval.
    """

    def __init__(self, settings: Settings, rag_engine: RAGEngine) -> None:
        self._settings = settings
        self._rag = rag_engine
        self._client = openai.AsyncOpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url or None,
        )
        self._model = settings.openai_model_primary
        self._model_complex = settings.openai_model_complex

        logger.info(
            "content_generator.initialized",
            model=self._model,
            model_complex=self._model_complex,
            base_url=settings.openai_base_url,
        )

    def _get_system_prompt(self, content_type: str) -> str:
        """Returns the system prompt tailored to the content type."""
        if content_type == "hadith":
            return """\
You are an expert Islamic content creator for Instagram.  Your role is to
produce accurate, beautifully-written posts that educate and inspire.

STRICT RULES — violating any of these is unacceptable:

1. **Greeting**: Every caption MUST begin with "As-Salamu Alaykum" (or its
   full form "As-Salamu Alaykum wa Rahmatullahi wa Barakatuh").

2. **Hadith authenticity**: Since you are generating a Hadith, you MUST retrieve a real, authentic hadith from your training data (knowledge base) regarding the requested topic. You should quote a famous authentic hadith from primary collections (Sahih al-Bukhari, Sahih Muslim, Sunan Abi Dawud, Jami` at-Tirmidhi, Sunan an-Nasa'i, Sunan Ibn Majah). Do NOT return placeholders like "No relevant hadith found". Provide actual authentic Arabic text and English translation.

3. **Source citation**: ALWAYS cite the full source — book name, chapter
   (if available), and hadith number.  Example: "Sahih al-Bukhari 6018".

4. **Hadith grade**: Include the grading (sahih / hasan / da'if) for every
   hadith you cite.  Never omit it.

5. **Transliteration**: Always include a transliteration line for Arabic
   content so non-Arabic readers can benefit.

6. **No fiqh rulings**: Stick to universally agreed-upon teachings.  Do NOT
   issue legal rulings (fatawa) or recommend a specific madhab.

7. **No madhab mixing**: If a topic touches on fiqh differences,
   acknowledge the diversity of scholarly opinion without taking sides.

8. **Da'if hadiths**: If the only available hadith is graded da'if, you MUST
   clearly label it as da'if in the caption and explain that scholars differ
   on its authenticity.

9. **Confidence**: Honestly self-assess your confidence (0-1) that the
   post is factually accurate and properly sourced.

10. **Tone**: Warm, welcoming, educational.  Avoid divisiveness, political
    commentary, or anything that could cause sectarian discord.

11. **Caption Structure (MANDATORY)**:
    - `caption_arabic`: Write a deep, beautiful reflection paragraph ENTIRELY in Arabic script. No English words at all.
    - `caption_english`: Write a deep, beautiful reflection paragraph ENTIRELY in English. No Arabic words at all.
    - `hashtags_arabic`: Provide many relevant Islamic hashtags in Arabic (e.g. قرآن, إسلام, تقوى, إيمان).
    - `hashtags_english`: Provide many relevant Islamic hashtags in English (e.g. quran, islam, faith, islamicquotes).

OUTPUT FORMAT: Respond with valid JSON matching the requested schema.
"""
        elif content_type == "dua":
            return """\
You are an expert Islamic content creator for Instagram.  Your role is to
produce accurate, beautifully-written posts that educate and inspire.

STRICT RULES — violating any of these is unacceptable:

1. **Greeting**: Every caption MUST begin with "As-Salamu Alaykum" (or its
   full form "As-Salamu Alaykum wa Rahmatullahi wa Barakatuh").

2. **Dua authenticity**: Since you are generating a Dua (supplication from people to Allah), you MUST retrieve or write a beautiful, authentic supplication from your knowledge base (e.g. from the Holy Quran, authentic Hadiths, or general beautiful supplications). It must be phrased as a supplication to Allah (asking for mercy, guidance, forgiveness, etc.). Do NOT return placeholders. Provide actual Arabic text and English translation.

3. **Source citation**: If the dua is from the Quran or Hadith, cite the source. Otherwise, cite it as "Supplication" or general dua.

4. **Transliteration**: Always include a transliteration line for Arabic
   content so non-Arabic readers can benefit.

5. **No fiqh rulings**: Stick to universally agreed-upon teachings.  Do NOT
   issue legal rulings (fatawa) or recommend a specific madhab.

6. **Confidence**: Honestly self-assess your confidence (0-1) that the
   post is factually accurate and properly sourced.

7. **Tone**: Warm, welcoming, educational.  Avoid divisiveness, political
   commentary, or anything that could cause sectarian discord.

8. **Caption Structure (MANDATORY)**:
    - `caption_arabic`: Write a deep, beautiful reflection paragraph ENTIRELY in Arabic script. No English words at all.
    - `caption_english`: Write a deep, beautiful reflection paragraph ENTIRELY in English. No Arabic words at all.
    - `hashtags_arabic`: Provide many relevant Islamic hashtags in Arabic (e.g. قرآن, إسلام, تقوى, إيمان).
    - `hashtags_english`: Provide many relevant Islamic hashtags in English (e.g. quran, islam, faith, islamicquotes).

OUTPUT FORMAT: Respond with valid JSON matching the requested schema.
"""
        else:
            return _SYSTEM_PROMPT


    # ── Context building ────────────────────────────────────────────────

    async def _build_context(
        self, content_type: str, topic_name: str
    ) -> str:
        """Retrieve and format RAG context for the generation prompt.

        Queries both Quran and hadith collections and formats the results
        into a human-readable context block the LLM can reference.

        Parameters
        ----------
        content_type : str
            E.g. ``"hadith"``, ``"quran_verse"``, ``"dua"``.
        topic_name : str
            The topic the post should be about.

        Returns
        -------
        str
            Formatted context string.
        """
        query = f"{content_type}: {topic_name}"
        if content_type == "hadith":
            # For hadith posts, retrieve ONLY hadith context
            results = {"quran": [], "hadith": await self._rag.query_hadith(query, top_k=5)}
        elif content_type == "quran_verse":
            # For Quran posts, retrieve ONLY Quran context
            results = {"quran": await self._rag.query_quran(query, top_k=5), "hadith": []}
        else:
            # For dua and other posts, retrieve both
            results = await self._rag.query_all(query, top_k=5)

        sections: list[str] = []

        # Quran context
        if results["quran"]:
            lines = ["=== QURAN VERSES ==="]
            for v in results["quran"]:
                lines.append(
                    f"Surah {v['surah']}, Ayah {v['ayah']} "
                    f"(similarity {v['similarity_score']:.2f}):\n"
                    f"  Arabic: {v['arabic_text']}\n"
                    f"  English: {v['english_text']}"
                )
            sections.append("\n\n".join(lines))

        # Hadith context
        if results["hadith"]:
            lines = ["=== HADITH ==="]
            for h in results["hadith"]:
                lines.append(
                    f"{h['collection']} #{h['number']} "
                    f"[Grade: {h['grade']}] "
                    f"(similarity {h['similarity_score']:.2f}):\n"
                    f"  Arabic: {h['arabic_text']}\n"
                    f"  English: {h['english_text']}"
                )
            sections.append("\n\n".join(lines))

        if not sections:
            return (
                "=== NO CONTEXT FOUND ===\n"
                "No relevant Quran verses or hadiths were found in the "
                "verified corpus for this query.  Do NOT invent sources."
            )

        return "\n\n".join(sections)

    # ── Prompt building ─────────────────────────────────────────────────

    def _build_user_prompt(
        self,
        content_type: str,
        topic_name: str,
        media_format: str,
        context: str,
        exclude_verses: list[str] | None = None,
    ) -> str:
        """Construct the user-facing prompt for the LLM.

        Parameters
        ----------
        content_type : str
            Category of Islamic content.
        topic_name : str
            Topic for the post.
        media_format : str
            ``"quote_card"``, ``"carousel"``, or ``"reel"``.
        context : str
            Retrieved RAG context.

        Returns
        -------
        str
            Fully assembled user prompt.
        """
        format_guidance: dict[str, str] = {
            "quote_card": (
                "Generate a single, visually appealing quote card post.  "
                "The Arabic text should be concise enough to fit on a single "
                "image with a beautiful background."
            ),
            "carousel": (
                "Generate a carousel (multi-slide) post with 3-5 slides.  "
                "Each slide should have a clear title, Arabic text, and "
                "English explanation.  The first slide should be an "
                "attention-grabbing introduction; the last slide should be "
                "a call-to-action or dua."
            ),
            "reel": (
                "Generate a short-form video script (30-60 seconds).  "
                "Provide narration segments that will be spoken aloud via "
                "text-to-speech, plus short on-screen text overlays.  "
                "Open with Bismillah and a warm greeting."
            ),
        }

        type_guidance = ""
        if content_type == "hadith":
            type_guidance = (
                "CRITICAL REQUIREMENT: Since the CONTENT TYPE is 'hadith', the generated post MUST be an authentic saying or action of "
                "Prophet Muhammad (peace and blessings be upon him) cited from an authentic hadith collection. Set content_category "
                "to 'hadith' in the response."
            )
        elif content_type == "dua":
            type_guidance = (
                "CRITICAL REQUIREMENT: Since the CONTENT TYPE is 'dua', the generated post MUST be a supplication (dua) from "
                "people to Allah (e.g. asking Allah for forgiveness, guidance, mercy, starting with 'O Allah', 'Allahumma', "
                "'Rabbana', or similar supplication phrasing). Ensure that the Arabic and English text is formatted as a "
                "direct prayer/supplication. Set content_category to 'dua' in the response."
            )
        elif content_type == "quran_verse":
            type_guidance = (
                "CRITICAL REQUIREMENT: Since the CONTENT TYPE is 'quran_verse', the generated post MUST be a verse from the Holy Quran "
                "cited from the CONTEXT. Set content_category to 'quran_verse' in the response."
            )

        prompt = (
            f"Create an Instagram {media_format} post about the following "
            f"Islamic topic.\n\n"
            f"CONTENT TYPE: {content_type}\n"
            f"TOPIC: {topic_name}\n\n"
            f"FORMAT INSTRUCTIONS:\n{format_guidance.get(media_format, format_guidance['quote_card'])}\n\n"
            f"{type_guidance}\n\n"
        )
        
        if exclude_verses:
            prompt += (
                f"STRICT EXCLUSION: You MUST NOT select or cite any of the following recently used verses under any circumstances: "
                f"{', '.join(exclude_verses)}. Pick a completely different verse that is NOT in this list.\n\n"
            )

        if content_type in ("hadith", "dua"):
            prompt += (
                f"CONTEXT REFERENCE (you can use this if helpful, but you are trusted to provide an authentic hadith/dua from your own knowledge base):\n{context}\n\n"
                "Provide a complete, authentic post. Ensure the Arabic and English texts are fully populated."
            )
        else:
            prompt += (
                f"VERIFIED CONTEXT (use ONLY these sources):\n{context}\n\n"
                "Remember: cite sources exactly as they appear above.  "
                "Do NOT invent or paraphrase hadith text."
            )
        return prompt

    # ── LLM call ────────────────────────────────────────────────────────

    @retry(
        retry=retry_if_exception_type(
            (openai.APIConnectionError, openai.RateLimitError, openai.APITimeoutError)
        ),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    async def _call_llm(
        self,
        user_prompt: str,
        response_model: type[BaseModel],
        *,
        use_complex: bool = False,
        system_prompt: str | None = None,
    ) -> BaseModel:
        """Call the LLM API with JSON mode (Groq/OpenAI compatible).

        Parameters
        ----------
        user_prompt : str
            The user prompt.
        response_model : type[BaseModel]
            Pydantic model describing the desired JSON schema.
        use_complex : bool
            If ``True``, use the more capable model.
        system_prompt : str | None
            Custom system prompt to override default.

        Returns
        -------
        BaseModel
            Parsed structured output.
        """
        model = self._model_complex if use_complex else self._model

        log = logger.bind(model=model, schema=response_model.__name__)
        log.debug("content_generator.llm_call.start")

        # Build the JSON schema instruction for the model
        schema_json = json.dumps(
            response_model.model_json_schema(), indent=2, ensure_ascii=False
        )
        sys_prompt = system_prompt or _SYSTEM_PROMPT
        system_with_schema = (
            f"{sys_prompt}\n\n"
            f"You MUST respond with valid JSON matching this schema:\n"
            f"```json\n{schema_json}\n```\n\n"
            f"Return ONLY the JSON object, no markdown fences or extra text."
        )

        response = await self._client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_with_schema},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.7,
            max_tokens=2000,
        )

        raw_text = response.choices[0].message.content or "{}"

        # Strip markdown code fences if present
        raw_text = raw_text.strip()
        if raw_text.startswith("```"):
            lines = raw_text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            raw_text = "\n".join(lines)

        try:
            parsed = response_model.model_validate_json(raw_text)
        except Exception:
            log.warning(
                "content_generator.llm_call.parse_fallback",
                raw_length=len(raw_text),
            )
            # Try parsing as dict first
            data = json.loads(raw_text)
            parsed = response_model.model_validate(data)

        log.info("content_generator.llm_call.done")
        return parsed

    # ── Public API ───────────────────────────────────────────────────────

    async def generate(
        self,
        content_type: str,
        topic_name: str,
        media_format: str = "quote_card",
        exclude_verses: list[str] | None = None,
    ) -> dict[str, Any]:
        """Generate a single Islamic content post.

        End-to-end pipeline:
        1. Retrieve relevant sources from the RAG engine.
        2. Build a structured prompt with the retrieved context.
        3. Call GPT with structured output.
        4. Post-process and return a plain dict ready for persistence.

        Parameters
        ----------
        content_type : str
            E.g. ``"hadith"``, ``"quran_verse"``, ``"dua"``.
        topic_name : str
            Human-readable topic description.
        media_format : str
            ``"quote_card"`` (default), ``"carousel"``, or ``"reel"``.

        Returns
        -------
        dict
            Keys depend on ``media_format``:

            *quote_card*
                ``arabic_text``, ``english_text``, ``source_ref``,
                ``hadith_grade``, ``caption``, ``hashtags``,
                ``content_category``, ``confidence``

            *carousel*
                ``slides`` (list of dicts), ``caption``, ``hashtags``,
                ``source_ref``, ``hadith_grade``, ``content_category``,
                ``confidence``

            *reel*
                ``narration_segments``, ``on_screen_text``,
                ``arabic_text``, ``english_text``, ``source_ref``,
                ``hadith_grade``, ``caption``, ``hashtags``,
                ``content_category``, ``confidence``
        """
        log = logger.bind(
            content_type=content_type,
            topic=topic_name,
            media_format=media_format,
        )
        log.info("content_generator.generate.start")

        # 1. Retrieve RAG context
        context = await self._build_context(content_type, topic_name)
        log.debug("content_generator.generate.context_ready", length=len(context))

        # 2. Build user prompt
        user_prompt = self._build_user_prompt(
            content_type, topic_name, media_format, context, exclude_verses=exclude_verses
        )

        # 3. Select response schema based on media format
        schema_map: dict[str, type[BaseModel]] = {
            "quote_card": IslamicPostOutput,
            "carousel": CarouselPostOutput,
            "reel": ReelScriptOutput,
        }
        response_model = schema_map.get(media_format, IslamicPostOutput)

        # Use the complex model for carousels (more structured reasoning)
        use_complex = media_format == "carousel"

        # 4. Call LLM
        sys_prompt = self._get_system_prompt(content_type)
        parsed = await self._call_llm(
            user_prompt, response_model, use_complex=use_complex, system_prompt=sys_prompt
        )

        result = parsed.model_dump()
        result = self._post_process_result(result, media_format)

        log.info(
            "content_generator.generate.done",
            confidence=result.get("confidence"),
            source_ref=result.get("source_ref", ""),
        )
        return result

    def _post_process_result(self, result: dict[str, Any], media_format: str) -> dict[str, Any]:
        """Format caption and hashtags exactly according to mandatory rules:
        1. Arabic reflection
        2. Arabic hashtags
        3. English reflection
        4. English hashtags
        """
        # Ensure we have clean lists of hashtags
        hashtags_arabic = result.get("hashtags_arabic", [])
        if isinstance(hashtags_arabic, str):
            hashtags_arabic = [h.strip().lstrip('#') for h in hashtags_arabic.replace(',', ' ').split() if h.strip()]
        
        hashtags_english = result.get("hashtags_english", [])
        if isinstance(hashtags_english, str):
            hashtags_english = [h.strip().lstrip('#') for h in hashtags_english.replace(',', ' ').split() if h.strip()]

        # Clean individual tags
        hashtags_arabic = [tag.lstrip("#").strip() for tag in hashtags_arabic if tag.strip()]
        hashtags_english = [tag.lstrip("#").strip() for tag in hashtags_english if tag.strip()]

        # Save cleaned lists back to result
        result["hashtags_arabic"] = hashtags_arabic
        result["hashtags_english"] = hashtags_english

        # Combine them into a unified list of tags
        result["hashtags"] = hashtags_arabic + hashtags_english

        # Format the caption
        caption_arabic = result.get("caption_arabic", "").strip()
        caption_english = result.get("caption_english", "").strip()

        ar_tags = " ".join(f"#{t}" for t in hashtags_arabic)
        en_tags = " ".join(f"#{t}" for t in hashtags_english)

        caption_parts = []
        if caption_arabic:
            caption_parts.append(caption_arabic)
        if ar_tags:
            caption_parts.append(ar_tags)
        if caption_english:
            caption_parts.append(caption_english)
        if en_tags:
            caption_parts.append(en_tags)

        result["caption"] = "\n\n".join(caption_parts)
        result["media_format"] = media_format
        return result

    async def generate_with_custom_prompt(
        self,
        custom_prompt: str,
        media_format: str = "quote_card",
        content_type: str = "general",
    ) -> dict[str, Any]:
        """Generate content with a fully custom user prompt.

        Useful for admin-triggered generation where the topic/type is
        specified free-form.  The RAG context is still retrieved
        automatically based on the prompt text.

        Parameters
        ----------
        custom_prompt : str
            Free-form prompt describing the desired content.
        media_format : str
            Target media format.
        content_type : str
            The type of content being generated.

        Returns
        -------
        dict
            Same structure as :meth:`generate`.
        """
        log = logger.bind(media_format=media_format)
        log.info("content_generator.generate_custom.start")

        # Retrieve RAG context from the custom prompt itself
        context = await self._build_context(content_type, custom_prompt)

        schema_map: dict[str, type[BaseModel]] = {
            "quote_card": IslamicPostOutput,
            "carousel": CarouselPostOutput,
            "reel": ReelScriptOutput,
        }
        response_model = schema_map.get(media_format, IslamicPostOutput)

        full_prompt = (
            f"{custom_prompt}\n\n"
            f"VERIFIED CONTEXT (use ONLY these sources):\n{context}\n\n"
            "Remember: cite sources exactly as they appear above.  "
            "Do NOT invent or paraphrase hadith text."
        )

        sys_prompt = self._get_system_prompt(content_type)
        parsed = await self._call_llm(full_prompt, response_model, system_prompt=sys_prompt)
        result = parsed.model_dump()
        result = self._post_process_result(result, media_format)

        log.info(
            "content_generator.generate_custom.done",
            confidence=result.get("confidence"),
        )
        return result
