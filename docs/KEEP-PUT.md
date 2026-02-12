# keep put

Add or update a document in the store.

## Usage

Three input modes, auto-detected:

```bash
keep put "my note"                    # Text mode (inline content)
keep put file:///path/to/doc.pdf      # URI mode (fetch and index)
keep put https://example.com/page     # URI mode (web content)
keep put -                            # Stdin mode (explicit)
echo "piped content" | keep put       # Stdin mode (detected)
```

## Options

| Option | Description |
|--------|-------------|
| `-t`, `--tag KEY=VALUE` | Tag as key=value (repeatable) |
| `-i`, `--id ID` | Custom document ID (auto-generated for text/stdin) |
| `--summary TEXT` | User-provided summary (skips auto-summarization) |
| `--suggest-tags` | Show tag suggestions from similar items |
| `-s`, `--store PATH` | Override store directory |

## Text mode and content-addressed IDs

Text mode uses content-addressed IDs for automatic versioning:

```bash
keep put "my note"              # Creates %a1b2c3d4e5f6
keep put "my note" -t done      # Same ID, new version (tag change)
keep put "different note"       # Different ID (new document)
```

Same content = same ID = enables versioning through tag changes.

## Smart summary behavior

- **Short content** (under `max_summary_length`, default 1000 chars): stored verbatim as its own summary
- **Long content**: truncated placeholder stored immediately, real summary generated in background by `process-pending`
- **`--summary` provided**: used as-is, skips auto-summarization

## Tag suggestions

The `--suggest-tags` flag shows tags from similar existing items, helping maintain consistent tagging:

```bash
keep put "OAuth2 token handling" --suggest-tags
# Suggests: project=myapp (3 similar), topic=auth (5 similar)
```

## Update behavior

When updating an existing document (same ID):
- **Summary**: replaced with new summary
- **Tags**: merged — existing tags preserved, new tags override on key collision
- **Version**: previous version archived automatically

## Contextual summarization

When you provide tags during indexing, the summarizer uses context from related items to produce more relevant summaries.

1. System finds similar items sharing your tags
2. Items with more matching tags rank higher (+20% score boost per tag)
3. Top related summaries are passed as context to the LLM
4. Summary highlights relevance to that context

Tag changes trigger re-summarization:
```bash
keep put doc.pdf                       # Generic summary
keep put doc.pdf -t topic=auth         # Re-queued for contextual summary
```

## Supported formats

| Format | Extensions | Content extracted | Auto-tags |
|--------|-----------|-------------------|-----------|
| **Text** | .md, .txt, .py, .js, .json, .yaml, ... | Full text | — |
| **PDF** | .pdf | Text from all pages | — |
| **HTML** | .html, .htm | Text (scripts/styles removed) | — |
| **DOCX** | .docx | Paragraphs + tables | `author`, `title` |
| **PPTX** | .pptx | Slides + notes | `author`, `title` |
| **Audio** | .mp3, .flac, .ogg, .wav, .aiff, .m4a, .wma | Structured metadata (+ transcription\*) | `artist`, `album`, `genre`, `year`, `title` |
| **Images** | .jpg, .png, .tiff, .webp | EXIF metadata (+ description\*) | `dimensions`, `camera`, `date` |

\* When a media description provider is configured (`[media]` in `keep.toml`), images get vision-model descriptions and audio files get speech-to-text transcription, appended to the extracted metadata. See [QUICKSTART.md](QUICKSTART.md#media-description-optional) for setup.

Auto-extracted tags merge with user-provided tags. User tags win on collision:

```bash
keep put file:///path/to/song.mp3                    # Auto-tags: artist, album, genre, year
keep put file:///path/to/song.mp3 -t genre="Nu Jazz" # Overrides auto-extracted genre
keep put file:///path/to/photo.jpg -t topic=vacation  # Adds topic alongside auto camera/date
```

## Indexing documents

Index important documents encountered during work:

```bash
keep put "https://docs.example.com/auth" -t topic=auth -t project=myapp
keep put "file:///path/to/design.pdf" -t type=reference -t topic=architecture
```

## See Also

- [TAGGING.md](TAGGING.md) — Tag system, merge order, speech acts
- [VERSIONING.md](VERSIONING.md) — How versioning works
- [KEEP-GET.md](KEEP-GET.md) — Retrieve indexed documents
- [REFERENCE.md](REFERENCE.md) — Quick reference index
