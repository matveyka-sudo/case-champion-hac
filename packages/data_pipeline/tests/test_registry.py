import re

from a2_data_pipeline.manifest import load_dataset_manifest, load_registry
from a2_data_pipeline.paths import repo_root


def registry_doc_ids() -> list[str]:
    text = (repo_root() / "docs" / "SOURCE_REGISTRY.md").read_text(encoding="utf-8")
    return re.findall(r"^\| ([a-z_]+) \|", text, flags=re.MULTILINE)


def test_every_a2_source_has_a_role() -> None:
    registry = load_registry()
    ids = {entry.source_id for entry in registry.entries}
    documented = registry_doc_ids()
    assert len(documented) == 24
    assert set(documented) <= ids


def test_a2_refs_cover_s01_to_s22() -> None:
    refs = {entry.a2_ref for entry in load_registry().entries if entry.a2_ref}
    assert refs == {f"S{number:02d}" for number in range(1, 23)}


def test_registered_is_not_accepted() -> None:
    for entry in load_registry().entries:
        if entry.status == "accepted":
            assert entry.license.status == "permitted", entry.source_id
            assert entry.datasets, entry.source_id


def test_registry_datasets_have_manifests() -> None:
    for entry in load_registry().entries:
        statuses = []
        for dataset_id in entry.datasets:
            manifest = load_dataset_manifest(dataset_id)
            assert manifest.source_id == entry.source_id, dataset_id
            statuses.append(manifest.status)
        if entry.status == "accepted":
            assert "accepted" in statuses, entry.source_id
