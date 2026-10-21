import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from hakimi_analysis.ai_budget import AiBudgetError, AiBudgetLedger, BudgetedAsyncClient, token_cost


def test_budget_is_atomic_and_survives_reopen(tmp_path: Path) -> None:
    path = tmp_path / "budget.sqlite"
    ledger = AiBudgetLedger(path, total_limit=100, daily_limit=100)

    def reserve(_: int) -> str | None:
        try:
            return ledger.reserve(30, kind="model", model="fixture")
        except AiBudgetError:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        attempts = [item for item in pool.map(reserve, range(10)) if item]
    assert len(attempts) == 3
    assert (
        AiBudgetLedger(path, total_limit=100, daily_limit=100).snapshot()["used_micro_yuan"] == 90
    )
    ledger.settle(attempts[0], 5)
    ledger.settle(attempts[0], 5)
    assert ledger.snapshot()["used_micro_yuan"] == 65
    with pytest.raises(ValueError):
        ledger.settle(attempts[0], 0)
    with pytest.raises(ValueError):
        AiBudgetLedger(path, total_limit=200, daily_limit=100)


def test_day_rollover_uses_shanghai_and_does_not_reset_total(tmp_path: Path) -> None:
    clock = [datetime(2026, 10, 21, 15, 59, tzinfo=UTC)]
    ledger = AiBudgetLedger(
        tmp_path / "budget.sqlite", total_limit=100, daily_limit=60, now=lambda: clock[0]
    )
    ledger.reserve(60, kind="model", model="fixture")
    with pytest.raises(AiBudgetError):
        ledger.reserve(1, kind="model", model="fixture")
    clock[0] = datetime(2026, 10, 21, 16, 1, tzinfo=UTC)
    ledger.reserve(40, kind="model", model="fixture")
    with pytest.raises(AiBudgetError):
        ledger.reserve(1, kind="model", model="fixture")


@pytest.mark.asyncio
async def test_http_settles_usage_but_retains_unknown_and_cancelled_attempts(
    tmp_path: Path,
) -> None:
    ledger = AiBudgetLedger(tmp_path / "budget.sqlite")
    count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        if count == 3:
            raise asyncio.CancelledError()
        return httpx.Response(
            200, json={"usage": {"input_tokens": 100, "output_tokens": 20}} if count == 1 else {}
        )

    async with BudgetedAsyncClient(budget=ledger, transport=httpx.MockTransport(handler)) as client:
        kwargs: dict[str, Any] = {
            "json": {"model": "doubao-seed-2-1-pro-260915", "input": "fixture"}
        }
        await client.post("https://ark.cn-beijing.volces.com/api/v3/responses", **kwargs)
        assert ledger.snapshot()["used_micro_yuan"] == token_cost(kwargs["json"]["model"], 100, 20)
        await client.post("https://ark.cn-beijing.volces.com/api/v3/responses", **kwargs)
        with pytest.raises(asyncio.CancelledError):
            await client.post("https://ark.cn-beijing.volces.com/api/v3/responses", **kwargs)
        assert ledger.snapshot()["pending_attempts"] == 2
        with pytest.raises(AiBudgetError):
            await client.post("https://unknown.invalid/responses", **kwargs)


def test_unknown_model_is_not_admitted() -> None:
    with pytest.raises(AiBudgetError):
        token_cost("unknown", 1, 1)


@pytest.mark.asyncio
async def test_denial_happens_before_http_and_usage_breach_blocks_after_reopen(
    tmp_path: Path,
) -> None:
    count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        return httpx.Response(200, json={"usage": {"input_tokens": 1_000_000, "output_tokens": 0}})

    path = tmp_path / "ledger.sqlite"
    ledger = AiBudgetLedger(path)
    async with BudgetedAsyncClient(budget=ledger, transport=httpx.MockTransport(handler)) as client:
        await client.post(
            "https://ark.cn-beijing.volces.com/api/v3/responses",
            json={"model": "doubao-seed-2-1-pro-260915", "input": "fixture"},
        )
        assert ledger.snapshot()["used_micro_yuan"] == 6_000_000
        with pytest.raises(AiBudgetError):
            await client.post(
                "https://ark.cn-beijing.volces.com/api/v3/responses",
                json={"model": "doubao-seed-2-1-pro-260915"},
            )
        assert count == 1
    with pytest.raises(AiBudgetError):
        AiBudgetLedger(path).reserve(1, kind="model", model="fixture")


@pytest.mark.asyncio
async def test_asr_every_retry_is_reserved_and_failed_attempt_stays_charged(tmp_path: Path) -> None:
    import wave

    from hakimi_analysis.models import Transcript
    from hakimi_analysis.providers.asr import VolcAsrClient
    from hakimi_analysis.providers.base import ProviderError

    ledger = AiBudgetLedger(tmp_path / "ledger.sqlite")
    calls = 0

    class RetryingAsr(VolcAsrClient):
        async def _recognize_once(
            self, *, chunks: list[bytes], window_start_seconds: float, connect_id: str
        ) -> Transcript:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ProviderError("provider_error", "fixture", retryable=True)
            return Transcript(text="", utterances=[], provider_request_id=None)

    audio = tmp_path / "fixture.wav"
    with wave.open(str(audio), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(bytes(32001 * 2))
    client = RetryingAsr(
        api_key="fixture",
        resource_id="volc.seedasr.sauc.duration",
        url="wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream",
        retry_delays=(0,),
        budget=ledger,
    )
    await client.recognize(audio_path=audio, window_start_seconds=0, request_id="fixture")
    assert calls == 2
    assert ledger.snapshot()["used_micro_yuan"] == 7_500
    assert ledger.snapshot()["pending_attempts"] == 1


@pytest.mark.asyncio
async def test_chat_reserves_reasoning_and_answer_together_without_unknown_usage_refund(
    tmp_path: Path,
) -> None:
    import json

    model = "doubao-seed-2-0-mini-260428"
    ledger = AiBudgetLedger(tmp_path / "ledger.sqlite")

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["max_completion_tokens"] == 4096
        assert "max_tokens" not in body
        return httpx.Response(200, json={})

    async with BudgetedAsyncClient(budget=ledger, transport=httpx.MockTransport(handler)) as client:
        await client.post(
            "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
            json={"model": model, "messages": [], "max_tokens": 500},
        )
    assert ledger.snapshot()["used_micro_yuan"] >= token_cost(model, 0, 4096)
    assert AiBudgetLedger(ledger.path).snapshot()["pending_attempts"] == 1
