"""API contract checks; no provider calls or API key required."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch, Mock

from fastapi.testclient import TestClient
import main


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        main.get_client.cache_clear()

    def tearDown(self):
        main.get_client.cache_clear()

    def test_health_without_key(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "ai_configured": False})

    def test_upload_validation(self):
        with patch.object(main, "get_client") as provider:
            for data, mime, expected in [
                (b"text", "text/plain", 415),
                (b"", "image/jpeg", 400),
                (b"x" * (main.MAX_IMAGE_BYTES + 1), "image/jpeg", 413),
            ]:
                with self.subTest(expected=expected):
                    response = self.client.post("/recognize-landmark", files={"image": ("photo", data, mime)})
                    self.assertEqual(response.status_code, expected)
            provider.assert_not_called()

    def test_missing_key_returns_service_unavailable(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            response = self.client.post("/recognize-landmark", files={"image": ("photo.jpg", b"image", "image/jpeg")})
        self.assertEqual(response.status_code, 503)

    def test_unknown_landmark_normalized(self):
        with patch.object(main, "generate_text", return_value="Unknown landmark."):
            response = self.client.post("/recognize-landmark", files={"image": ("photo.jpg", b"image", "image/jpeg")})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["landmark_name"], "Unknown landmark")

    def test_invalid_story_never_calls_wikipedia(self):
        with patch.object(main, "get_wikipedia_summary") as wiki:
            for body in [{"landmark": "   "}, {"landmark": "CN Tower", "length": "huge"}, {"landmark": "x" * 201}]:
                self.assertEqual(self.client.post("/generate-story", json=body).status_code, 422)
            wiki.assert_not_called()

    def test_story_contract(self):
        with patch.object(main, "get_wikipedia_summary", return_value="Toronto landmark."), patch.object(main, "generate_text", return_value="A tower above Toronto."):
            response = self.client.post("/generate-story", json={"landmark": " CN Tower "})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"landmark": "CN Tower", "summary": "Toronto landmark.", "story": "A tower above Toronto."})

    def test_missing_summary(self):
        with patch.object(main, "get_wikipedia_summary", return_value=None):
            self.assertEqual(self.client.post("/generate-story", json={"landmark": "Missing"}).status_code, 404)

    def test_provider_error_does_not_expose_details(self):
        provider = Mock()
        provider.responses.create.side_effect = RuntimeError("private provider details")
        with patch.object(main, "get_client", return_value=provider), patch.object(main, "get_wikipedia_summary", return_value="Summary"), self.assertLogs(main.logger, level="ERROR"):
            response = self.client.post("/generate-story", json={"landmark": "CN Tower"})
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("private provider details", response.text)

    def test_empty_provider_output(self):
        provider = Mock()
        provider.responses.create.return_value = SimpleNamespace(output_text=" ")
        with patch.object(main, "get_client", return_value=provider), self.assertLogs(main.logger, level="ERROR"):
            response = self.client.post("/recognize-landmark", files={"image": ("photo.jpg", b"image", "image/jpeg")})
        self.assertEqual(response.status_code, 502)

    def test_wikipedia_title_with_slash_and_unicode(self):
        search = Mock(status_code=200)
        search.json.return_value = {"query": {"search": [{"title": "Château/Test"}]}}
        summary = Mock(status_code=200)
        summary.json.return_value = {"query": {"pages": {"1": {"extract": "A landmark"}}}}
        with patch.object(main.requests, "get", side_effect=[search, summary]) as get:
            self.assertEqual(main.get_wikipedia_summary("Château/Test"), "A landmark")
        self.assertEqual(get.call_args.kwargs["params"]["titles"], "Château/Test")
        self.assertEqual(get.call_args.kwargs["params"]["redirects"], 1)


if __name__ == "__main__":
    unittest.main()
