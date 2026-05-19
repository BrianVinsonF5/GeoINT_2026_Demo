import os
import unittest

os.environ.setdefault("SKIP_STARTUP_INIT_FOR_TESTS", "true")

import app as app_module  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


class _FakeUpstreamResponse:
    def __init__(self, body: bytes = b"{}", status: int = 200, content_type: str = "application/json"):
        self._body = body
        self.status = status
        self.headers = {"Content-Type": content_type}

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class RbacIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app_module.app)

        self._orig_urlopen = app_module.request.urlopen
        self._orig_embed_query_text = app_module.embed_query_text
        self._orig_query_vector_store = app_module.query_vector_store
        self._orig_query_gemini = app_module.query_gemini
        self._orig_query_calypso = app_module.query_calypso
        self._orig_calypso_client = app_module.calypso_client
        self._orig_project_id = app_module.CALYPSOAI_PROJECT_ID
        self._orig_project_id_group1 = app_module.CALYPSOAI_PROJECT_ID_GROUP1
        self._orig_project_id_group2 = app_module.CALYPSOAI_PROJECT_ID_GROUP2

    def tearDown(self) -> None:
        app_module.request.urlopen = self._orig_urlopen
        app_module.embed_query_text = self._orig_embed_query_text
        app_module.query_vector_store = self._orig_query_vector_store
        app_module.query_gemini = self._orig_query_gemini
        app_module.query_calypso = self._orig_query_calypso
        app_module.calypso_client = self._orig_calypso_client
        app_module.CALYPSOAI_PROJECT_ID = self._orig_project_id
        app_module.CALYPSOAI_PROJECT_ID_GROUP1 = self._orig_project_id_group1
        app_module.CALYPSOAI_PROJECT_ID_GROUP2 = self._orig_project_id_group2

    def test_group1_can_access_all_layers_via_wfs_proxy(self) -> None:
        def _fake_urlopen(_req, timeout=90):
            return _FakeUpstreamResponse(body=b'{"type":"FeatureCollection","features":[]}')

        app_module.request.urlopen = _fake_urlopen

        res = self.client.get(
            "/api/geoserver/ows",
            params={
                "service": "WFS",
                "request": "GetFeature",
                "typeName": "geoint:military_installations,geoint:satellite_imagery_catalog,geoint:geoint_reports",
                "outputFormat": "application/json",
            },
            cookies={"accessLevel": "Group1"},
        )

        self.assertEqual(res.status_code, 200)

    def test_group2_requesting_military_installations_is_forbidden(self) -> None:
        res = self.client.get(
            "/api/geoserver/ows",
            params={
                "service": "WFS",
                "request": "GetFeature",
                "typeName": "geoint:military_installations",
                "outputFormat": "application/json",
            },
            cookies={"accessLevel": "Group2"},
        )

        self.assertEqual(res.status_code, 403)
        body = res.json()
        self.assertIn("restricted", body.get("message", ""))
        self.assertIn("military_installations", body.get("restrictedLayers", []))

    def test_group2_chat_declines_military_topic_and_filters_context(self) -> None:
        captured = {"query_called": False, "prompt": None}

        async def _fake_embed_query_text(_text):
            return [0.12, 0.34, 0.56]

        async def _fake_query_vector_store(_embedding, _k, where_filter=None):
            captured["query_called"] = True
            return {
                "documents": [[
                    "Military installation record 1: Restricted base details.",
                    "Satellite imagery record 2: Sensor Sentinel-2 with low cloud cover.",
                ]],
                "metadatas": [[
                    {"source_table": "military_installations", "record_id": "1", "classification": "secret", "coordinates": "[1,2]"},
                    {
                        "source_table": "satellite_imagery_catalog",
                        "record_id": "2",
                        "classification": "unclassified",
                        "coordinates": "[10,20]",
                    },
                ]],
            }

        async def _fake_query_gemini(prompt):
            captured["prompt"] = prompt
            return "Military installations indicate heightened activity in the AOI."

        app_module.embedding_model = object()
        app_module.chroma_collection = object()
        app_module.embed_query_text = _fake_embed_query_text
        app_module.query_vector_store = _fake_query_vector_store
        app_module.query_gemini = _fake_query_gemini

        res = self.client.post(
            "/api/chat",
            json={"message": "Tell me about military installations in Europe", "guardrails_enabled": False},
            cookies={"accessLevel": "Group2"},
        )

        self.assertEqual(res.status_code, 200)
        body = res.json()

        self.assertFalse(captured["query_called"], "restricted Group2 topic should be blocked before retrieval")
        self.assertIsNone(captured["prompt"], "restricted Group2 topic should not reach LLM prompt generation")

        self.assertIn("only discuss Satellite Imagery Catalog", body.get("response", ""))
        self.assertEqual(body.get("sources", []), [])

    def test_guardrails_routes_project_id_by_group(self) -> None:
        captured = {"projects": []}

        async def _fake_embed_query_text(_text):
            return [0.12, 0.34, 0.56]

        async def _fake_query_vector_store(_embedding, _k, where_filter=None):
            return {
                "documents": [["Satellite imagery record 2: Sensor Sentinel-2 with low cloud cover."]],
                "metadatas": [[
                    {
                        "source_table": "satellite_imagery_catalog",
                        "record_id": "2",
                        "classification": "unclassified",
                        "coordinates": "[10,20]",
                    }
                ]],
            }

        async def _fake_query_calypso(_prompt, project_id):
            captured["projects"].append(project_id)
            return f"guardrailed via {project_id}"

        app_module.embedding_model = object()
        app_module.chroma_collection = object()
        app_module.embed_query_text = _fake_embed_query_text
        app_module.query_vector_store = _fake_query_vector_store
        app_module.query_calypso = _fake_query_calypso
        app_module.calypso_client = object()
        app_module.CALYPSOAI_PROJECT_ID = ""
        app_module.CALYPSOAI_PROJECT_ID_GROUP1 = "project-group1"
        app_module.CALYPSOAI_PROJECT_ID_GROUP2 = "project-group2"

        res_group1 = self.client.post(
            "/api/chat",
            json={"message": "Summarize the imagery", "guardrails_enabled": True},
            cookies={"accessLevel": "Group1"},
        )
        self.assertEqual(res_group1.status_code, 200)

        res_group2 = self.client.post(
            "/api/chat",
            json={"message": "Summarize the imagery", "guardrails_enabled": True},
            cookies={"accessLevel": "Group2"},
        )
        self.assertEqual(res_group2.status_code, 200)

        self.assertEqual(captured["projects"], ["project-group1", "project-group2"])


if __name__ == "__main__":
    unittest.main()
