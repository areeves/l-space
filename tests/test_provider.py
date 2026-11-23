import httpx

from l_space.core.settings import Settings
from l_space.models.provider import OllamaProvider, from_settings, router_from_settings


def settings(**overrides) -> Settings:
    # _env_file=None so a developer's local .env can't change what these tests see
    return Settings(_env_file=None, ollama_model="llama3.2", **overrides)


def test_router_defaults_to_the_writer_provider():
    s = settings()
    writer = from_settings(s)
    assert router_from_settings(s, writer) is writer


def test_blank_or_identical_router_model_reuses_the_writer():
    writer = from_settings(settings())
    assert router_from_settings(settings(ollama_router_model=""), writer) is writer
    assert router_from_settings(settings(ollama_router_model="llama3.2"), writer) is writer


def test_a_different_router_model_gets_its_own_provider():
    s = settings(ollama_router_model="qwen2.5:7b")
    writer = from_settings(s)
    router = router_from_settings(s, writer)
    assert router is not writer
    assert (router.model, writer.model) == ("qwen2.5:7b", "llama3.2")
    assert router.base_url == writer.base_url


def test_request_payload(monkeypatch):
    sent = {}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"response": "hello"}

    def fake_post(url, json, timeout):
        sent.update(url=url, json=json, timeout=timeout)
        return Response()

    monkeypatch.setattr(httpx, "post", fake_post)
    provider = OllamaProvider("http://ollama:11434/", "llama3.2", timeout=12)

    reply = provider.generate("hi", system="be brief", schema={"type": "object"}, max_tokens=50)

    assert reply == "hello"
    assert sent["url"] == "http://ollama:11434/api/generate"
    assert sent["timeout"] == 12
    assert sent["json"] == {
        "model": "llama3.2",
        "prompt": "hi",
        "stream": False,
        "options": {"temperature": 0, "num_predict": 50},
        "system": "be brief",
        "format": {"type": "object"},
    }


def test_optional_fields_are_omitted_when_unused(monkeypatch):
    sent = {}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"response": ""}

    monkeypatch.setattr(httpx, "post", lambda url, json, timeout: sent.update(json=json) or Response())
    OllamaProvider("http://x", "m").generate("hi")
    assert sent["json"] == {
        "model": "m", "prompt": "hi", "stream": False, "options": {"temperature": 0},
    }
