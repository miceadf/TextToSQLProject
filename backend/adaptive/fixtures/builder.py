from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from ..catalog import SQLiteAdapter
from ..mapping import RuleBasedSchemaAligner
from ..ontology import DomainOntology, load_default_ontology
from .common import write_atomic_json
from .coded import build_coded_database
from .contracts import (
    CORE_VARIANTS,
    FIXTURE_SUITES,
    FixtureArtifact,
    FixtureBundle,
    FixtureSeed,
    FixtureVariant,
)
from .flat import build_flat_database
from .hybrid import build_hybrid_database
from .legacy import build_legacy_database
from .normalized import build_normalized_database
from .seed import DEFAULT_SEED


BUILDERS = {
    FixtureVariant.FLAT: build_flat_database,
    FixtureVariant.NORMALIZED: build_normalized_database,
    FixtureVariant.LEGACY: build_legacy_database,
    FixtureVariant.CODED: build_coded_database,
    FixtureVariant.HYBRID: build_hybrid_database,
}


def build_fixture_bundle(
    output_directory: str | Path,
    *,
    seed: FixtureSeed = DEFAULT_SEED,
    ontology: DomainOntology | None = None,
    variants: tuple[FixtureVariant, ...] = CORE_VARIANTS,
) -> FixtureBundle:
    if tuple(variants) not in FIXTURE_SUITES.values():
        raise ValueError("fixture variants must be exactly one fixture suite")
    output = Path(output_directory).resolve()
    output.mkdir(parents=True, exist_ok=True)
    generation_id = uuid4().hex
    generation = output / "generations" / generation_id
    generation.mkdir(parents=True, exist_ok=False)
    domain_ontology = ontology or load_default_ontology()
    aligner = RuleBasedSchemaAligner()
    artifacts: dict[FixtureVariant, FixtureArtifact] = {}

    for variant in variants:
        build_database = BUILDERS[variant]
        database_id = f"fixture-{variant.value}-v{seed.version}"
        database_path = build_database(
            generation / f"{variant.value}.sqlite3",
            seed,
        )
        catalog = SQLiteAdapter({database_id: database_path}).inspect(database_id)
        proposal = aligner.align(catalog, domain_ontology)
        catalog_path = write_atomic_json(
            generation / f"{variant.value}.catalog.json",
            catalog,
        )
        proposal_path = write_atomic_json(
            generation / f"{variant.value}.proposal.json",
            proposal,
        )
        artifacts[variant] = FixtureArtifact(
            variant=variant,
            database_id=database_id,
            database_path=database_path,
            catalog_path=catalog_path,
            proposal_path=proposal_path,
        )

    manifest_path = output / "bundle.json"
    bundle = FixtureBundle(
        generation_id=generation_id,
        seed_version=seed.version,
        manifest_path=manifest_path,
        artifacts=artifacts,
    )
    write_atomic_json(manifest_path, bundle)
    return bundle
