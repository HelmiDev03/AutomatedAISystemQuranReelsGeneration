"""Seed Topics — a curated list of 60+ Islamic content topics spanning all 12
categories, plus an async seeder function that inserts them into the database.

Topics are carefully chosen for accuracy and breadth of Islamic knowledge:
Quran Tafsir, Hadith, Seerah, Fiqh, Du'a, Islamic History, Daily Reminders,
Names of Allah, Akhlaq, Salah, Tawheed, and Quran Recitation.
"""

from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.topic import ContentTopic, TopicCategory

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Seed data — at least 5 topics per category, 60+ total
# ---------------------------------------------------------------------------

SEED_TOPICS: list[dict[str, str]] = [
    # ── Quran Tafsir (8 topics) ───────────────────────────────────────────
    {
        "name": "Surah Al-Fatiha — The Opening",
        "category": "quran_tafsir",
        "description": (
            "Tafsir and reflection on the opening chapter of the Quran, "
            "the essence of all supplication and the foundation of Salah."
        ),
    },
    {
        "name": "Ayat Al-Kursi — The Throne Verse",
        "category": "quran_tafsir",
        "description": (
            "The greatest verse of the Quran (2:255), affirming Allah's "
            "sovereignty, knowledge, and power over all creation."
        ),
    },
    {
        "name": "Last Two Verses of Al-Baqarah",
        "category": "quran_tafsir",
        "description": (
            "Verses 285-286 of Surah Al-Baqarah — protection, supplication, "
            "and the mercy of Allah for the believers."
        ),
    },
    {
        "name": "Surah Al-Mulk — Sovereignty",
        "category": "quran_tafsir",
        "description": (
            "Reflection on Surah Al-Mulk (67), its virtues as a protector "
            "from the punishment of the grave."
        ),
    },
    {
        "name": "Surah Al-Kahf — The Cave",
        "category": "quran_tafsir",
        "description": (
            "The four stories of Surah Al-Kahf: People of the Cave, "
            "the two men and the garden, Musa and Al-Khidr, Dhul-Qarnayn."
        ),
    },
    {
        "name": "Surah Yasin — The Heart of the Quran",
        "category": "quran_tafsir",
        "description": (
            "Tafsir of Surah Yasin (36), known as the heart of the Quran, "
            "discussing resurrection, signs of Allah, and the Hereafter."
        ),
    },
    {
        "name": "Surah Ar-Rahman — The Most Merciful",
        "category": "quran_tafsir",
        "description": (
            "Reflection on Allah's favours enumerated in Surah Ar-Rahman (55): "
            "'So which of the favours of your Lord would you deny?'"
        ),
    },
    {
        "name": "The Last Three Surahs — Al-Ikhlas, Al-Falaq, An-Nas",
        "category": "quran_tafsir",
        "description": (
            "Tafsir and virtues of the three Quls: Tawheed, seeking refuge "
            "from external and internal evil."
        ),
    },
    # ── Hadith (7 topics) ─────────────────────────────────────────────────
    {
        "name": "Hadith on Intentions (Niyyah)",
        "category": "hadith",
        "description": (
            "The famous hadith: 'Actions are judged by intentions' "
            "(Sahih al-Bukhari 1). Foundation of all worship in Islam."
        ),
    },
    {
        "name": "Hadith on Kindness to Parents",
        "category": "hadith",
        "description": (
            "Hadiths emphasising the immense reward and obligation of "
            "honouring and serving one's parents."
        ),
    },
    {
        "name": "Hadith on Truthfulness",
        "category": "hadith",
        "description": (
            "'Truthfulness leads to righteousness, and righteousness leads "
            "to Paradise' (Sahih al-Bukhari 6094)."
        ),
    },
    {
        "name": "Hadith on Removing Harm from the Road",
        "category": "hadith",
        "description": (
            "The branch of faith: removing harmful objects from the road "
            "as an act of charity (Sahih Muslim 35)."
        ),
    },
    {
        "name": "Hadith on the Best of You",
        "category": "hadith",
        "description": (
            "'The best of you are those who learn the Quran and teach it' "
            "(Sahih al-Bukhari 5027)."
        ),
    },
    {
        "name": "Hadith on Brotherhood in Islam",
        "category": "hadith",
        "description": (
            "'None of you truly believes until he loves for his brother "
            "what he loves for himself' (Sahih al-Bukhari 13)."
        ),
    },
    {
        "name": "Hadith on the Tongue",
        "category": "hadith",
        "description": (
            "Guarding the tongue — 'Whoever believes in Allah and the Last "
            "Day, let him speak good or remain silent' (Sahih al-Bukhari 6018)."
        ),
    },
    # ── Du'a (6 topics) ───────────────────────────────────────────────────
    {
        "name": "Morning and Evening Adhkar",
        "category": "dua",
        "description": (
            "Authentic supplications from the Sunnah for morning (after Fajr) "
            "and evening (after Asr/Maghrib) remembrance."
        ),
    },
    {
        "name": "Du'a Before Sleep",
        "category": "dua",
        "description": (
            "Prophetic supplications recited before sleeping, including "
            "Ayat Al-Kursi and the three Quls."
        ),
    },
    {
        "name": "Du'a for Entering and Leaving the Mosque",
        "category": "dua",
        "description": (
            "Sunnah supplications when stepping into and out of the masjid."
        ),
    },
    {
        "name": "Du'a for Parents",
        "category": "dua",
        "description": (
            "Quranic and Prophetic supplications for one's parents: "
            "'Rabbi irhamhuma kama rabbayanee sagheera' (17:24)."
        ),
    },
    {
        "name": "Du'a in Times of Distress",
        "category": "dua",
        "description": (
            "Supplications taught by the Prophet ﷺ for moments of anxiety, "
            "grief, and hardship."
        ),
    },
    {
        "name": "Du'a After Obligatory Prayers",
        "category": "dua",
        "description": (
            "Adhkar and supplications recited after the tasleem of each "
            "obligatory prayer, as established in the Sunnah."
        ),
    },
    # ── Daily Reminders (6 topics) ────────────────────────────────────────
    {
        "name": "Patience in Hardship (As-Sabr)",
        "category": "daily_reminder",
        "description": (
            "Quranic and Prophetic reminders on the virtue and reward of "
            "patience during trials and tribulations."
        ),
    },
    {
        "name": "Gratitude to Allah (Ash-Shukr)",
        "category": "daily_reminder",
        "description": (
            "The importance of being grateful: 'If you are grateful, I will "
            "surely increase you' (Quran 14:7)."
        ),
    },
    {
        "name": "The Power of Istighfar",
        "category": "daily_reminder",
        "description": (
            "Seeking forgiveness from Allah — its virtues, rewards, and "
            "how it opens doors to provision and peace."
        ),
    },
    {
        "name": "Trust in Allah (Tawakkul)",
        "category": "daily_reminder",
        "description": (
            "Relying on Allah after tying the camel — balancing effort "
            "with complete trust in Allah's plan."
        ),
    },
    {
        "name": "The Remembrance of Death",
        "category": "daily_reminder",
        "description": (
            "'Remember often the destroyer of pleasures' — the Prophet's ﷺ "
            "advice on keeping the Hereafter in perspective."
        ),
    },
    {
        "name": "Kindness in Speech and Action",
        "category": "daily_reminder",
        "description": (
            "Gentle speech, smiling, and small acts of kindness as "
            "continuous charity in Islam."
        ),
    },
    # ── Names of Allah (7 topics) ─────────────────────────────────────────
    {
        "name": "Ar-Rahman — The Most Gracious",
        "category": "names_of_allah",
        "description": (
            "The all-encompassing mercy of Allah that covers every creation, "
            "believer and disbeliever alike."
        ),
    },
    {
        "name": "Al-Malik — The King, The Sovereign",
        "category": "names_of_allah",
        "description": (
            "Allah's absolute sovereignty and ownership over all dominion "
            "and creation."
        ),
    },
    {
        "name": "As-Salam — The Source of Peace",
        "category": "names_of_allah",
        "description": (
            "Allah is free from every imperfection and is the source of all "
            "peace and security."
        ),
    },
    {
        "name": "Al-Ghaffar — The Repeatedly Forgiving",
        "category": "names_of_allah",
        "description": (
            "Allah's attribute of forgiving sins repeatedly, no matter "
            "how many times a servant returns in repentance."
        ),
    },
    {
        "name": "Al-Wadud — The Most Loving",
        "category": "names_of_allah",
        "description": (
            "Allah's love for His righteous servants and the reciprocal "
            "love the believer should cultivate."
        ),
    },
    {
        "name": "Al-Hakeem — The All-Wise",
        "category": "names_of_allah",
        "description": (
            "Allah's perfect wisdom in every decree, legislation, and "
            "creation — nothing is without purpose."
        ),
    },
    {
        "name": "Ash-Shakur — The Most Appreciative",
        "category": "names_of_allah",
        "description": (
            "Allah multiplies the reward of even small good deeds and "
            "appreciates every sincere effort."
        ),
    },
    # ── Seerah (6 topics) ─────────────────────────────────────────────────
    {
        "name": "The Birth and Early Life of the Prophet ﷺ",
        "category": "seerah",
        "description": (
            "The Year of the Elephant, the Prophet's ﷺ childhood, and "
            "the signs that preceded his prophethood."
        ),
    },
    {
        "name": "The First Revelation in Cave Hira",
        "category": "seerah",
        "description": (
            "The night Jibreel descended with 'Iqra' — the beginning "
            "of the Quranic revelation."
        ),
    },
    {
        "name": "The Hijrah to Madinah",
        "category": "seerah",
        "description": (
            "The Prophet's ﷺ migration from Makkah to Madinah — "
            "sacrifice, trust in Allah, and the birth of the Islamic state."
        ),
    },
    {
        "name": "The Battle of Badr",
        "category": "seerah",
        "description": (
            "The first major battle in Islam — 313 believers against 1000, "
            "and the decisive aid of Allah."
        ),
    },
    {
        "name": "The Conquest of Makkah",
        "category": "seerah",
        "description": (
            "The bloodless liberation of Makkah and the Prophet's ﷺ "
            "mercy toward the Quraysh."
        ),
    },
    {
        "name": "The Farewell Sermon",
        "category": "seerah",
        "description": (
            "The Prophet's ﷺ final Hajj sermon — a universal charter "
            "of human rights and dignity in Islam."
        ),
    },
    # ── Islamic History (5 topics) ────────────────────────────────────────
    {
        "name": "The Rightly Guided Caliphs — Al-Khulafa Ar-Rashidun",
        "category": "islamic_history",
        "description": (
            "The era of Abu Bakr, Umar, Uthman, and Ali — the golden "
            "age of Islamic governance."
        ),
    },
    {
        "name": "The Compilation of the Quran",
        "category": "islamic_history",
        "description": (
            "How the Quran was preserved: from oral tradition to the "
            "Uthmanic codex that we read today."
        ),
    },
    {
        "name": "The House of Wisdom — Bayt al-Hikmah",
        "category": "islamic_history",
        "description": (
            "The Abbasid golden age of knowledge: translation, science, "
            "medicine, and scholarship in Baghdad."
        ),
    },
    {
        "name": "Salahuddin Al-Ayyubi and the Liberation of Jerusalem",
        "category": "islamic_history",
        "description": (
            "The character, faith, and military genius of Salahuddin "
            "and the reconquest of Al-Quds."
        ),
    },
    {
        "name": "The Spread of Islam in Southeast Asia",
        "category": "islamic_history",
        "description": (
            "How traders and scholars brought Islam to the Malay "
            "Archipelago through character and commerce."
        ),
    },
    # ── Akhlaq — Islamic Ethics (5 topics) ────────────────────────────────
    {
        "name": "Husn Al-Khuluq — Good Character",
        "category": "akhlaq",
        "description": (
            "The Prophet ﷺ said: 'The heaviest thing placed on the scale "
            "is good character' (Abu Dawud 4799)."
        ),
    },
    {
        "name": "Humility and Avoiding Arrogance",
        "category": "akhlaq",
        "description": (
            "Islam's emphasis on humility — 'No one who has the weight "
            "of a seed of arrogance in his heart will enter Paradise.'"
        ),
    },
    {
        "name": "Fulfilling Promises and Trusts (Amanah)",
        "category": "akhlaq",
        "description": (
            "The importance of keeping one's word and safeguarding "
            "what is entrusted to you."
        ),
    },
    {
        "name": "Generosity and Charity (Sadaqah)",
        "category": "akhlaq",
        "description": (
            "The Prophet ﷺ was the most generous of people — charity "
            "in all its forms from wealth to a smile."
        ),
    },
    {
        "name": "Controlling Anger",
        "category": "akhlaq",
        "description": (
            "'The strong person is not the one who overpowers others; "
            "the strong person is the one who controls himself when angry.'"
        ),
    },
    # ── Tawheed — Islamic Monotheism (5 topics) ──────────────────────────
    {
        "name": "The Meaning of La Ilaha Illa Allah",
        "category": "tawheed",
        "description": (
            "The declaration of faith — its conditions, implications, "
            "and transformative power in a Muslim's life."
        ),
    },
    {
        "name": "Tawheed Ar-Rububiyyah — Lordship of Allah",
        "category": "tawheed",
        "description": (
            "Affirming that Allah alone is the Creator, Sustainer, "
            "and Controller of all affairs."
        ),
    },
    {
        "name": "Tawheed Al-Uluhiyyah — Worship of Allah Alone",
        "category": "tawheed",
        "description": (
            "Directing all acts of worship — prayer, supplication, "
            "reliance — exclusively to Allah."
        ),
    },
    {
        "name": "Tawheed Al-Asma wa As-Sifat — Names and Attributes",
        "category": "tawheed",
        "description": (
            "Affirming Allah's names and attributes as He described "
            "Himself, without distortion or denial."
        ),
    },
    {
        "name": "The Danger of Shirk",
        "category": "tawheed",
        "description": (
            "Understanding major and minor shirk, and how to protect "
            "one's Tawheed from subtle forms of associating partners."
        ),
    },
    # ── Salah — Prayer (5 topics) ─────────────────────────────────────────
    {
        "name": "The Importance of Salah in Islam",
        "category": "salah",
        "description": (
            "Salah as the pillar of Islam, the first deed to be judged, "
            "and the connection between the servant and Allah."
        ),
    },
    {
        "name": "Khushoo in Salah — Concentration and Humility",
        "category": "salah",
        "description": (
            "Practical tips from the Quran and Sunnah to achieve "
            "presence of heart during prayer."
        ),
    },
    {
        "name": "Fajr Prayer — Struggling to Wake Up",
        "category": "salah",
        "description": (
            "The immense reward of Fajr, tips for waking up, and the "
            "Prophet's ﷺ words on guarding this prayer."
        ),
    },
    {
        "name": "Sunnah and Nafl Prayers",
        "category": "salah",
        "description": (
            "Voluntary prayers that supplement the five daily — "
            "Rawatib, Duha, Tahajjud, and their rewards."
        ),
    },
    {
        "name": "The Night Prayer (Qiyam Al-Layl)",
        "category": "salah",
        "description": (
            "Standing in prayer at night — its virtues, the best time, "
            "and practical guidance from the Sunnah."
        ),
    },
    # ── Quran Recitation (5 topics) ───────────────────────────────────────
    {
        "name": "Virtues of Reciting Surah Al-Baqarah",
        "category": "quran_recitation",
        "description": (
            "Why the Prophet ﷺ called it the pinnacle of the Quran and "
            "how it wards off Shaytan from the home."
        ),
    },
    {
        "name": "The Reward of Each Letter of the Quran",
        "category": "quran_recitation",
        "description": (
            "'Whoever reads a letter from the Book of Allah will have "
            "a reward, and that reward will be multiplied by ten.'"
        ),
    },
    {
        "name": "Beautifying the Voice with Quran",
        "category": "quran_recitation",
        "description": (
            "'Adorn the Quran with your voices' — the Sunnah of "
            "reciting with tarteel and a melodious voice."
        ),
    },
    {
        "name": "The Quran as an Intercessor on the Day of Judgement",
        "category": "quran_recitation",
        "description": (
            "The Quran will come as an intercessor for its companions — "
            "those who recited and acted upon it."
        ),
    },
    {
        "name": "Tadabbur — Reflecting on the Meanings of the Quran",
        "category": "quran_recitation",
        "description": (
            "'Do they not reflect upon the Quran?' (4:82) — the "
            "obligation and method of contemplating the Quran."
        ),
    },
    # ── Fiqh Basics (5 topics) ────────────────────────────────────────────
    {
        "name": "The Five Pillars of Islam",
        "category": "fiqh_basic",
        "description": (
            "Shahada, Salah, Zakah, Sawm, and Hajj — the foundational "
            "obligations every Muslim must know."
        ),
    },
    {
        "name": "Wudu — Ablution Before Prayer",
        "category": "fiqh_basic",
        "description": (
            "The pillars, Sunnah acts, and nullifiers of wudu as "
            "taught by the Prophet ﷺ."
        ),
    },
    {
        "name": "Fasting in Ramadan",
        "category": "fiqh_basic",
        "description": (
            "Rulings, virtues, and spiritual goals of the obligatory "
            "fast during the blessed month of Ramadan."
        ),
    },
    {
        "name": "Zakah — Purification of Wealth",
        "category": "fiqh_basic",
        "description": (
            "Who must pay Zakah, its nisab thresholds, eligible "
            "recipients, and the spiritual wisdom behind it."
        ),
    },
    {
        "name": "Halal and Haram in Daily Life",
        "category": "fiqh_basic",
        "description": (
            "General principles for distinguishing the permissible "
            "from the prohibited in food, drink, and transactions."
        ),
    },
]


# ---------------------------------------------------------------------------
# Seeder function
# ---------------------------------------------------------------------------


async def seed_topics(db_session: AsyncSession) -> int:
    """Insert seed topics into the database, skipping duplicates.

    Topics are matched by *name* — if a topic with the same name already
    exists it is silently skipped.

    Parameters
    ----------
    db_session:
        An async SQLAlchemy session (should be committed by the caller
        or the ``get_db`` dependency).

    Returns
    -------
    int
        Number of **new** topics inserted.
    """
    logger.info("seed_topics.start", total_seed_topics=len(SEED_TOPICS))

    # Fetch existing topic names to avoid duplicates
    result = await db_session.execute(select(ContentTopic.name))
    existing_names: set[str] = {row[0] for row in result.all()}

    inserted = 0
    for topic_data in SEED_TOPICS:
        name = topic_data["name"]
        if name in existing_names:
            continue

        category_value = topic_data["category"]
        try:
            category = TopicCategory(category_value)
        except ValueError:
            logger.warning(
                "seed_topics.invalid_category",
                name=name,
                category=category_value,
            )
            continue

        topic = ContentTopic(
            name=name,
            category=category,
            description=topic_data.get("description"),
            weight_score=1.0,
        )
        db_session.add(topic)
        inserted += 1

    if inserted:
        await db_session.flush()

    logger.info(
        "seed_topics.complete",
        inserted=inserted,
        skipped=len(SEED_TOPICS) - inserted,
    )
    return inserted
