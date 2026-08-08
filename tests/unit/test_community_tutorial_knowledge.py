from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import re
import shutil
import sys
import tempfile
import unittest
import zipfile
from contextlib import closing
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit


REPOSITORY_ROOT = Path(__file__).parents[2]
RUNTIME_PACKAGE_ROOT = REPOSITORY_ROOT / "houdini_package" / "python_libs"
COMMUNITY_PACK = REPOSITORY_ROOT / "knowledge" / "community-tutorials"
OFFICIAL_PACK = REPOSITORY_ROOT / "knowledge" / "sidefx-official"
PYRO_TECHNICAL_QUERIES = (
    "Pyro Source Volume Rasterize Attributes density temperature flame",
    "sparse Pyro Solver Voxel Size CFL Condition Max Substeps collision SDF",
    "Pyro Post-Process VDB 16-bit cache velocity motion blur",
)
PYRO_EXPERIMENT_QUERIES = (
    "Pyro EffectSpec source solver material render candidate acceptance",
    "detached flame candle smooth mushroom smoke black Karma render",
    "Pyro Start Frame low resolution viewport stale cache checkpoint",
)
PYRO_NOISE_QUERIES = (
    "Pyro smooth pillar mushroom source shape noise Attribute Noise SOP pscale density temperature flame",
    "Pyro Point Velocity SOP Curl Noise Attribute Adjust Vector Direction Only Length Only",
    "Pyro Solver Disturbance Block-Based Shredding Turbulence Swirl Size Pulse Length Control Field",
    "HOM native Houdini Pyro nodes avoid Python SOP single variable multi-frame validation",
)
PYRO_RECALL_QUERIES = (
    PYRO_TECHNICAL_QUERIES + PYRO_EXPERIMENT_QUERIES + PYRO_NOISE_QUERIES
)
EXECUTABLE_WORKFLOW_TERMS = {
    "cache-debug-evidence": (
        "File Cache SOP",
        "Load from Disk",
        "Missing Frame",
        "Base Name",
    ),
    "copernicus-layer-workflow": (
        "Layer COP",
        "ROP Image Output COP",
        "OCIO Transform COP",
        "Pixel Scale",
    ),
    "curve-procedural-modeling": (
        "Resample SOP",
        "Orientation Along Curve SOP",
        "Sweep SOP",
        "curveu",
    ),
    "flip-fluid-debug-cache": (
        "FLIP Container SOP",
        "FLIP Solver SOP",
        "Particle Separation",
        "Particle Fluid Surface SOP",
    ),
    "hda-interface-contract": (
        "Operator Type Properties",
        "IN_*",
        "OUT_*",
        "Increase Minor Version",
    ),
    "kinefx-transform-rig": (
        "Rig Doctor SOP",
        "Rig Pose SOP",
        "Joint Capture Biharmonic SOP",
        "Bone Deform SOP",
    ),
    "materialx-karma-lookdev": (
        "Material Library LOP",
        "MtlX Standard Surface",
        "Assign Material LOP",
        "specular_roughness",
    ),
    "pdg-tops-work-items": (
        "Range Generate TOP",
        "Wedge TOP",
        "Attribute Create TOP",
        "ROP Geometry Output TOP",
    ),
    "performance-profiling": (
        "Performance Monitor",
        "Compile Begin/End SOP",
        "File Cache SOP",
        "cold and warm",
    ),
    "pyro-fields-cache-contract": (
        "Pyro Source SOP",
        "Volume Rasterize Attributes SOP",
        "CFL Condition",
        "Pyro Post-Process SOP",
    ),
    "rbd-constraint-networks": (
        "RBD Configure SOP",
        "RBD Constraint Properties SOP",
        "RBD Bullet Solver SOP",
        "Bullet Substeps",
    ),
    "solaris-usd-scene-assembly": (
        "SOP Import LOP",
        "Reference LOP",
        "Material Library LOP",
        "USD ROP",
    ),
    "sop-attribute-vex-contracts": (
        "Attribute Wrangle SOP",
        "Attribute Promote SOP",
        "Copy to Points SOP",
        "Run Over",
    ),
    "vellum-constraint-workflow": (
        "Vellum Constraints SOP",
        "Vellum Solver SOP",
        "Thickness",
        "Damping Ratio",
    ),
    "vex-topology-spatial-queries": (
        "nearpoints",
        "xyzdist",
        "primuv",
        "APPLY_TOPOLOGY_EDIT",
    ),
    "viewer-state-interaction": (
        "ViewerStateTemplate",
        "onMouseEvent",
        "onInterrupt",
        "undo",
    ),
}
sys.path.insert(0, str(RUNTIME_PACKAGE_ROOT))

from hia_mcp_runtime.hybrid_knowledge import (  # noqa: E402
    HybridKnowledgeStore,
)
from hia_mcp_runtime.knowledge_index import (  # noqa: E402
    COMMUNITY_TUTORIAL_SOURCE,
    LocalKnowledgeIndex,
    _load_community_pack,
)


class FakeEmbedder:
    """Deterministic encoder that never imports or downloads a model."""

    def __init__(self, *, dim: int = 32) -> None:
        self.available = True
        self.model_id = "Qwen/Qwen3-Embedding-0.6B"
        self.model_revision = "community-fixture-1"
        self.active_profile = "qwen3-embedding-0.6b"
        self.requested_profile = self.active_profile
        self.dim = dim
        self.calls: list[dict[str, tuple[str, ...]]] = []

    def encode(
        self,
        *,
        documents: Sequence[str],
        queries: Sequence[str],
    ) -> Mapping[str, Any]:
        document_values = tuple(str(value) for value in documents)
        query_values = tuple(str(value) for value in queries)
        self.calls.append({"documents": document_values, "queries": query_values})
        return {
            "document_vectors": [
                _fake_vector(value, self.dim) for value in document_values
            ],
            "query_vectors": [_fake_vector(value, self.dim) for value in query_values],
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "profile_id": self.active_profile,
            "active_profile": self.active_profile,
            "requested_profile": self.requested_profile,
            "dim": self.dim,
            "normalized": True,
            "status": "ready",
            "repair": {},
        }

    def status(self) -> Mapping[str, Any]:
        return {
            "available": True,
            "status": "ready",
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "profile_id": self.active_profile,
            "active_profile": self.active_profile,
            "requested_profile": self.requested_profile,
            "dim": self.dim,
            "normalized": True,
            "repair": {},
            "device": "fake-cpu",
        }


class ConfiguredFakeEmbedder(FakeEmbedder):
    """Installed encoder whose worker has not been loaded by this process."""

    def status(self) -> Mapping[str, Any]:
        value = dict(super().status())
        value["status"] = "configured"
        value["ready"] = False
        return value


def _fake_vector(text: str, dim: int) -> list[float]:
    values = [0.0] * dim
    tokens = re.findall(r"[\w.-]+", text.casefold()) or [text.casefold()]
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        values[int.from_bytes(digest[:2], "little") % dim] += (
            1.0 if digest[2] % 2 else -1.0
        )
    norm = math.sqrt(math.fsum(value * value for value in values))
    if not norm:
        values[0] = 1.0
        norm = 1.0
    return [value / norm for value in values]


def _copy_pack(source: Path, project_root: Path) -> Path:
    target = project_root / "knowledge" / source.name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)
    return target


def _build_all_vectors(
    store: HybridKnowledgeStore,
) -> Mapping[str, Any]:
    for _attempt in range(100):
        status = store.build_batch(64)
        if status["complete"]:
            return status
        if int(status["chunks_indexed_this_call"]) <= 0:
            raise AssertionError("Fake vector build made no progress")
    raise AssertionError("Fake vector build did not converge")


def _refresh(index: LocalKnowledgeIndex, *, force: bool) -> Mapping[str, Any]:
    stats, warnings = index.refresh(
        {"project"},
        {"houdini_version": "21.0"},
        force=force,
    )
    if warnings:
        raise AssertionError(f"Unexpected fixture warnings: {warnings!r}")
    return stats


def _load_release_checker() -> Any:
    checker_path = REPOSITORY_ROOT / "scripts" / "check-public-release.py"
    specification = importlib.util.spec_from_file_location(
        "community_release_checker",
        checker_path,
    )
    if specification is None or specification.loader is None:
        raise AssertionError("Could not load release checker")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class CommunityTutorialKnowledgeTests(unittest.TestCase):
    def test_status_uses_configured_signature_without_loading_encoder(self) -> None:
        _copy_pack(COMMUNITY_PACK, self.project_root)
        index = LocalKnowledgeIndex(self.project_root)
        _refresh(index, force=True)
        embedder = ConfiguredFakeEmbedder()
        store = HybridKnowledgeStore(
            self.project_root,
            index=index,
            embedder=embedder,
        )

        before = store.status()

        self.assertEqual("configured", before["status"])
        self.assertTrue(before["available"])
        self.assertFalse(before["current_model_signature_match"])
        self.assertEqual("", before["database_signature"])
        self.assertEqual([], embedder.calls)

        built = _build_all_vectors(store)
        after = store.status()

        self.assertTrue(built["complete"])
        self.assertTrue(after["current_model_signature_match"])
        self.assertEqual(
            "qwen3-embedding-0.6b|Qwen/Qwen3-Embedding-0.6B|"
            "community-fixture-1|32|normalized=1",
            after["database_signature"],
        )

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="hia-community-knowledge-")
        self.project_root = Path(self._temporary.name) / "project"
        self.project_root.mkdir(parents=True)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_pack_schema_provenance_urls_depth_and_deduplication(self) -> None:
        pack = _load_community_pack(COMMUNITY_PACK / "manifest.json")
        manifest = json.loads(
            (COMMUNITY_PACK / "manifest.json").read_text(encoding="utf-8")
        )
        registry = json.loads(
            (COMMUNITY_PACK / "sources.json").read_text(encoding="utf-8")
        )
        coverage = json.loads(
            (COMMUNITY_PACK / "coverage.json").read_text(encoding="utf-8")
        )

        self.assertEqual(COMMUNITY_TUTORIAL_SOURCE, manifest["source_kind"])
        self.assertEqual("community_unverified", manifest["verification"])
        self.assertIs(False, manifest["upstream_content_redistributed"])
        self.assertEqual(16, len(pack.entries))
        self.assertEqual(16, coverage["card_count"])
        self.assertEqual(34, len(registry["sources"]))
        self.assertEqual(34, coverage["source_count"])

        source_ids: set[str] = set()
        source_urls: set[str] = set()
        for source in registry["sources"]:
            self.assertNotIn(source["id"], source_ids)
            self.assertNotIn(source["url"], source_urls)
            source_ids.add(source["id"])
            source_urls.add(source["url"])
            parsed = urlsplit(source["url"])
            self.assertEqual("https", parsed.scheme)
            self.assertTrue(parsed.hostname)
            self.assertTrue(source["author"])
            self.assertTrue(source["license_or_usage"])
            self.assertIsInstance(source["free"], bool)
            self.assertEqual(
                "community_unverified",
                source["verification"],
            )

        required_headings = (
            "## Use, prerequisites, and target",
            "## Semantic network stages",
            "## Ordered workflow",
            "## Executable parameter and connection contract",
            "## Data flow",
            "## Common failures and repairs",
            "## Checkpoints and observable evidence",
            "## When not to use this workflow",
            "## Provenance boundary",
            "## Semantic expectations and verification checklist",
            "## Sources",
        )
        declared_paths: set[str] = set()
        for entry in manifest["sources"]:
            expected_source_count = (
                4 if entry["id"] == "pyro-fields-cache-contract" else 2
            )
            self.assertEqual(expected_source_count, len(entry["source_ids"]))
            self.assertTrue(set(entry["source_ids"]).issubset(source_ids))
            self.assertEqual("community_unverified", entry["verification"])
            self.assertNotIn(entry["path"], declared_paths)
            declared_paths.add(entry["path"])
            body = (COMMUNITY_PACK / entry["path"]).read_text(encoding="utf-8")
            self.assertGreaterEqual(len(body.split()), 900, entry["id"])
            self.assertGreaterEqual(
                len(re.findall(r"(?m)^\d+\. ", body)),
                12,
                entry["id"],
            )
            for heading in required_headings:
                self.assertIn(heading, body, entry["id"])
            self.assertGreaterEqual(len(re.findall(r"`[^`\n]+`", body)), 12)
            self.assertEqual(
                5,
                len(re.findall(r"(?m)^- \*\*[A-Z][0-9] ", body)),
                entry["id"],
            )
            for term in EXECUTABLE_WORKFLOW_TERMS[entry["id"]]:
                self.assertIn(term, body, f"{entry['id']}: {term}")
            self.assertGreaterEqual(
                len(
                    re.findall(
                        r"\b(?:SOP|LOP|TOP|DOP|VOP|attribute|field|prim|"
                        r"layer|cache|solver|node)\b",
                        body,
                        flags=re.IGNORECASE,
                    )
                ),
                6,
                entry["id"],
            )
            self.assertNotIn("builtin_official_workflow", body)
            if entry["id"] == "pyro-fields-cache-contract":
                self.assertGreaterEqual(len(body.split()), 2500)
                for marker in (
                    "## EffectSpec and candidate decision contract",
                    "## Visual target to control-region map",
                    "Successful engineering evidence",
                    "A failed case is still useful",
                ):
                    self.assertIn(marker, body)
                for query in PYRO_EXPERIMENT_QUERIES:
                    self.assertIn(query, body)
                for query in PYRO_NOISE_QUERIES:
                    self.assertIn(query, body)

    def test_refresh_fts_filter_compact_and_full_reconstruction(self) -> None:
        _copy_pack(COMMUNITY_PACK, self.project_root)
        index = LocalKnowledgeIndex(self.project_root)
        stats = _refresh(index, force=True)
        self.assertEqual(16, stats["community_pack"]["cards"])
        self.assertEqual(16, stats["documents_added"])
        self.assertEqual(
            16,
            index.corpus_status()["community_pack"]["documents"],
        )

        store = HybridKnowledgeStore(
            self.project_root,
            index=index,
            embedder=FakeEmbedder(),
        )
        queries = (
            "curveu Sweep SOP frame",
            "density temperature sparse bounds",
            "narrow band particle separation",
            "localtransform retarget capture weights",
            "Copernicus premultiplication layer",
            "PDG dirty work item artifact",
            "onMouseEvent undo drawable",
        )
        results = store.search_many(
            queries,
            {"project"},
            current_houdini_version="21.0",
            offset=0,
            limit=10,
            mode="lexical",
            source_kinds={COMMUNITY_TUTORIAL_SOURCE},
        )
        for query, result in zip(queries, results):
            self.assertTrue(result["matches"], query)
            self.assertTrue(
                all(
                    match["source_kind"] == COMMUNITY_TUTORIAL_SOURCE
                    for match in result["matches"]
                )
            )

        official_only = store.search_many(
            ("onMouseEvent undo drawable",),
            {"project"},
            current_houdini_version="21.0",
            offset=0,
            limit=10,
            mode="lexical",
            source_kinds={"builtin_official_workflow"},
        )[0]
        self.assertEqual([], official_only["matches"])

        compact = store.search_many(
            ("viewer state",),
            {"project"},
            current_houdini_version="21.0",
            offset=0,
            limit=1,
            mode="lexical",
            source_kinds={COMMUNITY_TUTORIAL_SOURCE},
            card_id="viewer-state-interaction",
        )[0]["matches"][0]
        self.assertGreater(len(compact["metadata"]["summary"]), 80)
        self.assertRegex(
            compact["metadata"]["summary"],
            r"(?i)\b(?:keep|build|validate|inspect|connect|cache)\b",
        )
        self.assertEqual("Mohamad Salame; minami110", compact["metadata"]["author"])
        self.assertEqual("community_unverified", compact["metadata"]["verification"])

        document_id = int(compact["metadata"]["document_id"])
        full, truncated = index.document_content(document_id)
        self.assertFalse(truncated)
        ordered = (
            "# Viewer State interaction and undo boundaries",
            "## Semantic network stages",
            "## Ordered workflow",
            "1. Define the interaction contract",
            "8. Register/reload in a test session",
            "## Executable parameter and connection contract",
            "7. Test click, drag, no-hit",
            "## Checkpoints and observable evidence",
            "## When not to use this workflow",
            "## Semantic expectations and verification checklist",
            "## Sources",
        )
        positions = [full.index(marker) for marker in ordered]
        self.assertEqual(sorted(positions), positions)
        with closing(index._connect(read_only=True)) as connection:  # noqa: SLF001
            rows = connection.execute(
                "SELECT document_id, ordinal FROM chunks "
                "WHERE document_id = ? ORDER BY ordinal",
                (document_id,),
            ).fetchall()
        self.assertGreater(len(rows), 1)
        self.assertEqual(
            list(range(len(rows))),
            [int(row[1]) for row in rows],
        )
        self.assertEqual({document_id}, {int(row[0]) for row in rows})

    def test_pyro_queries_recall_in_lexical_partial_vector_and_hybrid(self) -> None:
        _copy_pack(COMMUNITY_PACK, self.project_root)
        index = LocalKnowledgeIndex(self.project_root)
        _refresh(index, force=True)
        store = HybridKnowledgeStore(
            self.project_root,
            index=index,
            embedder=FakeEmbedder(),
        )

        lexical_results = store.search_many(
            PYRO_RECALL_QUERIES,
            {"project"},
            current_houdini_version="21.0",
            offset=0,
            limit=5,
            mode="lexical",
            source_kinds={COMMUNITY_TUTORIAL_SOURCE},
        )
        for query, result in zip(PYRO_RECALL_QUERIES, lexical_results):
            pyro_matches = [
                match
                for match in result["matches"]
                if match["metadata"]["card_id"] == "pyro-fields-cache-contract"
            ]
            self.assertTrue(pyro_matches, f"lexical: {query}")
            self.assertEqual(
                COMMUNITY_TUTORIAL_SOURCE,
                pyro_matches[0]["source_kind"],
            )
            self.assertEqual(
                "community_unverified",
                pyro_matches[0]["metadata"]["verification"],
            )
            self.assertTrue(pyro_matches[0]["metadata"]["url"].startswith("https://"))
            self.assertIn("Attila Torok", pyro_matches[0]["metadata"]["author"])

        partial = store.search_many(
            (PYRO_EXPERIMENT_QUERIES[0],),
            {"project"},
            current_houdini_version="21.0",
            offset=0,
            limit=10,
            mode="hybrid",
            allow_index_updates=True,
            source_kinds={COMMUNITY_TUTORIAL_SOURCE},
        )[0]
        self.assertTrue(partial["retrieval"]["vector"]["index"]["partial"])
        partial_pyro = [
            match
            for match in partial["matches"]
            if match["metadata"]["card_id"] == "pyro-fields-cache-contract"
        ]
        self.assertTrue(partial_pyro)
        self.assertEqual(
            COMMUNITY_TUTORIAL_SOURCE,
            partial_pyro[0]["provenance"]["source_kind"],
        )
        self.assertEqual(
            "community_unverified",
            partial_pyro[0]["provenance"]["verification"],
        )
        self.assertIn("Attila Torok", partial_pyro[0]["provenance"]["author"])

        self.assertTrue(_build_all_vectors(store)["complete"])
        vector_results = store.search_many(
            PYRO_RECALL_QUERIES,
            {"project"},
            current_houdini_version="21.0",
            offset=0,
            limit=16,
            mode="vector",
            source_kinds={COMMUNITY_TUTORIAL_SOURCE},
        )
        hybrid_results = store.search_many(
            PYRO_RECALL_QUERIES,
            {"project"},
            current_houdini_version="21.0",
            offset=0,
            limit=5,
            mode="lexical",
            source_kinds={COMMUNITY_TUTORIAL_SOURCE},
        )
        for mode, results in (
            ("vector", vector_results),
            ("hybrid", hybrid_results),
        ):
            for query, result in zip(PYRO_RECALL_QUERIES, results):
                pyro_matches = [
                    match
                    for match in result["matches"]
                    if match["metadata"]["card_id"] == "pyro-fields-cache-contract"
                ]
                self.assertTrue(pyro_matches, f"{mode}: {query}")
                provenance = pyro_matches[0].get(
                    "provenance",
                    pyro_matches[0]["metadata"],
                )
                self.assertEqual(
                    COMMUNITY_TUTORIAL_SOURCE,
                    pyro_matches[0]["source_kind"],
                )
                self.assertEqual(
                    "community_unverified",
                    provenance["verification"],
                )
                self.assertTrue(provenance["url"].startswith("https://"))

    def test_fake_vector_partial_full_and_provenance_filters(self) -> None:
        _copy_pack(COMMUNITY_PACK, self.project_root)
        index = LocalKnowledgeIndex(self.project_root)
        _refresh(index, force=True)
        embedder = FakeEmbedder()
        store = HybridKnowledgeStore(
            self.project_root,
            index=index,
            embedder=embedder,
        )

        partial = store.search_many(
            ("onMouseEvent undo drawable",),
            {"project"},
            current_houdini_version="21.0",
            offset=0,
            limit=10,
            mode="hybrid",
            allow_index_updates=True,
            source_kinds={COMMUNITY_TUTORIAL_SOURCE},
        )[0]
        self.assertTrue(partial["retrieval"]["vector"]["index"]["partial"])
        self.assertTrue(partial["matches"])
        viewer_matches = [
            match
            for match in partial["matches"]
            if match["metadata"]["card_id"] == "viewer-state-interaction"
        ]
        self.assertTrue(viewer_matches)
        provenance = viewer_matches[0]["provenance"]
        self.assertEqual(COMMUNITY_TUTORIAL_SOURCE, provenance["source_kind"])
        self.assertEqual("Mohamad Salame; minami110", provenance["author"])
        self.assertTrue(provenance["url"].startswith("https://"))
        self.assertEqual("community_unverified", provenance["verification"])

        completed = _build_all_vectors(store)
        self.assertTrue(completed["complete"])
        vector = store.search_many(
            ("onMouseEvent undo drawable",),
            {"project"},
            current_houdini_version="21.0",
            offset=0,
            limit=16,
            mode="vector",
            source_kinds={COMMUNITY_TUTORIAL_SOURCE},
        )[0]
        self.assertEqual("vector", vector["retrieval"]["mode_used"])
        self.assertTrue(
            any(
                match["metadata"]["card_id"] == "viewer-state-interaction"
                for match in vector["matches"]
            )
        )
        self.assertTrue(
            all(
                match["source_kind"] == COMMUNITY_TUTORIAL_SOURCE
                for match in vector["matches"]
            )
        )

    def test_refresh_false_search_is_zero_write_and_update_delete_are_incremental(
        self,
    ) -> None:
        copied = _copy_pack(COMMUNITY_PACK, self.project_root)
        index = LocalKnowledgeIndex(self.project_root)
        _refresh(index, force=True)
        store = HybridKnowledgeStore(
            self.project_root,
            index=index,
            embedder=FakeEmbedder(),
        )
        with closing(index._connect(read_only=True)) as connection:  # noqa: SLF001
            before_vectors = int(
                connection.execute("SELECT COUNT(*) FROM chunk_vectors").fetchone()[0]
            )
        read_only_search = store.search_many(
            ("particle separation collision",),
            {"project"},
            current_houdini_version="21.0",
            offset=0,
            limit=10,
            mode="lexical",
            allow_index_updates=False,
            source_kinds={COMMUNITY_TUTORIAL_SOURCE},
        )[0]
        self.assertTrue(read_only_search["matches"])
        with closing(index._connect(read_only=True)) as connection:  # noqa: SLF001
            after_vectors = int(
                connection.execute("SELECT COUNT(*) FROM chunk_vectors").fetchone()[0]
            )
        self.assertEqual(before_vectors, after_vectors)

        self.assertTrue(_build_all_vectors(store)["complete"])
        changed_card = copied / "cards" / "cache-debug-evidence.md"
        changed_card.write_text(
            changed_card.read_text(encoding="utf-8")
            + "\nIncrementalRefreshNeedle proves a new final check.\n",
            encoding="utf-8",
        )
        updated = _refresh(index, force=False)
        self.assertEqual(["project"], updated["refresh_groups"])
        self.assertGreaterEqual(updated["documents_body_changed"], 1)
        self.assertTrue(store.status()["partial"])
        updated_hit = store.search_many(
            ("IncrementalRefreshNeedle",),
            {"project"},
            current_houdini_version="21.0",
            offset=0,
            limit=10,
            mode="lexical",
            source_kinds={COMMUNITY_TUTORIAL_SOURCE},
        )[0]
        self.assertEqual(
            "cache-debug-evidence",
            updated_hit["matches"][0]["metadata"]["card_id"],
        )

        manifest_path = copied / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["sources"] = [
            entry
            for entry in manifest["sources"]
            if entry["id"] != "cache-debug-evidence"
        ]
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        changed_card.unlink()
        removed = _refresh(index, force=False)
        self.assertEqual(1, removed["documents_removed"])
        self.assertEqual(
            (),
            index.filtered_document_ids(
                source_kinds={COMMUNITY_TUTORIAL_SOURCE},
                card_id="cache-debug-evidence",
            ),
        )
        self.assertEqual(
            15,
            index.corpus_status()["community_pack"]["documents"],
        )

    def test_hybrid_keeps_official_and_community_same_canonical_distinct(
        self,
    ) -> None:
        copied = _copy_pack(COMMUNITY_PACK, self.project_root)
        _copy_pack(OFFICIAL_PACK, self.project_root)
        manifest_path = copied / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for entry in manifest["sources"]:
            if entry["id"] == "cache-debug-evidence":
                entry["canonical_id"] = "cache-packaging-performance"
                break
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        index = LocalKnowledgeIndex(self.project_root)
        _refresh(index, force=True)
        store = HybridKnowledgeStore(
            self.project_root,
            index=index,
            embedder=FakeEmbedder(),
        )
        _build_all_vectors(store)
        result = store.search_many(
            ("File Cache versioning packed disk cache evidence",),
            {"project"},
            current_houdini_version="21.0",
            offset=0,
            limit=40,
            mode="hybrid",
        )[0]
        same_canonical = [
            match
            for match in result["matches"]
            if match["metadata"].get("canonical_id") == "cache-packaging-performance"
        ]
        self.assertEqual(
            {
                "builtin_official_workflow",
                COMMUNITY_TUTORIAL_SOURCE,
            },
            {match["source_kind"] for match in same_canonical},
        )

        official_only = store.search_many(
            ("File Cache versioning",),
            {"project"},
            current_houdini_version="21.0",
            offset=0,
            limit=20,
            mode="hybrid",
            source_kinds={"builtin_official_workflow"},
        )[0]
        self.assertTrue(official_only["matches"])
        self.assertTrue(
            all(
                match["source_kind"] == "builtin_official_workflow"
                for match in official_only["matches"]
            )
        )

    def test_release_allowlist_and_packaging_checker_cover_community(self) -> None:
        build_script = (REPOSITORY_ROOT / "scripts" / "build-release.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("'knowledge/community-tutorials/'", build_script)

        checker = _load_release_checker()
        tracked = [
            path.relative_to(REPOSITORY_ROOT).as_posix()
            for path in COMMUNITY_PACK.rglob("*")
            if path.is_file()
        ]
        self.assertEqual(
            [],
            checker.inspect_knowledge_source(
                REPOSITORY_ROOT,
                tracked,
                knowledge_pack_root=PurePosixPath("knowledge/community-tutorials"),
            ),
        )

        archive_path = Path(self._temporary.name) / "knowledge-release.zip"
        with zipfile.ZipFile(
            archive_path,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
        ) as archive:
            for pack_root in (OFFICIAL_PACK, COMMUNITY_PACK):
                for path in pack_root.rglob("*"):
                    if path.is_file():
                        archive.write(
                            path,
                            path.relative_to(REPOSITORY_ROOT).as_posix(),
                        )
        self.assertEqual([], checker.inspect_release(archive_path))


if __name__ == "__main__":
    unittest.main()
