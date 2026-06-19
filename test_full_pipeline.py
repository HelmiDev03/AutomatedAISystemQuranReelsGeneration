"""Full end-to-end test of the content generation pipeline.

Run with: python test_full_pipeline.py

Tests everything EXCEPT Instagram posting:
1. Seed topics into database
2. Load sample Quran verses into ChromaDB
3. Generate content with GPT
4. Verify content (5-step pipeline)
5. Render quote card image
6. Show results

Requires: OPENAI_API_KEY in .env
"""

import asyncio
import sys
import os
import time

# Windows fixes
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    # Fix console encoding for Arabic text
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def print_header(text: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {text}")
    print(f"{'='*60}\n")


def print_step(num: int, text: str) -> None:
    print(f"\n>> Step {num}: {text}")
    print(f"   {'-'*50}")


def print_ok(text: str) -> None:
    print(f"   [OK] {text}")


def print_fail(text: str) -> None:
    print(f"   [FAIL] {text}")


def print_info(text: str) -> None:
    print(f"   [INFO] {text}")


async def run_full_test():
    from app.config import get_settings
    from app.database import async_session, init_db

    settings = get_settings()

    print_header("Islamic Content Automation — Full Pipeline Test")
    print_info(f"Database: {settings.database_url}")
    print_info(f"ChromaDB: embedded mode (persist_dir={settings.chroma_persist_dir})")
    print_info(f"OpenAI key: {'configured ✓' if settings.openai_api_key and settings.openai_api_key != 'sk-your-key-here' else '❌ NOT SET'}")
    print_info(f"Review mode: {settings.review_mode}")

    has_openai = (
        settings.openai_api_key
        and settings.openai_api_key not in ("sk-your-key-here", "your-groq-api-key-here", "")
    )

    # ═══════════════════════════════════════════════════════════════
    # Step 1: Initialize Database
    # ═══════════════════════════════════════════════════════════════
    print_step(1, "Initialize Database")
    try:
        await init_db()
        print_ok("Database tables created (SQLite)")
    except Exception as e:
        print_fail(f"Database init failed: {e}")
        return

    # ═══════════════════════════════════════════════════════════════
    # Step 2: Seed Topics
    # ═══════════════════════════════════════════════════════════════
    print_step(2, "Seed Topics (60+ Islamic content topics)")
    try:
        from app.corpus.seed_topics import seed_topics

        async with async_session() as db:
            count = await seed_topics(db)
            await db.commit()
        print_ok(f"Seeded {count} topics into database")
    except Exception as e:
        print_fail(f"Topic seeding failed: {e}")
        # Continue anyway — may already be seeded

    # List some topics
    try:
        from app.models.topic import ContentTopic
        from sqlalchemy import select, func

        async with async_session() as db:
            count_result = await db.execute(select(func.count()).select_from(ContentTopic))
            total = count_result.scalar() or 0

            sample_result = await db.execute(select(ContentTopic).limit(5))
            samples = sample_result.scalars().all()

        print_ok(f"Total topics in database: {total}")
        for t in samples:
            print(f"     * {t.name} ({t.category.value})")
        if total > 5:
            print(f"     * ... and {total - 5} more")
    except Exception as e:
        print_fail(f"Could not list topics: {e}")

    # ═══════════════════════════════════════════════════════════════
    # Step 3: Load Sample Quran Verses into ChromaDB
    # ═══════════════════════════════════════════════════════════════
    print_step(3, "Load Sample Quran Verses into ChromaDB")
    try:
        from app.services.rag_engine import RAGEngine

        rag = RAGEngine(settings)

        # Check if already loaded
        quran_count = rag._quran_col.count()
        if quran_count > 0:
            print_ok(f"Quran collection already has {quran_count} verses — skipping load")
        else:
            print_info("Loading sample verses (Surah Al-Fatiha + first 20 of Al-Baqarah)...")
            import httpx

            async with httpx.AsyncClient(timeout=30) as client:
                # Fetch Al-Fatiha (Arabic)
                resp_ar = await client.get("https://api.alquran.cloud/v1/surah/1/quran-uthmani")
                resp_en = await client.get("https://api.alquran.cloud/v1/surah/1/en.sahih")

                if resp_ar.status_code == 200 and resp_en.status_code == 200:
                    ar_data = resp_ar.json()["data"]["ayahs"]
                    en_data = resp_en.json()["data"]["ayahs"]

                    ids = []
                    documents = []
                    metadatas = []

                    for ar, en in zip(ar_data, en_data):
                        verse_id = f"quran_1_{ar['numberInSurah']}"
                        doc = f"{ar['text']}\n{en['text']}"
                        meta = {
                            "surah_number": 1,
                            "surah_name": "Al-Fatiha",
                            "ayah_number": ar["numberInSurah"],
                            "arabic_text": ar["text"],
                            "english_text": en["text"],
                            "type": "quran",
                        }
                        ids.append(verse_id)
                        documents.append(doc)
                        metadatas.append(meta)

                    rag._quran_col.add(
                        ids=ids,
                        documents=documents,
                        metadatas=metadatas,
                    )
                    print_ok(f"Loaded {len(ids)} verses from Al-Fatiha")

                # Also load first 10 verses of Al-Baqarah
                resp_ar2 = await client.get("https://api.alquran.cloud/v1/surah/2/quran-uthmani")
                resp_en2 = await client.get("https://api.alquran.cloud/v1/surah/2/en.sahih")

                if resp_ar2.status_code == 200 and resp_en2.status_code == 200:
                    ar_data2 = resp_ar2.json()["data"]["ayahs"][:20]
                    en_data2 = resp_en2.json()["data"]["ayahs"][:20]

                    ids2 = []
                    documents2 = []
                    metadatas2 = []

                    for ar, en in zip(ar_data2, en_data2):
                        verse_id = f"quran_2_{ar['numberInSurah']}"
                        doc = f"{ar['text']}\n{en['text']}"
                        meta = {
                            "surah_number": 2,
                            "surah_name": "Al-Baqarah",
                            "ayah_number": ar["numberInSurah"],
                            "arabic_text": ar["text"],
                            "english_text": en["text"],
                            "type": "quran",
                        }
                        ids2.append(verse_id)
                        documents2.append(doc)
                        metadatas2.append(meta)

                    rag._quran_col.add(
                        ids=ids2,
                        documents=documents2,
                        metadatas=metadatas2,
                    )
                    print_ok(f"Loaded {len(ids2)} verses from Al-Baqarah")

            print_ok(f"Total verses in ChromaDB: {rag._quran_col.count()}")

    except Exception as e:
        print_fail(f"Quran loading failed: {e}")
        import traceback
        traceback.print_exc()

    # ═══════════════════════════════════════════════════════════════
    # Step 4: Test RAG Query
    # ═══════════════════════════════════════════════════════════════
    print_step(4, "Test RAG Query (search Quran by meaning)")
    try:
        rag = RAGEngine(settings)
        results = await rag.query_quran("guidance and mercy from Allah", top_k=3)

        if results:
            print_ok(f"Found {len(results)} relevant verses:")
            for r in results:
                arabic = r.get("metadata", {}).get("arabic_text", "N/A")
                english = r.get("metadata", {}).get("english_text", "N/A")
                surah = r.get("metadata", {}).get("surah_name", "?")
                ayah = r.get("metadata", {}).get("ayah_number", "?")
                print(f"        [{surah}:{ayah}]")
                print(f"        {arabic[:80]}...")
                print(f"        {english[:80]}...")
                print()
        else:
            print_info("No results — this is normal if verses weren't loaded yet")
    except Exception as e:
        print_fail(f"RAG query failed: {e}")

    # ═══════════════════════════════════════════════════════════════
    # Step 5: Generate Content with GPT
    # ═══════════════════════════════════════════════════════════════
    print_step(5, "Generate Content with GPT")
    generated = None

    if not has_openai:
        print_fail("SKIPPED — No OpenAI API key in .env")
        print_info("Add OPENAI_API_KEY=sk-... to your .env file and run again")

        # Use mock data for remaining steps
        generated = {
            "arabic_text": "بِسْمِ اللَّهِ الرَّحْمَٰنِ الرَّحِيمِ",
            "english_text": "In the name of Allah, the Most Gracious, the Most Merciful.",
            "source_ref": "Quran 1:1",
            "hadith_grade": None,
            "caption": "As-Salamu Alaykum 🤍\n\nEvery journey begins with Bismillah.",
            "hashtags": ["islam", "quran", "bismillah", "muslim", "reminder"],
            "content_category": "quran_verse",
            "confidence": 0.95,
            "media_format": "quote_card",
        }
        print_info("Using mock data for remaining steps...")
    else:
        try:
            from app.services.content_generator import ContentGenerator

            rag = RAGEngine(settings)
            generator = ContentGenerator(settings, rag)

            print_info("Calling GPT-5.4 Mini... (may take 5-15 seconds)")
            start = time.time()
            generated = await generator.generate(
                content_type="quran_verse",
                topic_name="Patience and Prayer from Surah Al-Baqarah",
            )
            elapsed = time.time() - start

            print_ok(f"Content generated in {elapsed:.1f}s!")
            print(f"\n        Arabic: {generated['arabic_text'][:100]}")
            print(f"        English: {generated['english_text'][:100]}")
            print(f"        Source: {generated.get('source_ref', 'N/A')}")
            print(f"        Confidence: {generated.get('confidence', 'N/A')}")
            print(f"        Caption: {generated.get('caption', 'N/A')[:80]}")
            print(f"         Hashtags: {', '.join(generated.get('hashtags', []))}")

        except Exception as e:
            print_fail(f"Content generation failed: {e}")
            import traceback
            traceback.print_exc()

    # ═══════════════════════════════════════════════════════════════
    # Step 6: Verify Content (5-step pipeline)
    # ═══════════════════════════════════════════════════════════════
    print_step(6, "Verify Content (5-step pipeline)")
    if generated:
        try:
            from app.services.verifier import ContentVerifier

            rag = RAGEngine(settings)
            verifier = ContentVerifier(settings, rag)

            verification = await verifier.verify({
                "arabic_text": generated["arabic_text"],
                "english_text": generated["english_text"],
                "source_ref": generated.get("source_ref", ""),
                "hadith_grade": generated.get("hadith_grade"),
                "content_type": generated.get("content_category", "quran_verse"),
            })

            if verification.passed:
                print_ok(f"PASSED -- Confidence: {verification.composite_score:.2f}")
            else:
                print_fail(f"FAILED -- Issues: {verification.issues}")

            print(f"     * Source grounding: {'[OK]' if not any('source' in i.lower() for i in verification.issues) else '[FAIL]'}")
            print(f"     * Sensitivity check: {'[OK]' if not any('sensitive' in i.lower() for i in verification.issues) else '[!] Flagged'}")
            print(f"     * Confidence score: {verification.composite_score:.2f}")
            print(f"     * Needs human review: {verification.requires_human_review}")

        except Exception as e:
            print_fail(f"Verification failed: {e}")
            import traceback
            traceback.print_exc()
    else:
        print_fail("SKIPPED — No content to verify")

    # ═══════════════════════════════════════════════════════════════
    # Step 7: Render Quote Card Image
    # ═══════════════════════════════════════════════════════════════
    print_step(7, "Render Quote Card Image")
    if generated:
        try:
            from app.services.media_renderer import MediaRenderer

            renderer = MediaRenderer(settings)

            print_info("Rendering 1080x1080 quote card...")
            file_path = await renderer.render_quote_card(
                arabic_text=generated["arabic_text"],
                english_text=generated["english_text"],
                source_ref=generated.get("source_ref", ""),
                style="dark",
            )

            if file_path and os.path.exists(file_path):
                size_kb = os.path.getsize(file_path) / 1024
                print_ok(f"Quote card saved: {file_path}")
                print_ok(f"File size: {size_kb:.1f} KB")
                print_info("Open this file to see your generated Instagram post!")
            else:
                print_fail("File was not created")

        except Exception as e:
            print_fail(f"Rendering failed: {e}")
            import traceback
            traceback.print_exc()
    else:
        print_fail("SKIPPED — No content to render")

    # ═══════════════════════════════════════════════════════════════
    # Step 8: Test Edge TTS (Arabic voice)
    # ═══════════════════════════════════════════════════════════════
    print_step(8, "Test Edge TTS (Arabic text-to-speech)")
    if generated:
        try:
            import edge_tts

            tts_output = os.path.join("media_output", "test_tts.mp3")
            os.makedirs("media_output", exist_ok=True)

            text_to_speak = generated["arabic_text"][:200]  # First 200 chars
            voice = settings.edge_tts_voice_male

            print_info(f"Generating Arabic speech with voice: {voice}")
            communicate = edge_tts.Communicate(text_to_speak, voice)
            await communicate.save(tts_output)

            if os.path.exists(tts_output):
                size_kb = os.path.getsize(tts_output) / 1024
                print_ok(f"TTS audio saved: {tts_output}")
                print_ok(f"File size: {size_kb:.1f} KB")
                print_info("Play this MP3 to hear the Arabic narration!")
            else:
                print_fail("TTS file was not created")

        except Exception as e:
            print_fail(f"TTS failed: {e}")
    else:
        print_fail("SKIPPED — No content for TTS")

    # ═══════════════════════════════════════════════════════════════
    # Step 9: Render Quran Reel (Intelligent Background + Audio)
    # ═══════════════════════════════════════════════════════════════
    print_step(9, "Render Quran Reel (AI Theme + Reel Generation)")
    if generated:
        try:
            from app.services.background_manager import BackgroundVideoManager
            from app.services.quran_reel_renderer import QuranReelRenderer

            rag = RAGEngine(settings)
            bg_manager = BackgroundVideoManager(settings)
            reel_renderer = QuranReelRenderer(settings, rag)

            # The LLM chose an intelligent visual theme based on the verses!
            visual_theme = generated.get("visual_theme", "nature landscape")
            print_info(f"AI selected visual theme: '{visual_theme}'")
            
            try:
                bg_video_path = await bg_manager.get_background_video(theme=visual_theme)
                print_ok(f"Fetched background video: {bg_video_path}")
            except Exception as e:
                print_fail(f"Failed to fetch background video: {e}")
                sys.exit(1)

            # Extract the Surah and Ayah dynamically from the AI's source reference!
            import re
            source_ref = generated.get("source_ref", "")
            surah_match = re.search(r"Surah\s*(\d+)", source_ref, re.IGNORECASE) or re.search(r"(\d+):", source_ref)
            ayah_match = re.search(r"Ayah\s*(\d+)", source_ref, re.IGNORECASE) or re.search(r":(\d+)", source_ref)
            
            # Default to Surah 2, Ayah 2 if regex fails
            dynamic_surah = int(surah_match.group(1)) if surah_match else 2
            dynamic_ayah = int(ayah_match.group(1)) if ayah_match else 2

            print_info(f"Dynamically parsed from AI -> Surah: {dynamic_surah}, Start Ayah: {dynamic_ayah}")

            print_info("Rendering full Quran reel... (this takes ~30 seconds)")
            reel_path = await reel_renderer.render_quran_reel(
                surah_number=dynamic_surah,
                start_ayah=dynamic_ayah,
                reel_duration=25.0, # Automatically fetches Ayahs until 25s is reached!
                background_video=bg_video_path,
            )
            
            size_kb = os.path.getsize(reel_path) / 1024
            print_ok(f"Quran Reel saved: {reel_path}")
            print_ok(f"File size: {size_kb:.1f} KB")
            print_info("Open this file to see your AI-themed Instagram Reel!")

        except Exception as e:
            print_fail(f"Reel rendering failed: {e}")
            import traceback
            traceback.print_exc()
    else:
        print_fail("SKIPPED — No content to render")

    # ═══════════════════════════════════════════════════════════════
    # Summary
    # ═══════════════════════════════════════════════════════════════
    print_header("Test Complete!")
    print("  Generated files:")
    if os.path.exists("media_output"):
        for f in os.listdir("media_output"):
            fpath = os.path.join("media_output", f)
            size = os.path.getsize(fpath) / 1024
            print(f"       {fpath} ({size:.1f} KB)")

    print(f"\n  Database: automate.db")
    if os.path.exists("automate.db"):
        size = os.path.getsize("automate.db") / 1024
        print(f"    Size: {size:.1f} KB")

    print(f"\n  ChromaDB: {settings.chroma_persist_dir}/")

    if not has_openai:
        print("\n  [!]  To test real AI generation, add your Groq/OpenAI API key:")
        print("     1. Open .env")
        print("     2. Set OPENAI_API_KEY=your-key-here")
        print("     3. Run this script again")

    print("\n  Next steps:")
    print("     1. Check the generated quote card image and Reel in media_output/")
    print("     2. Play the TTS audio file")
    print("     3. Check that the visual_theme matched the video!")
    print("     4. Set up Cloudinary + Instagram when ready to publish")
    print()


if __name__ == "__main__":
    asyncio.run(run_full_test())
