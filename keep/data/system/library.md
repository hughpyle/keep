---
tags:
  category: system
  context: library
---
# Library

Public domain texts for testing and bootstrapping keep.
The content, as well as the format, is relevant to the practice of this skill.

## Resolving File Paths

The library files are located in the `docs/library/` directory of the keep package.
To construct URIs for these files:

1. **From shell:** Use `file://$(keep config tool)/docs/library/{filename}`
2. **In Python:**
   ```python
   from importlib.resources import files
   library_path = files("keep").parent / "docs" / "library"
   uri = f"file://{library_path}/{filename}"
   ```

---

## Files

### ancrenewisse.pdf
- **URI template:** `file://{keep_library}/ancrenewisse.pdf`
- **Title:** Ancrene Wisse (Ancrene Riwle)
- **Date:** c. 1200s (13th century)
- **Language:** Middle English
- **Translator:** James Morton, The Camden Society, London 1853
- **Source:** https://www.bsswebsite.me.uk/History/AncreneRiwle/AncreneRiwle2.html
- **Status:** Public domain
- **Description:** A monastic guide for Christian anchoresses.  Provides guidance on conduct with an "inner" and "outer" rule, and their relationship: "one relates to the right conduct of the heart; the other, to the regulation of the outward life".

---

### han_verse.txt
- **URI template:** `file://{keep_library}/han_verse.txt`
- **Title:** 版の偈 (Han Verse / Inscription on the Zen Sounding Board)
- **Date:** Traditional Zen verse; origins in Chinese Chan monastic codes (Song dynasty onward)
- **Language:** Japanese (Kanji), with romanization and multiple English translations
- **Source:** Pan-Zen tradition (Soto, Rinzai, Obaku); Chinese Chan monastic codes
- **Status:** Traditional teaching, freely shared
- **Description:** Four-line verse inscribed on the han (wooden sounding board) struck to signal practice periods. "Great is the matter of birth and death / Impermanence is swift / Each of us must awaken / Don't be careless." Includes Soto and Rinzai variants, character-by-character breakdown, cultural context, and linguistic notes.

---

### mn61.html
- **URI template:** `file://{keep_library}/mn61.html`
- **Title:** Ambalaṭṭhikārāhulovāda Sutta (MN 61) - The Exhortation to Rāhula at Mango Stone
- **Date:** Original: ~5th century BCE; Translation: contemporary
- **Language:** English translation from Pali
- **Translator:** Thanissaro Bhikkhu
- **Source:** https://www.dhammatalks.org/suttas/MN/MN61.html
- **Format:** Raw HTML (complete with markup, navigation, footnotes)
- **License:** Freely distributed for educational use
- **Description:** Buddha's teaching to his son Rāhula on reflection before, during, and after bodily, verbal, and mental actions. The triple-check pattern: reflect before acting/speaking, check while doing, review after. Mirror metaphor for self-reflection.
**Format note:** Kept as raw HTML to test document processing and summarization on markup-heavy content.

---

### an5.57_translation-en-sujato.json
- **URI template:** `file://{keep_library}/an5.57_translation-en-sujato.json`
- **Title:** Upajjhāyasutta (AN 5.57) - Subjects for Regular Reviewing
- **Date:** Original: ~5th century BCE; Translation: modern
- **Language:** English translation from Pali
- **Translator:** Bhikkhu Sujato
- **Source:** SuttaCentral
- **Source URL:** https://suttacentral.net/an5.57/en/sujato?lang=en
- **Data:** https://github.com/suttacentral/sc-data/blob/main/sc_bilara_data/translation/en/sujato/sutta/an/an5/an5.57_translation-en-sujato.json
- **License:** Creative Commons CC0 1.0 Universal (SuttaCentral translations)
- **Description:** The Five Remembrances - five subjects that all sentient beings should reflect on regularly: aging, sickness, death, separation from loved ones, and being heir to one's own actions.  "Reviewing this subject often, they entirely give up bad conduct, or at least reduce it".

---

### fortytwo_chapters.txt
- **URI template:** `file://{keep_library}/fortytwo_chapters.txt`
- **Title:** 佛說四十二章經 (Sutra of Forty-Two Chapters)
- **Date:** Eastern Han Dynasty (25-220 CE)
- **Language:** Classical Chinese
- **Source:** Project Gutenberg (#23585)
- **Status:** Public domain
- **Description:** One of the earliest Buddhist texts to reach China, traditionally attributed to translation by Kāśyapa Mātaṅga and Dharmarakṣa

---

### mumford_sticks_and_stones.txt
- **URI template:** `file://{keep_library}/mumford_sticks_and_stones.txt`
- **Title:** Sticks and Stones: A Study of American Architecture and Civilization
- **Author:** Lewis Mumford (1895-1990)
- **Date:** 1924
- **Language:** English
- **Source:** Internet Archive (sticksstones0000lewi)
- **Status:** Public domain (published before 1929)
- **Description:** Mumford's first major work on architecture, examining American building traditions from medieval influences through industrialization. Includes chapters on "The Medieval Tradition," "The Renaissance in New England," "The Age of Rationalism," and more.

**Note:** This is OCR text from archive.org. Quality is good but may contain occasional scanning artifacts.

---

### true_person_no_rank.md
- **URI template:** `file://{keep_library}/true_person_no_rank.md`
- **Title:** 無位真人 (The True Person of No Rank)
- **Date:** Original: 9th century CE; Commentary layers: 9th-20th centuries
- **Language:** Chinese (verified original text) with English translation and commentary
- **Source:** Record of Linji (臨濟錄, Línjì Lù); Book of Serenity (從容錄) Case 38
- **Primary sources:** DILA Buddhist Dictionary, multiple scholarly translations
- **Status:** Core teaching in public domain; compiled with verification notes
- **Description:** Linji Yixuan's famous teaching: "Within this mass of red flesh, there is a true person of no rank, constantly coming and going through the gates of your face." Multi-layered document exploring the original teaching, koan tradition, Dōgen's commentary, modern interpretations, and linguistic analysis. Includes Chinese text (verified), translations, and commentary relationships.

---

## Usage for Testing

These texts provide diverse test cases for keep:

1. **Different languages:** English, Chinese (Classical and modern romanization), Japanese, Middle English, Pali (via translation)
2. **Different formats:** PDF, plain text, JSON, Markdown, HTML (with markup)
3. **Different domains:** Buddhist teachings, Zen liturgy, architectural criticism, medieval instructional prose
4. **Different writing styles:** Ancient scripture, koan commentary, scholarly analysis, liturgical verse, teaching notes
5. **Different lengths:** Four-line verses to full books
6. **Different structures:** Linear narratives, multi-layered commentaries, character-by-character analysis, mirror patterns, web documents with navigation
7. **Multilingual content:** Japanese-English parallel texts, Chinese with romanization, cross-linguistic terminology
8. **Processing challenges:** Markdown, UTF-8 plaintext, OCR artifacts (Mumford), HTML markup (MN 61), PDF extraction (Ancrene Wisse), structured JSON data (AN5.57).

---

## Adding More Test Data

When adding public domain texts:

1. Verify their relevance to the practice of this skill
2. Verify compatibility with the MIT license, e.g. public domain status (pre-1929 for US, or explicit license)
3. Include source URL (Project Gutenberg, archive.org, etc.)
4. Add metadata to this index

---

## License

Each text retains its original license status (public domain or Creative Commons as noted above). This index and dataset organization is released under CC0 1.0.
