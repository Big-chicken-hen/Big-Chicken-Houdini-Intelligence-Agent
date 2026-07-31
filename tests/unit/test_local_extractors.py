from __future__ import annotations

import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path
from unittest import mock


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_PACKAGE_ROOT = REPOSITORY_ROOT / "houdini_package" / "python_libs"
TEST_TMP_ROOT = REPOSITORY_ROOT / ".runtime" / "tmp"
TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(RUNTIME_PACKAGE_ROOT))

from hia_mcp_runtime import local_extractors as extractors


class FakeOcrBackend:
    name = "fake_local_ocr"

    def capability(self) -> dict[str, object]:
        return {
            "name": self.name,
            "status": "available",
            "formats": [".png"],
            "message": "fixture ready",
            "action": "",
            "local_only": True,
        }

    @staticmethod
    def extract_text(path: Path) -> str:
        return f"OCR text from {path.stem}"

    @staticmethod
    def extract_image(image: object) -> str:
        del image
        return "OCR text from PDF page"


class MissingOcrBackend:
    name = "missing_local_ocr"

    def capability(self) -> dict[str, object]:
        return {
            "name": self.name,
            "status": "dependency_missing",
            "formats": [".png"],
            "dependencies": ["fixture-ocr"],
            "message": "fixture OCR dependency is missing",
            "action": "Install fixture-ocr into the project .venv",
            "local_only": True,
        }

    @staticmethod
    def extract_text(path: Path) -> str:
        raise AssertionError(f"must not run for {path}")

    @staticmethod
    def extract_image(image: object) -> str:
        raise AssertionError(f"must not run for {image!r}")


class FakeAsrBackend:
    name = "fake_local_asr"

    def __init__(self, duration: float = 125.0) -> None:
        self.duration = duration
        self.calls: list[tuple[float, float]] = []

    def capability(self) -> dict[str, object]:
        return {
            "name": self.name,
            "status": "available",
            "formats": [".mp3"],
            "message": "fixture ready",
            "action": "",
            "local_only": True,
        }

    def duration_seconds(self, path: Path) -> float:
        del path
        return self.duration

    def transcribe_slice(
        self,
        path: Path,
        start_seconds: float,
        end_seconds: float,
    ) -> list[dict[str, object]]:
        del path
        self.calls.append((start_seconds, end_seconds))
        return [
            {
                "text": f"Transcript {start_seconds:.0f}-{end_seconds:.0f}",
                "start_seconds": start_seconds,
                "end_seconds": end_seconds,
                "metadata": {"confidence": 1.0},
            }
        ]


class MissingAsrBackend:
    name = "missing_local_asr"

    def capability(self) -> dict[str, object]:
        return {
            "name": self.name,
            "status": "not_configured",
            "formats": [".mp3"],
            "dependencies": ["local model"],
            "message": "fixture ASR model is not configured",
            "action": "Place the model below project .runtime",
            "local_only": True,
        }

    @staticmethod
    def duration_seconds(path: Path) -> float:
        raise AssertionError(f"must not run for {path}")

    @staticmethod
    def transcribe_slice(
        path: Path,
        start_seconds: float,
        end_seconds: float,
    ) -> list[dict[str, object]]:
        raise AssertionError(
            f"must not run for {path}: {start_seconds}-{end_seconds}"
        )


class LocalExtractorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            dir=TEST_TMP_ROOT,
            prefix="extractor-test-",
        )
        self.root = Path(self.temporary.name)
        self.config = extractors.ExtractionConfig(project_root=REPOSITORY_ROOT)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def extractor(
        self,
        *,
        config: extractors.ExtractionConfig | None = None,
        ocr_backend: object | None = None,
        asr_backend: object | None = None,
    ) -> extractors.LocalAssetExtractor:
        return extractors.LocalAssetExtractor(
            config or self.config,
            ocr_backend=ocr_backend or FakeOcrBackend(),
            asr_backend=asr_backend or FakeAsrBackend(),
        )

    def assert_fragment_protocol(
        self,
        fragment: dict[str, object],
        asset_id: str,
    ) -> None:
        self.assertEqual(
            {"asset_id", "fragment_id", "text", "locator", "metadata"},
            set(fragment),
        )
        self.assertEqual(asset_id, fragment["asset_id"])
        self.assertTrue(str(fragment["fragment_id"]).startswith(asset_id + ":"))
        self.assertTrue(str(fragment["text"]).strip())
        self.assertIsInstance(fragment["locator"], dict)
        self.assertIsInstance(fragment["metadata"], dict)

    def test_txt_markdown_and_html_use_stdlib_and_stable_fragments(self) -> None:
        markdown = self.root / "notes.md"
        markdown.write_text("# Heading\n\nLocal evidence.", encoding="utf-8")
        html = self.root / "page.html"
        html.write_text(
            "<h1>Visible</h1><script>hidden()</script><p>Body</p>",
            encoding="utf-8",
        )
        extractor = self.extractor()

        first = extractor.extract(markdown, asset_id="asset-md")
        second = extractor.extract(markdown, asset_id="asset-md")
        html_result = extractor.extract(html, asset_id="asset-html")

        self.assertEqual("ready", first.status)
        self.assertEqual(first.as_dict(), second.as_dict())
        self.assertIn("# Heading", first.fragments[0]["text"])
        self.assertEqual("Visible\n\nBody", html_result.fragments[0]["text"])
        self.assertNotIn("hidden", html_result.fragments[0]["text"])
        self.assert_fragment_protocol(first.fragments[0], "asset-md")

    def test_srt_and_vtt_emit_timecode_locators(self) -> None:
        srt = self.root / "captions.srt"
        srt.write_text(
            "1\n00:00:01,250 --> 00:00:03,500\nFirst <b>cue</b>\n\n"
            "2\n00:01:00,000 --> 00:01:02,000\nSecond cue\n",
            encoding="utf-8",
        )
        vtt = self.root / "captions.vtt"
        vtt.write_text(
            "WEBVTT\n\n00:01.000 --> 00:02.500\nWeb cue\n",
            encoding="utf-8",
        )

        srt_result = self.extractor().extract(srt, asset_id="asset-srt")
        vtt_result = self.extractor().extract(vtt, asset_id="asset-vtt")

        self.assertEqual(2, len(srt_result.fragments))
        self.assertEqual(
            {
                "type": "time_range",
                "start_seconds": 1.25,
                "end_seconds": 3.5,
                "cue": 1,
            },
            srt_result.fragments[0]["locator"],
        )
        self.assertEqual("First cue", srt_result.fragments[0]["text"])
        self.assertEqual(1.0, vtt_result.fragments[0]["locator"]["start_seconds"])
        self.assertEqual(2.5, vtt_result.fragments[0]["locator"]["end_seconds"])

    def test_csv_emits_addressable_rows(self) -> None:
        source = self.root / "table.csv"
        source.write_text('name,note\nalpha,"one,two"\n', encoding="utf-8")

        result = self.extractor().extract(source, asset_id="asset-csv")

        self.assertEqual("ready", result.status)
        self.assertEqual(2, len(result.fragments))
        self.assertEqual(
            {"type": "row", "row": 2},
            result.fragments[1]["locator"],
        )
        self.assertEqual("alpha\tone,two", result.fragments[1]["text"])

    def test_docx_emits_paragraph_fragments_without_python_docx(self) -> None:
        source = self.root / "document.docx"
        self.write_zip(
            source,
            {
                "word/document.xml": (
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    '<w:document xmlns:w="http://schemas.openxmlformats.org/'
                    'wordprocessingml/2006/main"><w:body>'
                    "<w:p><w:r><w:t>First paragraph</w:t></w:r></w:p>"
                    "<w:p><w:r><w:t>Second</w:t><w:tab/>"
                    "<w:t>paragraph</w:t></w:r></w:p>"
                    "</w:body></w:document>"
                )
            },
        )

        result = self.extractor().extract(source, asset_id="asset-docx")

        self.assertEqual("ready", result.status)
        self.assertEqual(2, len(result.fragments))
        self.assertEqual(
            {"type": "paragraph", "paragraph": 2},
            result.fragments[1]["locator"],
        )
        self.assertEqual("Second\tparagraph", result.fragments[1]["text"])

    def test_pptx_emits_one_locator_per_slide(self) -> None:
        source = self.root / "slides.pptx"
        namespace = "http://schemas.openxmlformats.org/drawingml/2006/main"
        self.write_zip(
            source,
            {
                "ppt/slides/slide2.xml": (
                    f'<a:root xmlns:a="{namespace}"><a:p><a:r>'
                    "<a:t>Slide two</a:t></a:r></a:p></a:root>"
                ),
                "ppt/slides/slide1.xml": (
                    f'<a:root xmlns:a="{namespace}"><a:p><a:r>'
                    "<a:t>Slide one</a:t></a:r></a:p></a:root>"
                ),
            },
        )

        result = self.extractor().extract(source, asset_id="asset-pptx")

        self.assertEqual(["Slide one", "Slide two"], [
            fragment["text"] for fragment in result.fragments
        ])
        self.assertEqual(
            [{"type": "slide", "slide": 1}, {"type": "slide", "slide": 2}],
            [fragment["locator"] for fragment in result.fragments],
        )

    def test_xlsx_resolves_sheet_names_shared_strings_and_rows(self) -> None:
        source = self.root / "workbook.xlsx"
        spreadsheet_namespace = (
            "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
        )
        document_relations = (
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
        )
        package_relations = (
            "http://schemas.openxmlformats.org/package/2006/relationships"
        )
        self.write_zip(
            source,
            {
                "xl/workbook.xml": (
                    f'<workbook xmlns="{spreadsheet_namespace}" '
                    f'xmlns:r="{document_relations}"><sheets>'
                    '<sheet name="Evidence" sheetId="1" r:id="rId1"/>'
                    "</sheets></workbook>"
                ),
                "xl/_rels/workbook.xml.rels": (
                    f'<Relationships xmlns="{package_relations}">'
                    '<Relationship Id="rId1" '
                    'Target="worksheets/sheet1.xml" '
                    'Type="worksheet"/></Relationships>'
                ),
                "xl/sharedStrings.xml": (
                    f'<sst xmlns="{spreadsheet_namespace}">'
                    "<si><t>Header</t></si><si><t>Value</t></si></sst>"
                ),
                "xl/worksheets/sheet1.xml": (
                    f'<worksheet xmlns="{spreadsheet_namespace}"><sheetData>'
                    '<row r="1"><c r="A1" t="s"><v>0</v></c></row>'
                    '<row r="2"><c r="A2" t="s"><v>1</v></c>'
                    '<c r="B2" t="inlineStr"><is><t>Inline</t></is></c></row>'
                    "</sheetData></worksheet>"
                ),
            },
        )

        result = self.extractor().extract(source, asset_id="asset-xlsx")

        self.assertEqual("ready", result.status)
        self.assertEqual("A1=Header", result.fragments[0]["text"])
        self.assertEqual("A2=Value\tB2=Inline", result.fragments[1]["text"])
        self.assertEqual(
            {"type": "sheet_row", "sheet": "Evidence", "row": 2},
            result.fragments[1]["locator"],
        )

    def test_pdf_adapter_emits_per_page_fragments_and_skips_blank_page(self) -> None:
        source = self.root / "document.pdf"
        source.write_bytes(b"%PDF fixture")

        class FakePage:
            def __init__(self, text: str) -> None:
                self.text = text

            def extract_text(self) -> str:
                return self.text

        class FakeReader:
            def __init__(self, path: str) -> None:
                self.path = path
                self.pages = [
                    FakePage("Page one"),
                    FakePage(""),
                    FakePage("Page three"),
                ]

        fake_pypdf = types.SimpleNamespace(PdfReader=FakeReader)
        with (
            mock.patch.object(
                extractors,
                "_module_available",
                side_effect=lambda name: name == "pypdf",
            ),
            mock.patch.dict(sys.modules, {"pypdf": fake_pypdf}),
        ):
            result = self.extractor().extract(source, asset_id="asset-pdf")

        self.assertEqual("ready", result.status)
        self.assertEqual(3, result.details["pages"])
        self.assertEqual(
            [{"type": "page", "page": 1}, {"type": "page", "page": 3}],
            [fragment["locator"] for fragment in result.fragments],
        )

    def test_scanned_pdf_page_is_rendered_in_memory_for_the_single_ocr_backend(
        self,
    ) -> None:
        source = self.root / "scan.pdf"
        source.write_bytes(b"%PDF fixture")
        package_root = (
            self.root / ".venv" / "Lib" / "site-packages" / "pypdfium2"
        )
        package_root.mkdir(parents=True)

        class BlankPage:
            @staticmethod
            def extract_text() -> str:
                return ""

        class FakeReader:
            def __init__(self, path: str) -> None:
                self.path = path
                self.pages = [BlankPage()]

        fake_pypdf = types.SimpleNamespace(PdfReader=FakeReader)
        config = extractors.ExtractionConfig(project_root=self.root)
        with (
            mock.patch.object(
                extractors,
                "_module_available",
                side_effect=lambda name: name in {"pypdf", "pypdfium2"},
            ),
            mock.patch.object(
                extractors,
                "_module_root",
                return_value=package_root,
            ),
            mock.patch.object(
                extractors,
                "_render_pdf_page",
                return_value="in-memory-page-array",
            ) as render,
            mock.patch.dict(sys.modules, {"pypdf": fake_pypdf}),
        ):
            result = self.extractor(
                config=config,
                ocr_backend=FakeOcrBackend(),
            ).extract(source, asset_id="asset-scan")

        self.assertEqual("ready", result.status)
        self.assertEqual("OCR text from PDF page", result.fragments[0]["text"])
        self.assertEqual("rapidocr", result.fragments[0]["metadata"]["extraction"])
        render.assert_called_once_with(source.resolve(), 1)

    def test_image_ocr_is_injected_local_only_and_has_image_locator(self) -> None:
        source = self.root / "frame.png"
        source.write_bytes(b"fixture image bytes")

        result = self.extractor(
            ocr_backend=FakeOcrBackend(),
        ).extract(source, asset_id="asset-image")

        self.assertEqual("ready", result.status)
        self.assertEqual("OCR text from frame", result.fragments[0]["text"])
        self.assertEqual({"type": "image"}, result.fragments[0]["locator"])
        self.assertEqual(
            "fake_local_ocr",
            result.fragments[0]["metadata"]["backend"],
        )

    def test_rapidocr_backend_uses_wheel_models_and_returns_chinese_english(
        self,
    ) -> None:
        package_root = self.root / ".venv" / "Lib" / "site-packages" / "rapidocr"
        model_root = package_root / "models"
        model_root.mkdir(parents=True)
        for model in extractors.RapidOcrBackend._MODEL_FILES:
            (model_root / model).write_bytes(b"fixture model")
        image = self.root / "source.png"
        image.write_bytes(b"fixture image")
        config = extractors.ExtractionConfig(project_root=self.root)
        backend = extractors.RapidOcrBackend(config)

        class FakeEngine:
            def __call__(self, source: object) -> object:
                self.source = source
                return types.SimpleNamespace(txts=("中文证据", "English evidence"))

        with (
            mock.patch.object(extractors, "_module_available", return_value=True),
            mock.patch.object(
                extractors,
                "_module_root",
                side_effect=lambda name: (
                    package_root
                    if name == "rapidocr"
                    else self.root / ".venv" / "Lib" / "site-packages" / name
                ),
            ),
            mock.patch.object(
                extractors.importlib,
                "import_module",
                return_value=types.SimpleNamespace(RapidOCR=FakeEngine),
            ),
        ):
            capability = backend.capability()
            text = backend.extract_text(image)

        self.assertEqual("available", capability["status"])
        self.assertEqual("中文证据\nEnglish evidence", text)
        self.assertEqual("PP-OCRv6 small", capability["model_family"])
        self.assertFalse(capability["network_downloads"])

    def test_remediation_uses_managed_launcher_entrypoints(self) -> None:
        config = extractors.ExtractionConfig(project_root=self.root)
        source = (
            REPOSITORY_ROOT
            / "houdini_package"
            / "python_libs"
            / "hia_mcp_runtime"
            / "local_extractors.py"
        ).read_text(encoding="utf-8")
        for obsolete in (
            ".venv/Scripts/python.exe -m pip",
            "Place one converted model",
            "Install FFmpeg below",
            "Copy the model below .runtime",
        ):
            self.assertNotIn(obsolete, source)

        with mock.patch.object(
            extractors,
            "_module_available",
            return_value=False,
        ):
            report = extractors.capability_report(config)
            contract = extractors.dependency_contract(config)

        self.assertIn(
            extractors.KNOWLEDGE_ENVIRONMENT_REPAIR_ACTION,
            report["capabilities"]["pypdf"]["action"],
        )
        for name in ("image_ocr", "media_asr"):
            self.assertIn(
                extractors.ASSET_REPAIR_ACTION,
                report["capabilities"][name]["action"],
            )
        self.assertEqual(
            extractors.ASSET_REPAIR_ACTION,
            contract["backends"]["ocr"]["install_command"],
        )
        self.assertEqual(
            extractors.ASSET_REPAIR_ACTION,
            contract["backends"]["asr"]["install_command"],
        )

        site_packages = self.root / ".venv" / "Lib" / "site-packages"
        for module in ("faster_whisper", "ctranslate2", "numpy"):
            (site_packages / module).mkdir(parents=True, exist_ok=True)
        backend = extractors.FasterWhisperBackend(config)
        with (
            mock.patch.object(
                extractors,
                "_module_available",
                return_value=True,
            ),
            mock.patch.object(
                extractors,
                "_module_root",
                side_effect=lambda name: site_packages / name,
            ),
        ):
            missing_model = backend.capability()
            (
                self.root
                / extractors.ASR_MODEL_RELATIVE
            ).mkdir(parents=True, exist_ok=True)
            incomplete_model = backend.capability()
            model_path = self.root / extractors.ASR_MODEL_RELATIVE
            for name in (
                *extractors.ASR_MODEL_REQUIRED_FILES,
                "vocabulary.json",
            ):
                (model_path / name).write_bytes(b"fixture")
            missing_ffmpeg = backend.capability()

        self.assertEqual("not_configured", missing_model["status"])
        self.assertIn(
            extractors.ASSET_REPAIR_ACTION,
            missing_model["action"],
        )
        self.assertEqual("not_configured", incomplete_model["status"])
        self.assertIn("model.bin", incomplete_model["dependencies"])
        self.assertIn("resume", incomplete_model["action"])
        self.assertEqual("not_configured", missing_ffmpeg["status"])
        self.assertIn(
            extractors.ASSET_REPAIR_ACTION,
            missing_ffmpeg["action"],
        )

    def test_ordinary_and_media_size_limits_are_both_enforced(self) -> None:
        text = self.root / "oversized.txt"
        media = self.root / "oversized.mp3"
        text.write_bytes(b"12345")
        media.write_bytes(b"12345")
        config = extractors.ExtractionConfig(
            project_root=REPOSITORY_ROOT,
            max_source_bytes=4,
            max_media_source_bytes=4,
        )
        extractor = self.extractor(config=config)

        text_result = extractor.extract(text, asset_id="asset-text")
        media_result = extractor.extract(media, asset_id="asset-media")

        self.assertEqual("failed", text_result.status)
        self.assertEqual("failed", media_result.status)
        self.assertEqual("unavailable", text_result.details["failure_reason"])
        self.assertEqual("unavailable", media_result.details["failure_reason"])
        self.assertIn("4 byte limit", text_result.warnings[0])
        self.assertIn("4 byte limit", media_result.warnings[0])

    def test_missing_ocr_and_asr_are_actionable_without_backend_execution(self) -> None:
        image = self.root / "frame.png"
        image.write_bytes(b"fixture")
        media = self.root / "recording.mp3"
        media.write_bytes(b"fixture")
        extractor = self.extractor(
            ocr_backend=MissingOcrBackend(),
            asr_backend=MissingAsrBackend(),
        )

        report = extractor.capabilities()
        image_result = extractor.extract(image, asset_id="asset-image")
        media_result = extractor.extract(media, asset_id="asset-media")

        self.assertTrue(report["local_only"])
        self.assertFalse(report["network_access"])
        self.assertFalse(report["summarization"])
        self.assertEqual("failed", image_result.status)
        self.assertEqual(
            "dependency_missing",
            image_result.details["failure_reason"],
        )
        self.assertIn("project .venv", image_result.warnings[1])
        self.assertEqual("failed", media_result.status)
        self.assertEqual(
            "not_configured",
            media_result.details["failure_reason"],
        )
        self.assertIn("project .runtime", media_result.warnings[1])

    def test_media_slices_resume_at_checkpoint_boundaries(self) -> None:
        source = self.root / "long-recording.mp3"
        source.write_bytes(b"fixture media")
        backend = FakeAsrBackend(duration=125.0)
        config = extractors.ExtractionConfig(
            project_root=REPOSITORY_ROOT,
            media_slice_seconds=60.0,
            max_media_slices_per_call=1,
        )
        extractor = self.extractor(config=config, asr_backend=backend)

        first = extractor.extract(source, asset_id="asset-media")
        second = extractor.extract(
            source,
            asset_id="asset-media",
            checkpoint=first.checkpoint,
        )
        final = extractor.extract(
            source,
            asset_id="asset-media",
            checkpoint=second.checkpoint,
        )

        self.assertEqual("partial", first.status)
        self.assertEqual(60.0, first.checkpoint["next_start_seconds"])
        self.assertEqual("partial", second.status)
        self.assertEqual(120.0, second.checkpoint["next_start_seconds"])
        self.assertEqual("ready", final.status)
        self.assertTrue(final.checkpoint["complete"])
        self.assertEqual(125.0, final.checkpoint["next_start_seconds"])
        self.assertEqual(
            [(0.0, 62.0), (58.0, 122.0), (118.0, 125.0)],
            backend.calls,
        )
        all_fragments = (
            first.fragments + second.fragments + final.fragments
        )
        self.assertEqual(3, len({
            fragment["fragment_id"] for fragment in all_fragments
        }))
        self.assertEqual(
            {
                "type": "time_range",
                "start_seconds": 120.0,
                "end_seconds": 125.0,
                "slice_index": 2,
                "segment": 1,
            },
            final.fragments[0]["locator"],
        )

    def test_media_overlap_assigns_boundary_segment_by_midpoint_once(self) -> None:
        source = self.root / "boundary.mp3"
        source.write_bytes(b"fixture media")

        class BoundaryBackend(FakeAsrBackend):
            def __init__(self) -> None:
                super().__init__(duration=120.0)

            def transcribe_slice(
                self,
                path: Path,
                start_seconds: float,
                end_seconds: float,
            ) -> list[dict[str, object]]:
                del path
                self.calls.append((start_seconds, end_seconds))
                return [
                    {
                        "text": "Boundary phrase",
                        "start_seconds": 59.0,
                        "end_seconds": 61.0,
                    }
                ]

        backend = BoundaryBackend()
        config = extractors.ExtractionConfig(
            project_root=REPOSITORY_ROOT,
            media_slice_seconds=60.0,
        )
        extractor = self.extractor(config=config, asr_backend=backend)

        first = extractor.extract(source, asset_id="asset-boundary")
        second = extractor.extract(
            source,
            asset_id="asset-boundary",
            checkpoint=first.checkpoint,
        )

        self.assertEqual("partial", first.status)
        self.assertEqual((), first.fragments)
        self.assertEqual("ready", second.status)
        self.assertEqual(1, len(second.fragments))
        self.assertEqual(
            {
                "type": "time_range",
                "start_seconds": 60.0,
                "end_seconds": 61.0,
                "slice_index": 1,
                "segment": 1,
            },
            second.fragments[0]["locator"],
        )
        self.assertEqual([(0.0, 62.0), (58.0, 120.0)], backend.calls)

    def test_failed_media_unit_discards_work_and_does_not_advance_checkpoint(
        self,
    ) -> None:
        source = self.root / "failure.mp3"
        source.write_bytes(b"fixture media")

        class FailsSecondSlice(FakeAsrBackend):
            def transcribe_slice(
                self,
                path: Path,
                start_seconds: float,
                end_seconds: float,
            ) -> list[dict[str, object]]:
                if self.calls:
                    raise RuntimeError("fixture ASR failure")
                return super().transcribe_slice(
                    path,
                    start_seconds,
                    end_seconds,
                )

        backend = FailsSecondSlice(duration=125.0)
        config = extractors.ExtractionConfig(
            project_root=REPOSITORY_ROOT,
            media_slice_seconds=60.0,
            max_media_slices_per_call=2,
        )

        result = self.extractor(
            config=config,
            asr_backend=backend,
        ).extract(source, asset_id="asset-failure")

        self.assertEqual("failed", result.status)
        self.assertEqual((), result.fragments)
        self.assertEqual(0.0, result.checkpoint["next_start_seconds"])
        self.assertEqual(0, result.checkpoint["next_slice_index"])
        self.assertEqual(
            extractors.EXTRACTION_CHECKPOINT_SCHEMA,
            result.checkpoint["schema"],
        )
        self.assertEqual(1, result.details["discarded_fragments"])

    def test_changed_media_rejects_an_old_checkpoint(self) -> None:
        source = self.root / "recording.mp3"
        source.write_bytes(b"first media state")
        backend = FakeAsrBackend()
        config = extractors.ExtractionConfig(
            project_root=REPOSITORY_ROOT,
            media_slice_seconds=60.0,
        )
        extractor = self.extractor(config=config, asr_backend=backend)
        first = extractor.extract(source, asset_id="asset-media")
        source.write_bytes(b"changed media state")

        result = extractor.extract(
            source,
            asset_id="asset-media",
            checkpoint=first.checkpoint,
        )

        self.assertEqual("failed", result.status)
        self.assertEqual("invalid_checkpoint", result.details["failure_reason"])
        self.assertIn("changed", result.warnings[0])
        self.assertEqual(1, len(backend.calls))

    def test_unsupported_and_invalid_asset_ids_are_structured_failures(self) -> None:
        unsupported = self.root / "asset.bin"
        unsupported.write_bytes(b"fixture")
        text = self.root / "notes.txt"
        text.write_text("text", encoding="utf-8")
        extractor = self.extractor()

        unsupported_result = extractor.extract(unsupported)
        invalid_id_result = extractor.extract(text, asset_id="../escape")

        self.assertEqual("failed", unsupported_result.status)
        self.assertEqual(
            "unsupported",
            unsupported_result.details["failure_reason"],
        )
        self.assertEqual("failed", invalid_id_result.status)
        self.assertIn("asset_id", invalid_id_result.warnings[0])

    def test_public_star_import_exports_rapidocr_and_not_tesseract(self) -> None:
        namespace: dict[str, object] = {}
        exec(
            "from hia_mcp_runtime.local_extractors import *",
            namespace,
        )

        self.assertIs(extractors.RapidOcrBackend, namespace["RapidOcrBackend"])
        self.assertNotIn("TesseractOcrBackend", namespace)

    def test_dependency_contract_is_launcher_and_manager_ready(self) -> None:
        contract = extractors.dependency_contract(
            self.config,
            ocr_backend=FakeOcrBackend(),
            asr_backend=FakeAsrBackend(),
        )

        self.assertEqual(
            "hia-local-extractor-dependencies/1",
            contract["schema"],
        )
        self.assertEqual("Launcher", contract["installer_owner"])
        self.assertFalse(contract["network_downloads"])
        ocr = contract["backends"]["ocr"]
        self.assertEqual(
            ["rapidocr", "onnxruntime", "pypdfium2"],
            [package["distribution"] for package in ocr["packages"]],
        )
        self.assertEqual(["Chinese", "English"], ocr["languages"])
        asr = contract["backends"]["asr"]
        self.assertEqual("cpu", asr["defaults"]["device"])
        self.assertEqual("int8", asr["defaults"]["compute_type"])
        self.assertEqual(5, asr["defaults"]["beam_size"])
        self.assertEqual(
            2.0,
            asr["defaults"]["slice_overlap_seconds"],
        )
        integration = contract["manager_integration"]
        self.assertTrue(integration["single_file_parser"])
        self.assertEqual(
            {"ready", "partial", "failed"},
            set(integration["statuses"]),
        )
        self.assertEqual(
            extractors.EXTRACTION_CHECKPOINT_SCHEMA,
            integration["checkpoint_schema"],
        )

    @staticmethod
    def write_zip(path: Path, members: dict[str, str]) -> None:
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, text in members.items():
                archive.writestr(name, text.encode("utf-8"))


if __name__ == "__main__":
    unittest.main()
