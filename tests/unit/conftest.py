from __future__ import annotations

import pytest

# Credentials the code legitimately reads from the environment. A unit test must
# never depend on whether the developer happens to have these set — that made
# test_factory_returns_null_when_no_key pass locally for whoever had no .env and
# fail for everyone else.
_CREDENTIAL_VARS = ("LLM_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "USDA_API_KEY")


@pytest.fixture(autouse=True)
def _isolate_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run unit tests as if no API credentials are configured.

    A test that wants one sets it explicitly with monkeypatch.setenv, which
    still wins because it runs inside the test body, after this fixture.
    """
    for var in _CREDENTIAL_VARS:
        monkeypatch.delenv(var, raising=False)
