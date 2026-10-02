"""Storage column names behind source-neutral domain names (§naming-map).

The domain/application layer calls a work's upstream identity ``work_id``
(source-neutral). The persisted column predates that vocabulary and keeps its
historical name forever (constraint: zero-renames on storage). All SQL that
touches the column should reference this constant instead of scattering the
literal, so the wire→domain→storage mapping has exactly one home:

    wire ``work_id`` (preferred) / ``pixiv_id`` (deprecated alias)
      → domain ``work_id``
      → storage column ``WORK_ID_COLUMN`` ("pixiv_id", immutable)

Audit payloads also keep the historical ``pixiv_id`` key: audit_events rows
are a persisted schema that old readers may still consume.
"""

#: The immutable storage column carrying the domain ``work_id``.
WORK_ID_COLUMN = "pixiv_id"
