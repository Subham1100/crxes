"""Size caps for the ingest phase.

Two tiers, because a paste and an upload are not the same problem. A paste is
typed or clipboard-copied by a human and stays small; an upload is a real
incident dump, and capping it at paste size would throw away exactly the
history the diagnosis stages need.

The caps are enforced at ingest, not at parse — a file that overflows is
truncated and *reported*, never silently dropped.
"""

#: A pasted blob. Also the `max_length` on `AnalyzeRequest.logs`, so an
#: oversized paste is rejected by validation instead of being truncated
#: somewhere deeper where the user cannot see it happen.
MAX_PASTE_BYTES = 2_000_000
MAX_PASTE_LINES = 20_000

#: An uploaded file set. Correlation and digest run over everything; only the
#: reconstructed flows reach an LLM, so the ceiling here is memory, not tokens.
MAX_UPLOAD_BYTES = 64_000_000
MAX_UPLOAD_LINES = 500_000

#: Files per upload. More than this is a directory, and a directory should be
#: an archive with per-file roles rather than a hundred form parts.
MAX_FILES = 32

#: Lines sampled from the head of a file to decide its format. Enough to get
#: past a banner or a stanza of startup noise without reading a 40MB file
#: twice.
DETECT_SAMPLE_LINES = 200

#: A single log line longer than this is truncated. Minified JS bundles and
#: base64 payloads land in logs and would otherwise dominate memory.
MAX_LINE_CHARS = 64_000

#: Lines folded into one entry as continuations (stack frames, SQL bodies).
#: A runaway Java trace can be thousands of frames; keep the head.
MAX_CONTINUATION_LINES = 200
