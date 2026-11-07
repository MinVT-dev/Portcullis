# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

import logging

from portcullis.amp.redact import MASK, RedactingFilter, Redactor

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
