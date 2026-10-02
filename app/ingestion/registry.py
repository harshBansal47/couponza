from app.ingestion.base import SourceAdapter
from app.ingestion.sources.csv_source import CsvSource
from app.ingestion.sources.feed import FeedSource
from app.ingestion.sources.static import StaticSource
from app.models.source import SourceKind

_ADAPTERS: dict[SourceKind, type] = {
    SourceKind.csv: CsvSource,
    SourceKind.static: StaticSource,
    SourceKind.feed: FeedSource,
}


def get_adapter(kind: SourceKind) -> SourceAdapter:
    try:
        return _ADAPTERS[kind]()
    except KeyError as exc:
        raise ValueError(f"No adapter registered for source kind {kind!r}") from exc
