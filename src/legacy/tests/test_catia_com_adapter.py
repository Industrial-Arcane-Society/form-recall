import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path


ADAPTER_PATH = Path(__file__).parents[1] / "ai_modeler" / "catia_com_adapter.py"
SPEC = importlib.util.spec_from_file_location("catia_com_adapter", ADAPTER_PATH)
ADAPTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ADAPTER)


class FakeSketch:
    def __init__(self):
        self.Factory2D = FakeFactory2D()

    def CloseEdition(self):
        pass


class FakeFactory2D:
    def CreateClosedCircle(self, *args):
        pass

    def CreateLine(self, *args):
        pass


class FakeSketches:
    def Add(self, _plane):
        return FakeSketch()


class FakeBody:
    def __init__(self):
        self.Sketches = FakeSketches()


class FakeCollection:
    def __init__(self, values=None):
        self.values = values or []
        self.Count = len(self.values)

    def Item(self, key):
        if key == "PartBody":
            return FakeBody()
        return self.values[key - 1]

    def Add(self):
        item = FakeHybridBody()
        self.values.append(item)
        self.Count = len(self.values)
        return item


class FakeHybridBody:
    def AppendHybridShape(self, _shape):
        pass


class FakeFeature:
    Name = ""


class FakeShapeFactory:
    def __init__(self):
        self.operations = []

    def AddNewPad(self, _sketch, height):
        self.operations.append(("pad", height))
        return FakeFeature()

    def AddNewPocket(self, _sketch, depth):
        self.operations.append(("pocket", depth))
        return FakeFeature()


class FakePart:
    def __init__(self):
        self.OriginElements = type("Origin", (), {"PlaneXY": object(), "PlaneYZ": object(), "PlaneZX": object()})()
        self.HybridBodies = FakeCollection()
        self.HybridShapeFactory = type("HybridFactory", (), {"AddNewPlaneOffset": lambda self, *args: object()})()
        self.Bodies = FakeCollection()
        self.ShapeFactory = FakeShapeFactory()
        self.MainBody = object()

    def CreateReferenceFromObject(self, item):
        return item

    def Update(self):
        pass

    def GetWorkbench(self, _name):
        raise RuntimeError("no SPA measurement in fake")


class FakeDocument:
    def __init__(self):
        self.Part = FakePart()
        self.closed = False

    def SaveAs(self, path):
        Path(path).write_bytes(b"mock CATPart")

    def Close(self):
        self.closed = True


class FakeDocuments:
    def __init__(self):
        self.document = FakeDocument()

    def Add(self, _kind):
        return self.document


class FakeCatia:
    def __init__(self):
        self.Documents = FakeDocuments()
        self.Visible = False
        self.DisplayFileAlerts = True


def fixture_request(output_path):
    return {
        "protocol": ADAPTER.PROTOCOL,
        "action": "export",
        "identity": {"model_id": "active-model-1", "generation": 4, "revision": 2,
                     "geometry_fingerprint": "sha256-fingerprint"},
        "project": {
            "format": "ai-modeler", "version": 1, "name": "plate", "source_prompt": "reference image",
            "features": [
                {"kind": "box", "params": {"length": 100, "width": 20, "height": 60, "center": [0, 0, 0], "base": False},
                 "op": "add", "name": "main body", "feature_id": "body", "enabled": True,
                 "provenance": {"length": "image_estimate"}},
                {"kind": "hole", "params": {"diameter": 8, "pos": "point", "point": [0, 0, 20], "axis": "Y", "through": True},
                 "op": "cut", "name": "mounting hole", "feature_id": "hole-1", "enabled": True,
                 "provenance": {"point": "image_estimate", "diameter": "user"}},
            ],
        },
        "cadplan": {"schema_version": "1.0", "features": [
            {"id": "body", "kind": "box", "operation": "add", "parameters": {"length": 100},
             "sources": {"length": {"type": "image_estimate", "evidence": "front view scale"}}},
            {"id": "hole-1", "kind": "hole", "operation": "cut", "parameters": {"diameter": 8},
             "sources": {"point": {"type": "image_estimate", "evidence": "center row"},
                         "diameter": {"type": "user", "evidence": "8 mm"}}},
        ]},
        "output_path": str(output_path),
        "open_in_catia": False,
    }


class CatiaAdapterTests(unittest.TestCase):
    def test_preflight_maps_native_features_and_carries_sources_without_com(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            request_path = root / "request.json"
            request_path.write_text(json.dumps(fixture_request(root / "part.CATPart")), encoding="utf-8")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = ADAPTER.execute(request_path, preflight=True,
                    catia_factory=lambda: self.fail("preflight must not create a CATIA COM object"))
            result = json.loads(stdout.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(result["state"], "preflight_passed")
            self.assertEqual(len(result["planned_operations"]), 2)
            self.assertEqual(result["planned_operations"][1]["source_types"], "image_estimate,user")
            self.assertEqual(result["planned_operations"][1]["mapping_status"], "operation_parameters_transferred")
            self.assertEqual(len(result["approximations"]), 2)
            self.assertTrue(all(row["mapping_status"] == "approximate_feature_mapping" for row in result["approximations"]))

    def test_mock_com_builds_from_rust_project_and_writes_provenance_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "part.CATPart"
            request_path = root / "request.json"
            request_path.write_text(json.dumps(fixture_request(output)), encoding="utf-8")
            catia = FakeCatia()
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = ADAPTER.execute(request_path, catia_factory=lambda: catia)
            result = json.loads(stdout.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(result["state"], "partial")
            self.assertTrue(output.is_file())
            self.assertEqual(len(result["completed"]), 2)
            self.assertIn("image_estimate", result["completed"][1]["source"]["sources"]["point"]["type"])
            report = json.loads(Path(result["report_path"]).read_text(encoding="utf-8"))
            self.assertEqual(report["geometry_source"], "rust_active_model_f5_aimodel")
            self.assertEqual(report["fidelity_claim"], "operation_mapping_only_geometry_not_verified")
            self.assertEqual(report["identity"]["geometry_fingerprint"], "sha256-fingerprint")
            self.assertEqual(report["cadplan"]["features"][1]["sources"]["point"]["evidence"], "center row")
            self.assertFalse(report["catpart_verified_in_catia"])
            self.assertEqual(len(report["approximations"]), 2)
            self.assertTrue(all(row["mapping_status"] == "approximate_feature_mapping" for row in report["approximations"]))
            self.assertTrue(catia.Visible is False)
            self.assertTrue(catia.Documents.document.closed)

    def test_push_keeps_the_created_document_visible_only_after_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "part.CATPart"
            request = fixture_request(output)
            request["open_in_catia"] = True
            request_path = root / "request.json"
            request_path.write_text(json.dumps(request), encoding="utf-8")
            catia = FakeCatia()
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = ADAPTER.execute(request_path, catia_factory=lambda: catia)
            result = json.loads(stdout.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(result["state"], "partial")
            self.assertTrue(catia.Visible)
            self.assertFalse(catia.Documents.document.closed)

    def test_no_supported_operation_fails_before_any_com_connection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            request = fixture_request(root / "part.CATPart")
            request["project"]["features"] = [
                {"kind": "helix", "params": {}, "op": "add", "name": "spring", "feature_id": "spring"},
                {"kind": "cylinder", "params": {"axis": "A"}, "op": "add", "name": "bad axis", "feature_id": "bad-axis"},
            ]
            path = root / "request.json"
            path.write_text(json.dumps(request), encoding="utf-8")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = ADAPTER.execute(path, catia_factory=lambda: self.fail("unsupported model must not launch CATIA"))
            result = json.loads(stdout.getvalue())
            self.assertEqual(code, 2)
            self.assertEqual(result["state"], "failed")
            self.assertIn("no CATIA-supported operations", result["error"])
            plan = ADAPTER.build_feature_table(request)
            self.assertEqual(len(plan["skipped"]), 2)
            self.assertTrue(all(row.get("mapping_status") for row in plan["skipped"]))


if __name__ == "__main__":
    unittest.main()
