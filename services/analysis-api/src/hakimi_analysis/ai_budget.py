"""Persistent, fail-closed admission for every paid provider attempt.

Amounts are integer micro-yuan. Unsettled attempts remain charged at their
reservation, including after process death, cancellation or a code rollback.
Only identifiers and numeric accounting data are stored, never source content.
"""

import json
import math
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from hakimi_analysis.providers.base import ProviderError

YUAN = 1_000_000
PRICE_VERSION = "volcengine-cn-beijing-20261021"
PRO_MODELS = frozenset({"doubao-seed-2-1-pro-260915", "doubao-seed-2-1-pro-260628"})
MINI_MODEL = "doubao-seed-2-0-mini-260428"
VIDEO_INPUT_TOKEN_BOUND = 81_920
OUTPUT_TOKEN_LIMIT = 16_384


class AiBudgetError(ProviderError):
    def __init__(self, code: str = "ai_budget_exhausted") -> None:
        super().__init__(code, "AI 体验额度暂不可用，可以继续编辑方案和训练。", retryable=False)


class AiBudgetLedger:
    def __init__(
        self,
        path: Path,
        *,
        total_limit: int = 50 * YUAN,
        daily_limit: int = 5 * YUAN,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if total_limit <= 0 or daily_limit <= 0:
            raise ValueError("AI budget limits must be positive")
        self.path = path
        self._now = now or (lambda: datetime.now(UTC))
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS budget_limits (
                    id INTEGER PRIMARY KEY CHECK(id=1), total_limit INTEGER NOT NULL,
                    daily_limit INTEGER NOT NULL, blocked INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS ai_attempts (
                    id TEXT PRIMARY KEY, day TEXT NOT NULL, kind TEXT NOT NULL,
                    model TEXT NOT NULL, reserved INTEGER NOT NULL CHECK(reserved>0),
                    charged INTEGER CHECK(charged>=0), price_version TEXT NOT NULL
                );
            """)
            db.execute(
                "INSERT OR IGNORE INTO budget_limits(id,total_limit,daily_limit) VALUES(1,?,?)",
                (total_limit, daily_limit),
            )
            limits = db.execute(
                "SELECT total_limit,daily_limit FROM budget_limits WHERE id=1"
            ).fetchone()
            if limits != (total_limit, daily_limit):
                raise ValueError("Existing AI budget limits must not be reset by deployment")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10, isolation_level="DEFERRED")
        try:
            with db:
                yield db
        finally:
            db.close()

    def _day(self) -> str:
        return (
            self._now().astimezone(timezone(timedelta(hours=8), "Asia/Shanghai")).date().isoformat()
        )

    def reserve(self, amount: int, *, kind: str, model: str) -> str:
        if not isinstance(amount, int) or amount <= 0:
            raise ValueError("Reservation must be a positive integer")
        try:
            day = self._day()
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                limits = db.execute(
                    "SELECT total_limit,daily_limit,blocked FROM budget_limits WHERE id=1"
                ).fetchone()
                total, daily = db.execute(
                    "SELECT COALESCE(SUM(COALESCE(charged,reserved)),0), "
                    "COALESCE(SUM(CASE WHEN day=? THEN COALESCE(charged,reserved) ELSE 0 END),0) "
                    "FROM ai_attempts",
                    (day,),
                ).fetchone()
                if (
                    not limits
                    or limits[2]
                    or total + amount > limits[0]
                    or daily + amount > limits[1]
                ):
                    raise AiBudgetError()
                attempt_id = str(uuid4())
                db.execute(
                    "INSERT INTO ai_attempts VALUES(?,?,?,?,?,NULL,?)",
                    (attempt_id, day, kind, model, amount, PRICE_VERSION),
                )
                return attempt_id
        except sqlite3.Error as error:
            raise AiBudgetError("ai_budget_unavailable") from error

    def settle(self, attempt_id: str, amount: int) -> None:
        if not isinstance(amount, int) or amount < 0:
            raise ValueError("Charge must be a nonnegative integer")
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT reserved,charged FROM ai_attempts WHERE id=?", (attempt_id,)
                ).fetchone()
                if row is None:
                    raise ValueError("Unknown AI budget reservation")
                if row[1] is not None:
                    if row[1] != amount:
                        raise ValueError("AI budget settlement cannot be overwritten")
                    return
                db.execute("UPDATE ai_attempts SET charged=? WHERE id=?", (amount, attempt_id))
                if amount > row[0]:
                    db.execute("UPDATE budget_limits SET blocked=1 WHERE id=1")
        except sqlite3.Error as error:
            raise AiBudgetError("ai_budget_unavailable") from error

    def block(self) -> None:
        with self._connect() as db:
            db.execute("UPDATE budget_limits SET blocked=1 WHERE id=1")

    def snapshot(self) -> dict[str, int]:
        with self._connect() as db:
            used, daily, pending = db.execute(
                "SELECT COALESCE(SUM(COALESCE(charged,reserved)),0), "
                "COALESCE(SUM(CASE WHEN day=? THEN COALESCE(charged,reserved) ELSE 0 END),0), "
                "SUM(CASE WHEN charged IS NULL THEN 1 ELSE 0 END) FROM ai_attempts",
                (self._day(),),
            ).fetchone()
            return {
                "used_micro_yuan": used,
                "daily_micro_yuan": daily,
                "pending_attempts": pending or 0,
            }


def token_cost(model: str, input_tokens: int, output_tokens: int) -> int:
    if model in PRO_MODELS:
        input_rate, output_rate = 6_000, 30_000
    elif model == MINI_MODEL:
        input_rate, output_rate = (
            (200, 2_000)
            if input_tokens <= 32_768
            else (400, 4_000)
            if input_tokens <= 131_072
            else (800, 8_000)
        )
    else:
        raise AiBudgetError("ai_budget_unavailable")
    return math.ceil((input_tokens * input_rate + output_tokens * output_rate) / 1_000)


def _input_bound(value: Any) -> int:
    if isinstance(value, dict):
        if value.get("type") == "input_video":
            # Only our FFmpeg-generated MP4 chunks are admitted. They contain a
            # frame count; Ark's documented video budget is 80K tokens per video.
            url = value.get("video_url", "")
            if not isinstance(url, str) or not url.startswith("data:video/mp4;base64,"):
                raise AiBudgetError("ai_budget_unavailable")
            return VIDEO_INPUT_TOKEN_BOUND + 256
        if value.get("type") == "input_image":
            raise AiBudgetError("ai_budget_unavailable")
        return sum(len(str(key).encode()) + _input_bound(item) + 8 for key, item in value.items())
    if isinstance(value, list):
        return sum(_input_bound(item) + 8 for item in value)
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


class BudgetedAsyncClient(httpx.AsyncClient):
    def __init__(self, *, budget: AiBudgetLedger, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.budget = budget

    async def request(self, method: str, url: httpx.URL | str, **kwargs: Any) -> httpx.Response:
        if method.upper() != "POST":
            return await super().request(method, url, **kwargs)
        target = httpx.URL(url)
        body = kwargs.get("json")
        if (
            target.host != "ark.cn-beijing.volces.com"
            or target.scheme != "https"
            or target.path not in {"/api/v3/responses", "/api/v3/chat/completions"}
            or not isinstance(body, dict)
            or body.get("model") not in PRO_MODELS | {MINI_MODEL}
        ):
            raise AiBudgetError("ai_budget_unavailable")
        body = dict(body)
        output_key = (
            "max_output_tokens" if target.path.endswith("responses") else "max_completion_tokens"
        )
        limit = OUTPUT_TOKEN_LIMIT if output_key == "max_output_tokens" else 4_096
        if output_key == "max_completion_tokens":
            body.pop("max_tokens", None)
        body[output_key] = min(body.get(output_key, limit), limit)
        kwargs["json"] = body
        input_bound = _input_bound(body) + 2_048
        model = body["model"]
        attempt = self.budget.reserve(
            token_cost(model, input_bound, body[output_key]), kind="model", model=model
        )
        response = await super().request(method, url, **kwargs)
        try:
            usage = response.json().get("usage", {})
            input_tokens = usage.get("input_tokens", usage.get("prompt_tokens"))
            output_tokens = usage.get("output_tokens", usage.get("completion_tokens"))
            if (
                type(input_tokens) is int
                and type(output_tokens) is int
                and input_tokens >= 0
                and output_tokens >= 0
            ):
                self.budget.settle(attempt, token_cost(model, input_tokens, output_tokens))
                if input_tokens > input_bound or output_tokens > body[output_key]:
                    self.budget.block()
        except (ValueError, AttributeError, TypeError):
            pass  # Missing/unknown usage remains reserved, including HTTP errors.
        return response
