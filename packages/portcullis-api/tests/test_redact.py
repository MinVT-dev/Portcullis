# SPDX-License-Identifier: MIT
from __future__ import annotations

import logging

from portcullis_api.redact import MASK, RedactingFilter, Redactor

JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJwb3J0Y3VsbGlzIn0.abcdefghij"


def test_known_secrets_and_patterns() -> None:
    r = Redactor(["hunter2-long-password"])
    text = r.text(f"pw=hunter2-long-password Authorization: Bearer {JWT} url=/hubs/shard?access_token=abc123&x=1")
    assert "hunter2" not in text and JWT not in text and "abc123" not in text
    assert "x=1" in text


def test_secret_keys_in_data() -> None:
    r = Redactor()
    out = r.data({"token": "t-123456", "expiresAt": "2026-11-07T20:00:00Z", "nested": [{"password": "p"}],
                  "name": "Steve", "text": f"leaked {JWT}"})
    assert out["token"] == MASK and out["nested"][0]["password"] == MASK
    assert out["expiresAt"] == "2026-11-07T20:00:00Z" and out["name"] == "Steve"
    assert JWT not in out["text"]


def test_url_masks_connection_id() -> None:
    assert Redactor().url("ws://h/hubs/shard?id=conn-abc&access_token=t") == \
        f"ws://h/hubs/shard?id={MASK}&access_token={MASK}"


def test_logging_filter(caplog) -> None:  # noqa: ANN001
    r = Redactor(["sekret-value-123"])
    logger = logging.getLogger("redact-test")
    logger.addFilter(RedactingFilter(r))
    with caplog.at_level(logging.INFO, logger="redact-test"):
        logger.info("token is %s", "sekret-value-123")
    assert "sekret-value-123" not in caplog.text and MASK in caplog.text


def test_short_lived_secrets_are_capped_and_permanent_ones_are_kept() -> None:
    import logging

    from portcullis_api.redact import RedactingFilter, Redactor

    r = Redactor(["the-password"], max_short_lived=3)
    for n in range(10):
        r.add(f"ticket-{n:04d}", short_lived=True)
    assert len(r._known()) == 4  # the password and the three newest tickets
    assert r.text("the-password ticket-0009 ticket-0000") == "<redacted> <redacted> ticket-0000"

    # A traceback is redacted too: aiohttp errors carry the hub URL with its ticket.
    try:
        raise RuntimeError("GET wss://amp/hubs/shard?access_token=abc123secret failed, the-password")
    except RuntimeError:
        import sys
        record = logging.LogRecord("x", logging.ERROR, __file__, 1, "boom", None, sys.exc_info())
    assert RedactingFilter(r).filter(record)
    shown = logging.Formatter("%(message)s").format(record)
    assert "abc123secret" not in shown and "the-password" not in shown and "RuntimeError" in shown
