from __future__ import annotations

import difflib
import hashlib
import json
import re
import unittest
from itertools import combinations
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PACK_ROOT = REPOSITORY_ROOT / "knowledge" / "sidefx-official"
MANIFEST_PATH = PACK_ROOT / "manifest.json"
SOURCES_PATH = PACK_ROOT / "sources.json"
COVERAGE_PATH = PACK_ROOT / "coverage.json"
CARDS_ROOT = PACK_ROOT / "cards"

ALLOWED_ACCESS_DATES = {"2026-07-26", "2026-07-27"}
PACK_VERSION = "2.0.0"
MINIMUM_WORDS = 450
MINIMUM_CHARACTERS = 2500
MINIMUM_WORKFLOW_STEPS = 5
MINIMUM_COVERAGE_WORKFLOWS = 36
REQUIRED_SECTIONS = (
    "Use for",
    "Context and core data",
    "Recommended data flow",
    "Step-by-step workflow",
    "Critical parameters and attributes",
    "Cache, version, and performance",
    "Validation",
    "Common failures and troubleshooting",
    "When not to use",
    "Houdini 21 notes",
    "Official sources",
)
FORBIDDEN_EXAMPLE_ASSETS = (
    "vending machine",
    "wood cabin",
    "wooden table",
    "staircase",
    "售货机",
    "木屋",
    "桌子",
    "楼梯",
)
REQUIRED_COVERAGE_DOMAINS = {
    "SOP procedural modeling and attributes": ("sop", "procedural", "attribute"),
    "VEX and Wrangle": ("vex", "wrangle"),
    "For-Each, compiled blocks, and Copy to Points": (
        "foreach",
        "compile",
        "copy",
    ),
    "UV and coordinate workflows": ("uv",),
    "MaterialX and Karma": ("materialx", "karma"),
    "Solaris, USD, and LOPs": ("solaris", "usd", "lop"),
    "lighting, cameras, rendering, and AOVs": (
        "lighting",
        "camera",
        "render",
        "aov",
    ),
    "RBD": ("rbd",),
    "Vellum": ("vellum",),
    "Pyro": ("pyro",),
    "FLIP and oceans": ("flip", "ocean"),
    "height fields": ("heightfield",),
    "KineFX, animation, and CHOPs": ("kinefx", "animation", "chop"),
    "crowds": ("crowd",),
    "TOPs and PDG": ("top", "pdg"),
    "Copernicus and COPs": ("copernicus", "cop"),
    "HDA interfaces and versioning": ("hda", "interface", "version"),
    "HOM and debugging": ("hom", "debug"),
    "Groom and hair": ("groom", "hair"),
    "FEM and MPM": ("fem", "mpm"),
    "HDK and package distribution": ("hdk", "package"),
    "cache, packaging, performance, and troubleshooting": (
        "cache",
        "packag",
        "performance",
        "troubleshoot",
    ),
}
REQUIRED_WORKFLOW_IDS = {
    "sop-procedural-modeling",
    "sop-attribute-design",
    "vex-wrangle-authoring",
    "foreach-loop-processing",
    "compiled-block-optimization",
    "copy-to-points-instancing",
    "uv-unwrapping-and-layout",
    "uv-transfer-and-coordinate-selection",
    "materialx-karma-authoring",
    "materialx-textures-and-primvars",
    "karma-cpu-xpu-validation",
    "usd-layer-composition",
    "solaris-asset-assembly",
    "solaris-instancing-payload-performance",
    "lighting-setup",
    "camera-setup",
    "karma-render-configuration",
    "render-products-and-aovs",
    "rbd-fracture-and-pack",
    "rbd-constraints-and-simulation",
    "vellum-constraints-and-simulation",
    "pyro-source-solve-cache-render",
    "flip-sourcing-and-simulation",
    "ocean-spectrum-and-rendering",
    "heightfield-terrain-authoring",
    "kinefx-rigging-and-deformation",
    "animation-keyframes-and-channels",
    "chops-motion-processing",
    "crowd-agent-preparation",
    "crowd-simulation-and-rendering",
    "pdg-work-item-dependencies",
    "pdg-cache-and-farm-execution",
    "copernicus-image-processing",
    "copernicus-texture-authoring",
    "hda-interface-authoring",
    "hda-versioning-and-upgrades",
    "hom-scene-authoring",
    "hom-debugging-and-errors",
    "cache-versioning-and-resume",
    "project-packaging-and-portability",
    "performance-profiling",
    "cross-domain-troubleshooting",
    "curve-nurbs-subdivision",
    "opencl-sop-compiled-dop",
    "groom-guides-uv-transfer",
    "fem-solids-constraints",
    "mpm-material-simulation",
    "copernicus-pyro-cop2-migration",
    "python-viewer-state",
    "hdk-package-distribution",
}


def _read_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"{path} must contain a JSON object")
    return value


def _records(
    document: dict[str, object], key: str, path: Path
) -> list[dict[str, object]]:
    value = document.get(key)
    if not isinstance(value, list) or not value:
        raise AssertionError(f"{path} must contain a non-empty {key!r} array")
    if not all(isinstance(item, dict) for item in value):
        raise AssertionError(f"Every {key!r} item in {path} must be an object")
    return value


def _unique_strings(
    records: list[dict[str, object]], field: str, description: str
) -> set[str]:
    values = [item.get(field) for item in records]
    if not all(isinstance(value, str) and value.strip() for value in values):
        raise AssertionError(f"Every {description} must have a non-empty {field!r}")
    strings = {str(value) for value in values}
    if len(strings) != len(values):
        raise AssertionError(f"{description} {field!r} values must be unique")
    return strings


def _normalized_terms(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _word_set(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z][a-z0-9_-]{2,}", value.casefold())
        if token
        not in {
            "and",
            "are",
            "for",
            "from",
            "into",
            "not",
            "the",
            "this",
            "that",
            "use",
            "when",
            "with",
        }
    }


class SideFxOfficialKnowledgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = _read_json(MANIFEST_PATH)
        cls.source_registry = _read_json(SOURCES_PATH)
        cls.coverage = _read_json(COVERAGE_PATH)
        cls.cards = _records(cls.manifest, "sources", MANIFEST_PATH)
        cls.official_sources = _records(
            cls.source_registry, "sources", SOURCES_PATH
        )
        cls.domains = _records(cls.coverage, "domains", COVERAGE_PATH)
        cls.workflows = _records(
            cls.coverage, "workflows", COVERAGE_PATH
        )

    def test_pack_version_is_consistent_across_metadata_and_cards(self) -> None:
        for path, document in (
            (MANIFEST_PATH, self.manifest),
            (SOURCES_PATH, self.source_registry),
            (COVERAGE_PATH, self.coverage),
        ):
            self.assertEqual(
                PACK_VERSION,
                document.get("pack_version"),
                f"{path} must declare pack_version {PACK_VERSION}",
            )
            self.assertNotIn(
                "\ufffd",
                json.dumps(document, ensure_ascii=False),
                f"{path} contains a Unicode replacement character",
            )

        marker = f"Pack version: {PACK_VERSION}"
        for card in self.cards:
            text = (PACK_ROOT / str(card["path"])).read_text(
                encoding="utf-8"
            )
            self.assertIn(marker, text, f"{card['id']} has no pack version")

    def test_evidence_status_never_implies_live_houdini_validation(self) -> None:
        readme = (PACK_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertEqual("documented/static", self.coverage.get("evidence_status"))
        self.assertEqual("not-run", self.coverage.get("live_validation"))
        self.assertIn("neither status means live-verified", str(
            self.coverage.get("status_semantics") or ""
        ))
        boundary = str(self.coverage.get("validation_boundary") or "")
        self.assertIn("no live Houdini scene", boundary)
        for label in (
            "documented/static",
            "change-note-supported",
            "live-verified",
        ):
            self.assertIn(label, readme)
        self.assertIn("No card in this rebuild", readme)
        self.assertIn("test recipes and expected evidence", readme)
        for card_name in (
            "pdg-partition-scheduler-cache-debug.md",
            "pyro-intro.md",
            "pyro-cache-shade-render.md",
            "vellum-softbody-grains.md",
        ):
            with self.subTest(provenance=card_name):
                text = (CARDS_ROOT / card_name).read_text(encoding="utf-8")
                self.assertIn("documented/static", text)
                self.assertIn("change-note-supported", text)
                self.assertRegex(
                    text,
                    r"(?:did not live-verify|did not run an H21)",
                )

    def test_manifest_card_inventory_paths_and_source_ids_are_consistent(
        self,
    ) -> None:
        card_ids = _unique_strings(self.cards, "id", "manifest card")
        card_paths = _unique_strings(self.cards, "path", "manifest card")
        _unique_strings(self.cards, "title", "manifest card")
        source_ids = _unique_strings(
            self.official_sources, "id", "official source"
        )
        source_urls = {
            str(source["id"]): str(source.get("url") or "")
            for source in self.official_sources
        }

        discovered_paths = {
            path.relative_to(PACK_ROOT).as_posix()
            for path in CARDS_ROOT.glob("*.md")
        }
        self.assertEqual(card_paths, discovered_paths)
        for card in self.cards:
            for field in ("id", "title", "path"):
                self.assertNotIn(
                    "\ufffd",
                    str(card[field]),
                    f"{card['id']} has corrupt {field} metadata",
                )
            relative_path = Path(str(card["path"]))
            resolved_path = (PACK_ROOT / relative_path).resolve()
            self.assertTrue(
                resolved_path.is_relative_to(PACK_ROOT.resolve()),
                f"Card path escapes the pack: {relative_path}",
            )
            self.assertTrue(resolved_path.is_file())
            references = card.get("source_ids")
            self.assertIsInstance(references, list)
            self.assertGreaterEqual(len(references), 2)
            self.assertTrue(all(isinstance(item, str) for item in references))
            self.assertTrue(
                set(references).issubset(source_ids),
                f"{card['id']} contains an unknown source_id",
            )
            referenced_urls = {source_urls[str(item)] for item in references}
            card_text = resolved_path.read_text(encoding="utf-8")
            self.assertTrue(
                all(url in card_text for url in referenced_urls),
                f"{card['id']} does not cite every registered source it references",
            )
            self.assertIn(card.get("url"), referenced_urls)
            self.assertRegex(str(card.get("houdini_version") or ""), r"^21(?:\.|$)")

        self.assertEqual(len(card_ids), len(self.cards))

    def test_card_registry_and_coverage_sources_are_bidirectionally_traceable(
        self,
    ) -> None:
        source_by_url = {
            str(source["url"]): source
            for source in self.official_sources
        }
        registry_urls = set(source_by_url)
        card_source_ids: dict[str, set[str]] = {}
        card_urls_union: set[str] = set()
        for card in self.cards:
            text = (PACK_ROOT / str(card["path"])).read_text(
                encoding="utf-8"
            )
            urls = set(re.findall(
                r"https://(?:www|media)\.sidefx\.com/[^\s)>]+",
                text,
            ))
            card_urls_union.update(urls)
            expected_ids = {
                str(source_by_url[url]["id"])
                for url in urls
            }
            actual_ids = {str(value) for value in card["source_ids"]}
            self.assertEqual(
                expected_ids,
                actual_ids,
                f"{card['id']} manifest source_ids must match every card URL",
            )
            for url in urls:
                accessed = str(source_by_url[url]["accessed"])
                self.assertRegex(
                    text,
                    rf"{re.escape(url)}[^\n]*accessed {re.escape(accessed)}",
                    f"{card['id']} must preserve the registry access date for {url}",
                )
            card_source_ids[str(card["id"])] = actual_ids

        self.assertEqual(
            registry_urls,
            card_urls_union,
            "sources.json must be the exact deduplicated URL registry for cards",
        )

        for collection_name, records in (
            ("domain", self.domains),
            ("workflow", self.workflows),
        ):
            for record in records:
                referenced_by_cards: set[str] = set()
                for card_id in record["card_ids"]:
                    referenced_by_cards.update(card_source_ids[str(card_id)])
                self.assertTrue(
                    set(record["source_ids"]).issubset(referenced_by_cards),
                    f"{collection_name} {record['id']} cites evidence absent "
                    "from its linked cards",
                )

    def test_official_source_registry_is_sidefx_only_and_dated(self) -> None:
        _unique_strings(self.official_sources, "id", "official source")
        for source in self.official_sources:
            url = source.get("url")
            self.assertIsInstance(url, str)
            self.assertRegex(
                url,
                r"^https://(?:www|media)\.sidefx\.com/",
                f"Non-SideFX source in registry: {source.get('id')}",
            )
            accessed = (
                source.get("accessed")
                or source.get("accessed_date")
                or source.get("accessed_on")
            )
            self.assertIn(accessed, ALLOWED_ACCESS_DATES)
            expected_id = "src-" + hashlib.sha256(
                url.encode("utf-8")
            ).hexdigest()[:16]
            self.assertEqual(
                expected_id,
                source.get("id"),
                f"Source ID must be derived from its exact URL: {url}",
            )

    def test_cards_have_substantive_workflow_structure(self) -> None:
        for card in self.cards:
            with self.subTest(card=card["id"]):
                text = (PACK_ROOT / str(card["path"])).read_text(
                    encoding="utf-8"
                )
                words = re.findall(r"\b[\w'-]+\b", text, flags=re.UNICODE)
                self.assertGreaterEqual(
                    len(text.strip()),
                    MINIMUM_CHARACTERS,
                    f"{card['id']} is shorter than {MINIMUM_CHARACTERS} characters",
                )
                self.assertGreaterEqual(
                    len(words),
                    MINIMUM_WORDS,
                    f"{card['id']} is only {len(words)} words",
                )
                for section in REQUIRED_SECTIONS:
                    self.assertRegex(
                        text,
                        rf"(?m)^## {re.escape(section)}\s*$",
                        f"{card['id']} is missing section {section!r}",
                    )

                workflow_match = re.search(
                    r"(?ms)^## Step-by-step workflow\s*$"
                    r"(.*?)"
                    r"^## Critical parameters and attributes\s*$",
                    text,
                )
                self.assertIsNotNone(workflow_match)
                numbered_steps = re.findall(
                    r"(?m)^\s*\d+\.\s+\S+", workflow_match.group(1)
                )
                self.assertGreaterEqual(
                    len(numbered_steps),
                    MINIMUM_WORKFLOW_STEPS,
                    f"{card['id']} needs at least five ordered workflow steps",
                )

                official_urls = set(
                    re.findall(
                        r"https://(?:www|media)\.sidefx\.com/[^\s)>]+",
                        text,
                    )
                )
                self.assertGreaterEqual(len(official_urls), 2)
                self.assertRegex(
                    text,
                    r"accessed 2026-07-(?:26|27)",
                )
                self.assertNotIn("\ufffd", text)
                self.assertNotRegex(text, r"(?i)\bTODO\b")
                folded = text.casefold()
                for asset in FORBIDDEN_EXAMPLE_ASSETS:
                    self.assertNotIn(asset.casefold(), folded)

    def test_coverage_matrix_resolves_cards_sources_and_required_domains(
        self,
    ) -> None:
        card_ids = {str(card["id"]) for card in self.cards}
        source_ids = {str(source["id"]) for source in self.official_sources}
        domain_texts: list[str] = []
        covered_cards: set[str] = set()

        _unique_strings(self.domains, "id", "coverage domain")
        for domain in self.domains:
            status = domain.get("status")
            references = domain.get("source_ids")
            domain_cards = domain.get("card_ids")
            self.assertIsInstance(status, str)
            self.assertTrue(status.strip())
            self.assertIsInstance(references, list)
            self.assertTrue(references)
            self.assertTrue(set(references).issubset(source_ids))
            self.assertIsInstance(domain_cards, list)
            self.assertTrue(domain_cards)
            self.assertTrue(set(domain_cards).issubset(card_ids))
            covered_cards.update(str(value) for value in domain_cards)
            self.assertIn("remaining_gaps", domain)
            self.assertIsInstance(domain["remaining_gaps"], list)
            domain_texts.append(
                _normalized_terms(
                    " ".join(
                        (
                            str(domain.get("id") or ""),
                            str(domain.get("title") or ""),
                            " ".join(
                                str(item)
                                for item in domain.get("aliases", [])
                            ),
                        )
                    )
                )
            )

        self.assertEqual(card_ids, covered_cards)
        for label, terms in REQUIRED_COVERAGE_DOMAINS.items():
            self.assertTrue(
                any(
                    all(_normalized_terms(term) in domain for term in terms)
                    for domain in domain_texts
                ),
                f"Coverage matrix is missing {label}",
            )

    def test_canonical_workflow_inventory_is_complete_and_resolvable(
        self,
    ) -> None:
        card_ids = {str(card["id"]) for card in self.cards}
        source_ids = {str(source["id"]) for source in self.official_sources}
        workflow_ids = _unique_strings(
            self.workflows, "id", "coverage workflow"
        )
        self.assertGreaterEqual(
            len(self.workflows),
            MINIMUM_COVERAGE_WORKFLOWS,
        )
        self.assertTrue(
            REQUIRED_WORKFLOW_IDS.issubset(workflow_ids),
            "Missing canonical workflows: "
            + ", ".join(sorted(REQUIRED_WORKFLOW_IDS - workflow_ids)),
        )
        partial_ids = {
            str(workflow["id"])
            for workflow in self.workflows
            if workflow.get("status") == "partial"
        }
        self.assertEqual(
            {"project-packaging-and-portability"},
            partial_ids,
        )
        self.assertEqual(49, sum(
            workflow.get("status") == "covered"
            for workflow in self.workflows
        ))

        for workflow in self.workflows:
            with self.subTest(workflow=workflow["id"]):
                for field in (
                    "id",
                    "title",
                    "status",
                    "card_ids",
                    "source_ids",
                    "remaining_gaps",
                ):
                    self.assertIn(field, workflow)
                self.assertIsInstance(workflow["title"], str)
                self.assertTrue(workflow["title"].strip())
                self.assertIn(workflow["status"], {"covered", "partial"})
                self.assertIsInstance(workflow["card_ids"], list)
                self.assertTrue(workflow["card_ids"])
                self.assertTrue(set(workflow["card_ids"]).issubset(card_ids))
                self.assertIsInstance(workflow["source_ids"], list)
                self.assertTrue(workflow["source_ids"])
                self.assertTrue(
                    set(workflow["source_ids"]).issubset(source_ids)
                )
                self.assertIsInstance(workflow["remaining_gaps"], list)

    def test_high_risk_workflows_cite_their_direct_official_evidence(
        self,
    ) -> None:
        source_by_url = {
            str(source["url"]): str(source["id"])
            for source in self.official_sources
        }
        cards_by_id = {str(card["id"]): card for card in self.cards}
        workflows_by_id = {
            str(workflow["id"]): workflow for workflow in self.workflows
        }
        expected = {
            "vex-wrangle-authoring": (
                "vex-neighbor-volume-debug",
                "https://www.sidefx.com/docs/houdini/nodes/sop/volumewrangle.html",
            ),
            "uv-transfer-and-coordinate-selection": (
                "uv-workflows",
                "https://www.sidefx.com/docs/houdini/nodes/sop/uvflattenfrompoints.html",
            ),
            "copy-to-points-instancing": (
                "pack-copy-instances",
                "https://www.sidefx.com/docs/houdini/nodes/sop/pack",
            ),
            "solaris-asset-assembly": (
                "solaris-scene-import-layout",
                "https://www.sidefx.com/docs/houdini/nodes/lop/layerbreak.html",
            ),
            "render-products-and-aovs": (
                "lighting-camera-aov",
                "https://www.sidefx.com/docs/houdini/nodes/lop/karmarenderproducts.html",
            ),
            "rbd-fracture-and-pack": (
                "rbd-fracture-name-pack",
                "https://www.sidefx.com/docs/houdini/nodes/sop/rbdunpack.html",
            ),
            "rbd-constraints-and-simulation": (
                "rbd-constraints-bullet-cache",
                "https://www.sidefx.com/docs/houdini/dyno/cache",
            ),
            "vellum-constraints-and-simulation": (
                "vellum-overview",
                "https://www.sidefx.com/docs/houdini/nodes/sop/vellumconstraints",
            ),
            "pyro-source-solve-cache-render": (
                "pyro-cache-shade-render",
                "https://www.sidefx.com/docs/houdini/nodes/lop/volume.html",
            ),
            "flip-sourcing-and-simulation": (
                "flip-mesh-whitewater-cache",
                "https://www.sidefx.com/docs/houdini/nodes/sop/whitewaterpostprocess.html",
            ),
            "ocean-spectrum-and-rendering": (
                "ocean-spectrum-evaluate-render",
                "https://www.sidefx.com/docs/houdini/nodes/lop/karmaocean.html",
            ),
            "kinefx-rigging-and-deformation": (
                "kinefx-rig-capture",
                "https://www.sidefx.com/docs/houdini/nodes/sop/kinefx--jointdeform.html",
            ),
            "animation-keyframes-and-channels": (
                "animation-keyframes-curves",
                "https://www.sidefx.com/docs/houdini/anim/keyframes.html",
            ),
            "crowd-simulation-and-rendering": (
                "crowds-transition-render",
                "https://www.sidefx.com/docs/houdini/nodes/dop/crowdtransition.html",
            ),
            "pdg-work-item-dependencies": (
                "pdg-tops",
                "https://www.sidefx.com/docs/houdini/tops/attributes.html",
            ),
            "copernicus-texture-authoring": (
                "copernicus-feedback-texture-output",
                "https://www.sidefx.com/docs/houdini/nodes/cop/bakegeometrytextures.html",
            ),
            "curve-nurbs-subdivision": (
                "curve-nurbs-subdivision",
                "https://www.sidefx.com/docs/houdini/nodes/sop/subdivide.html",
            ),
            "opencl-sop-compiled-dop": (
                "opencl-sop-compiled-dop",
                "https://www.sidefx.com/docs/houdini/nodes/dop/sopsolver.html",
            ),
            "groom-guides-uv-transfer": (
                "groom-guides-uv-transfer",
                "https://www.sidefx.com/docs/houdini/nodes/sop/guidetransfer.html",
            ),
            "fem-solids-constraints": (
                "fem-solids-constraints",
                "https://www.sidefx.com/docs/houdini/finiteelements/geometry.html",
            ),
            "mpm-material-simulation": (
                "mpm-material-simulation",
                "https://www.sidefx.com/docs/houdini/nodes/sop/mpmsolver.html",
            ),
            "copernicus-pyro-cop2-migration": (
                "copernicus-pyro-cop2-migration",
                "https://www.sidefx.com/docs/houdini/news/21/pyro.html",
            ),
            "python-viewer-state": (
                "python-viewer-state",
                "https://www.sidefx.com/docs/houdini/hom/python_states.html",
            ),
            "hdk-package-distribution": (
                "hdk-package-distribution",
                "https://www.sidefx.com/docs/hdk/_h_d_k__intro__compatibility.html",
            ),
        }
        for workflow_id, (card_id, url) in expected.items():
            with self.subTest(workflow=workflow_id):
                source_id = source_by_url[url]
                self.assertIn(
                    source_id,
                    workflows_by_id[workflow_id]["source_ids"],
                )
                self.assertIn(source_id, cards_by_id[card_id]["source_ids"])

        copy_source_id = source_by_url[
            "https://www.sidefx.com/docs/houdini/nodes/sop/copytopoints"
        ]
        self.assertIn(
            copy_source_id,
            workflows_by_id["copy-to-points-instancing"]["source_ids"],
        )

    def test_new_high_value_workflows_preserve_execution_and_version_boundaries(
        self,
    ) -> None:
        cards = {
            path.stem: path.read_text(encoding="utf-8")
            for path in (
                CARDS_ROOT / "curve-nurbs-subdivision.md",
                CARDS_ROOT / "opencl-sop-compiled-dop.md",
                CARDS_ROOT / "groom-guides-uv-transfer.md",
                CARDS_ROOT / "fem-solids-constraints.md",
                CARDS_ROOT / "mpm-material-simulation.md",
                CARDS_ROOT / "copernicus-pyro-cop2-migration.md",
                CARDS_ROOT / "python-viewer-state.md",
                CARDS_ROOT / "hdk-package-distribution.md",
            )
        }

        opencl = cards["opencl-sop-compiled-dop"]
        self.assertIn("Invoke Compiled Block", opencl)
        self.assertIn("arbitrary DOP network", opencl)
        self.assertIn("works for Geometry", opencl)
        self.assertIn("not scalar, vector, or matrix fields", opencl)

        groom = cards["groom-guides-uv-transfer"]
        for term in ("Guide Transfer", "Direct mode", "UV", "`guideorigin`"):
            self.assertIn(term, groom)
        self.assertRegex(
            groom,
            r"(?s)Houdini 22 introduced.*?Do not silently use those nodes "
            r"as the Houdini 21 recipe",
        )

        fem = cards["fem-solids-constraints"]
        for term in ("Tet Embed", "FEM Validate", "FEM Solver"):
            self.assertIn(term, fem)

        mpm = cards["mpm-material-simulation"]
        for term in (
            "MPM Container",
            "MPM Source",
            "MPM Collider",
            "MPM Solver",
        ):
            self.assertIn(term, mpm)
        self.assertRegex(
            mpm,
            r"MPM Solver inputs \(sources, colliders, container\)",
        )

        cop = cards["copernicus-pyro-cop2-migration"]
        self.assertIn("Pyro Block Begin 1.0", cop)
        self.assertIn("Pyro Block 2.0", cop)
        self.assertIn("COP Network - Old", cop)
        self.assertIn("node-for-node", cop)

        viewer = cards["python-viewer-state"]
        for term in (
            "createViewerStateTemplate()",
            "beginStateUndo()",
            "Viewer State Browser",
        ):
            self.assertIn(term, viewer)

        hdk = cards["hdk-package-distribution"]
        for term in (
            "`hcustom`",
            "`HDK_API_VERSION`",
            "`HOUDINI_PACKAGE_VERBOSE`",
            "`HOUDINI_DSO_ERROR`",
        ):
            self.assertIn(term, hdk)

        for card in cards.values():
            self.assertIn("documented/static", card)
            self.assertRegex(
                card,
                r"(?:not(?: been)? live-verified|not live H21)",
            )

    def test_traditional_animation_workflow_is_complete_and_precise(
        self,
    ) -> None:
        card_path = CARDS_ROOT / "animation-keyframes-curves.md"
        card_text = card_path.read_text(encoding="utf-8")
        card_text_lower = card_text.casefold()
        workflows_by_id = {
            str(workflow["id"]): workflow for workflow in self.workflows
        }

        for editor_term in (
            "Animation Editor",
            "Graph",
            "Dope Sheet",
            "Channel List",
        ):
            self.assertIn(editor_term, card_text)
        for curve_term in (
            "**Constant**",
            "**Linear**",
            "**Bezier**",
            "**Break Slopes**",
            "**Tie Slopes**",
            "`hou.Keyframe`",
            "`hou.parmExtrapolate`",
        ):
            self.assertIn(curve_term, card_text)
        self.assertRegex(
            card_text,
            r"“break” is not\s+another interpolation type",
        )
        for delivery_term in (
            "extrapolation",
            "cycle",
            "simplify",
            "reduce",
            "bake",
            "export",
        ):
            self.assertIn(delivery_term, card_text_lower)

        animation = workflows_by_id["animation-keyframes-and-channels"]
        self.assertEqual("covered", animation["status"])
        self.assertIn("animation-keyframes-curves", animation["card_ids"])
        self.assertEqual([], animation["remaining_gaps"])

        packaging = workflows_by_id["project-packaging-and-portability"]
        self.assertEqual("partial", packaging["status"])
        packaging_gap = " ".join(packaging["remaining_gaps"]).casefold()
        self.assertIn("foundations are covered", packaging_gap)
        self.assertIn("target-environment validation boundaries", packaging_gap)
        self.assertIn("rather than missing sidefx fundamentals", packaging_gap)

    def test_blocker_facts_and_minimal_reproductions_are_explicit(self) -> None:
        card_text = {
            str(card["id"]): (
                PACK_ROOT / str(card["path"])
            ).read_text(encoding="utf-8")
            for card in self.cards
        }
        volume = card_text["vex-neighbor-volume-debug"]
        self.assertIn("`@P` is the current voxel center", volume)
        self.assertIn(
            "`@center` is the center of the whole volume in SOP space",
            volume,
        )
        self.assertNotRegex(
            volume,
            r"`@center`\s+is the current voxel center",
        )
        self.assertIn("@density = length(@P - @center)", volume)

        sop = card_text["sop-procedural-attributes"]
        self.assertIn("Grid (Rows 3, Columns 4)", sop)
        self.assertIn("12 members in `tagged`", sop)

        vex = card_text["vex-snippets"]
        self.assertIn("i@group_right = @P.x > 0", vex)
        self.assertIn("two points, one polyline primitive", vex)

        hom = card_text["hom-debugging"]
        self.assertIn("node.updateParmStates()", hom)
        self.assertIn("parm.isHidden()", hom)
        self.assertIn("parm.isDisabled()", hom)
        self.assertIn('geo.createNode("grid", "SOURCE_GRID")', hom)
        self.assertIn("an empty `errors()` tuple", hom)

    def test_solaris_materialx_and_karma_support_boundaries_are_explicit(
        self,
    ) -> None:
        scene_import = (CARDS_ROOT / "solaris-scene-import-layout.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("Layer Break before new-layer opinions", scene_import)
        self.assertIn("**Start New Layer**", scene_import)
        self.assertRegex(
            scene_import,
            r"(?s)Do not treat it as interchangeable with Layer Break.*?"
            r"\*\*Start New Layer\*\*",
        )

        materialx = (CARDS_ROOT / "materialx-solaris.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("set Signature to Vector3 before MtlX Normalmap", materialx)
        self.assertIn(
            "it is metadata that Hydra currently does not pass to",
            materialx,
        )
        self.assertIn("the render delegate", materialx)

        advanced_materialx = (
            CARDS_ROOT / "materialx-layers-displacement-volume.md"
        ).read_text(encoding="utf-8")
        xpu = (CARDS_ROOT / "karma-xpu.md").read_text(encoding="utf-8")
        for text in (advanced_materialx, xpu):
            self.assertRegex(
                text,
                r"(?s)Standard Surface.*?OpenPBR.*?(?:float/scalar|scalar)",
            )
            self.assertIn("MaterialX VDF/EDF", text)
            for supported_route in (
                "Karma Volume",
                "XPU Pyro Preview",
                "Karma Whitewater",
            ):
                self.assertIn(supported_route, text)

    def test_karma_sampling_and_usd_instancing_contracts_are_explicit(
        self,
    ) -> None:
        sampling = (
            CARDS_ROOT / "karma-sampling-aov-denoise.md"
        ).read_text(encoding="utf-8")
        self.assertIn("Primary Samples", sampling)
        self.assertIn("Path Traced Samples", sampling)
        self.assertIn("Advanced ▸ Sampling ▸ Primary Samples", sampling)
        self.assertRegex(
            sampling,
            r"(?s)space-separated list to \*\*Planes\*\*.*?"
            r"beauty `C`.*?required plane",
        )
        self.assertIn(
            "CPU Automatic-convergence diagnosis cannot be copied directly",
            sampling,
        )

        usd = (CARDS_ROOT / "usd-basics.md").read_text(encoding="utf-8")
        self.assertRegex(
            usd,
            r"(?s)instanceable root.*?per-instance transform.*?"
            r"constant primvars",
        )
        self.assertRegex(
            usd,
            r"descendant\s+instance proxies cannot be edited directly",
        )

    def test_rbd_flip_ocean_and_checkpoint_contracts_are_explicit(self) -> None:
        fracture = (
            CARDS_ROOT / "rbd-fracture-name-pack.md"
        ).read_text(encoding="utf-8")
        self.assertRegex(
            fracture,
            r"(?s)RBD Unpack.*?output 1.*?input 1.*?"
            r"output 2.*?input 2.*?output 3.*?input 3",
        )
        self.assertIn(
            "A single RBD Pack output wired only to solver input 1 does not "
            "satisfy the",
            fracture,
        )

        constraints = (
            CARDS_ROOT / "rbd-constraints-bullet-cache.md"
        ).read_text(encoding="utf-8")
        self.assertIn("endpoint point string `name`", constraints)
        self.assertIn("primitive string `constraint_name`", constraints)

        whitewater = (
            CARDS_ROOT / "flip-mesh-whitewater-cache.md"
        ).read_text(encoding="utf-8")
        self.assertRegex(
            whitewater,
            r"(?s)Whitewater Source.*?Whitewater Solver.*?"
            r"Whitewater Post-Process.*?File Cache",
        )
        self.assertIn(
            "Restore the Source → Solver → Post-Process → File Cache",
            whitewater,
        )

        ocean = (
            CARDS_ROOT / "ocean-spectrum-evaluate-render.md"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "Ocean LOP renders this ocean with Karma CPU; it is not an XPU "
            "render route.",
            ocean,
        )
        self.assertRegex(
            ocean,
            r"(?s)Karma Ocean.*?LOP's CPU-only boundary",
        )

        checkpoint_cards = (
            "rbd-constraints-bullet-cache.md",
            "flip-source-collision-narrowband.md",
            "flip-mesh-whitewater-cache.md",
            "pyro-cache-shade-render.md",
        )
        for card_name in checkpoint_cards:
            with self.subTest(checkpoints=card_name):
                text = (CARDS_ROOT / card_name).read_text(encoding="utf-8")
                for parameter in (
                    "Save Checkpoints",
                    "Base Folder",
                    "Version",
                    "Trail Length",
                    "Interval",
                ):
                    self.assertIn(parameter, text)
                self.assertRegex(
                    text,
                    r"(?s)(?:not automatically deleted|"
                    r"not deleted automatically|invalidation does not delete)",
                )
                self.assertRegex(
                    text,
                    r"(?s)(?:advance|increment).*?Version|"
                    r"Version or use a new",
                )

    def test_animation_cop_pdg_and_pyro_execution_contracts_are_explicit(
        self,
    ) -> None:
        capture = (CARDS_ROOT / "kinefx-rig-capture.md").read_text(
            encoding="utf-8"
        )
        for method in (
            "Joint Capture Biharmonic",
            "Joint Capture Proximity",
            "Joint Capture Paint",
        ):
            self.assertIn(method, capture)
        self.assertIn("`boneCapture`", capture)
        self.assertRegex(
            capture,
            r"(?s)captured rest geometry to Joint Deform input\s+1.*?"
            r"capture pose to input\s+2.*?animated pose to input\s+3",
        )

        retarget = (
            CARDS_ROOT / "kinefx-retarget-motionclip.md"
        ).read_text(encoding="utf-8")
        self.assertRegex(
            retarget,
            r"(?s)Rig Match Pose.*?Map Points.*?Full Body IK.*?Joint Deform",
        )
        self.assertRegex(
            retarget,
            r"(?s)Biped Setup and Biped Retarget are Houdini 22 nodes.*?"
            r"not\s+valid substitutes for this H21 workflow",
        )

        vellum = (CARDS_ROOT / "vellum-overview.md").read_text(encoding="utf-8")
        self.assertIn("Hair (distance + bend/twist)", vellum)
        self.assertIn(
            "input-state preparation, not constraint generation",
            vellum,
        )
        self.assertNotIn(
            "Hair uses distance and bend or orient constraints",
            vellum,
        )

        cop = (
            CARDS_ROOT / "copernicus-feedback-texture-output.md"
        ).read_text(encoding="utf-8")
        self.assertRegex(
            cop,
            r"(?s)prepared low/high/optional cage SOPs.*?Bake Setup.*?"
            r"Bake Geometry Textures.*?All Outputs.*?ROP Image Output COP",
        )
        self.assertIn("Add AOVs from Input", cop)
        self.assertIn("`<LAYER>`", cop)

        crowd = (CARDS_ROOT / "crowds-transition-render.md").read_text(
            encoding="utf-8"
        )
        self.assertRegex(
            crowd,
            r"Crowd Trigger -> Crowd\s+Transition -> merge_transitions",
        )
        self.assertRegex(
            crowd,
            r"Crowd States -> merge_states ->\s+Crowd Solver",
        )

        pdg = (CARDS_ROOT / "pdg-tops.md").read_text(encoding="utf-8")
        for expression in (
            "@pdg_input.0",
            "@pdg_output.0",
            'pdgoutput(1, "", 0)',
        ):
            self.assertIn(expression, pdg)

        pyro = (CARDS_ROOT / "pyro-cache-shade-render.md").read_text(
            encoding="utf-8"
        )
        self.assertRegex(
            pyro,
            r"(?s)Volume LOP.*?Material Library.*?Assign Material.*?"
            r"Render Vars/Products.*?Karma Render Settings",
        )
        self.assertIn("For Karma CPU", pyro)
        self.assertIn("for XPU", pyro)
        self.assertIn("generic MaterialX VDF/EDF support", pyro)

    def test_editorial_and_project_policies_are_not_presented_as_sidefx_rules(
        self,
    ) -> None:
        hda = (CARDS_ROOT / "hda-authoring-versioning.md").read_text(
            encoding="utf-8"
        )
        cache = (CARDS_ROOT / "cache-packaging-performance.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("Editorial recommendation", hda)
        self.assertIn("project policy rather than a SideFX requirement", hda)
        self.assertIn("Project policy", cache)
        self.assertIn("does not prescribe a universal studio version scheme", cache)
        self.assertIn("authorized to redistribute", cache)

    def test_cards_are_not_near_duplicates(self) -> None:
        texts = {
            str(card["id"]): (
                PACK_ROOT / str(card["path"])
            ).read_text(encoding="utf-8")
            for card in self.cards
        }
        for (left_id, left), (right_id, right) in combinations(
            texts.items(), 2
        ):
            with self.subTest(left=left_id, right=right_id):
                left_terms = _word_set(left)
                right_terms = _word_set(right)
                union = left_terms | right_terms
                jaccard = (
                    len(left_terms & right_terms) / len(union)
                    if union
                    else 1.0
                )
                self.assertLess(
                    jaccard,
                    0.65,
                    f"{left_id} and {right_id} repeat the same vocabulary",
                )
                # A high sequence ratio necessarily shares substantial
                # vocabulary. Gate the much more expensive character-level
                # comparison so this test remains useful as the corpus grows.
                if jaccard >= 0.35:
                    normalized_left = " ".join(left.casefold().split())
                    normalized_right = " ".join(right.casefold().split())
                    sequence_ratio = difflib.SequenceMatcher(
                        None,
                        normalized_left,
                        normalized_right,
                        autojunk=False,
                    ).ratio()
                    self.assertLess(
                        sequence_ratio,
                        0.82,
                        f"{left_id} and {right_id} are near-duplicate prose",
                    )


if __name__ == "__main__":
    unittest.main()
