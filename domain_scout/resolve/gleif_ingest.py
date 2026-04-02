"""Download GLEIF golden copy data and build a local DuckDB file.

Downloads LEI entity and relationship CSVs from gleif.org, filters to
active records, and creates an indexed DuckDB file for use with
``Scout(config=ScoutConfig(gleif_db_path=...))``.

The GLEIF golden copy is updated daily and freely available under the
GLEIF Terms of Use: https://www.gleif.org/en/meta/gleif-data-license
"""

from __future__ import annotations

import io
import tempfile
import zipfile
from pathlib import Path

import httpx
import structlog

log = structlog.get_logger()

_GLEIF_API = "https://goldencopy.gleif.org/api/v2/golden-copies/publishes/latest"

_DEFAULT_DB_DIR = Path.home() / ".local" / "share" / "domain-scout"
DEFAULT_GLEIF_DB = _DEFAULT_DB_DIR / "gleif.duckdb"


def _get_download_urls() -> tuple[str, str]:
    """Fetch latest LEI2 and RR CSV zip URLs from GLEIF API."""
    resp = httpx.get(_GLEIF_API, timeout=30)
    resp.raise_for_status()
    data = resp.json()["data"]
    lei2_url: str = data["lei2"]["full_file"]["csv"]["url"]
    rr_url: str = data["rr"]["full_file"]["csv"]["url"]
    return lei2_url, rr_url


def _download_and_extract(url: str, dest_dir: Path) -> Path:
    """Download a zip file and extract the CSV inside it."""
    log.info("gleif.downloading", url=url.split("/")[-1])
    resp = httpx.get(url, timeout=300, follow_redirects=True)
    resp.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        csv_names = [n for n in zf.namelist() if n.endswith(".csv")]
        if not csv_names:
            raise ValueError(f"No CSV found in {url}")
        csv_name = csv_names[0]
        zf.extract(csv_name, dest_dir)
        return dest_dir / csv_name


def ingest(output: Path = DEFAULT_GLEIF_DB) -> Path:
    """Download GLEIF data and build a DuckDB file.

    Returns the path to the created database.
    """
    import duckdb

    lei2_url, rr_url = _get_download_urls()

    with tempfile.TemporaryDirectory(prefix="gleif-") as tmpdir:
        tmp = Path(tmpdir)
        lei2_csv = _download_and_extract(lei2_url, tmp)
        rr_csv = _download_and_extract(rr_url, tmp)

        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists():
            output.unlink()

        con = duckdb.connect(str(output))

        # Load entities (active only)
        log.info("gleif.loading_entities", csv=lei2_csv.name)
        con.execute(f"""
            CREATE TABLE gleif_entity AS
            SELECT
                "LEI" AS lei,
                "Entity.LegalName" AS legal_name,
                LIST_FILTER(
                    ["Entity.OtherEntityNames.OtherEntityName.1",
                     "Entity.OtherEntityNames.OtherEntityName.2",
                     "Entity.OtherEntityNames.OtherEntityName.3",
                     "Entity.OtherEntityNames.OtherEntityName.4",
                     "Entity.OtherEntityNames.OtherEntityName.5"],
                    x -> x IS NOT NULL AND x != ''
                ) AS other_names,
                "Entity.LegalAddress.Country" AS country
            FROM read_csv('{lei2_csv!s}', all_varchar=true, ignore_errors=true)
            WHERE "Entity.EntityStatus" = 'ACTIVE'
        """)

        # Load relationships (active only)
        log.info("gleif.loading_relationships", csv=rr_csv.name)
        con.execute(f"""
            CREATE TABLE gleif_relationship AS
            SELECT
                "Relationship.StartNode.NodeID" AS child_lei,
                "Relationship.EndNode.NodeID" AS parent_lei,
                "Relationship.RelationshipType" AS relationship_type,
                "Relationship.RelationshipStatus" AS relationship_status
            FROM read_csv('{rr_csv!s}', all_varchar=true, ignore_errors=true)
            WHERE "Relationship.RelationshipStatus" = 'ACTIVE'
        """)

        # Create indexes
        con.execute("CREATE INDEX idx_gleif_legal ON gleif_entity (legal_name)")
        con.execute("CREATE INDEX idx_gleif_lower ON gleif_entity (LOWER(legal_name))")
        con.execute("CREATE INDEX idx_gleif_rel_child ON gleif_relationship (child_lei)")
        con.execute("CREATE INDEX idx_gleif_rel_parent ON gleif_relationship (parent_lei)")

        entity_row = con.execute("SELECT COUNT(*) FROM gleif_entity").fetchone()
        rel_row = con.execute("SELECT COUNT(*) FROM gleif_relationship").fetchone()
        entity_count: int = entity_row[0] if entity_row else 0
        rel_count: int = rel_row[0] if rel_row else 0
        con.close()

    size_mb = output.stat().st_size / (1024 * 1024)
    log.info(
        "gleif.ingest_complete",
        entities=entity_count,
        relationships=rel_count,
        output=str(output),
        size_mb=round(size_mb, 1),
    )
    return output
