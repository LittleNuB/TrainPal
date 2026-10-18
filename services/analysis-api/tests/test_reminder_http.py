"""Synthetic accepted tips through the public HTTP reminder boundary."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from starlette.types import Message, Scope

from hakimi_analysis.app import create_app
from hakimi_analysis.providers.ark import ArkResponsesClient
from hakimi_analysis.reminders import TrainingReminderPlanner

FINGERPRINT_VECTORS = json.loads(
    (
        Path(__file__).resolve().parents[3] / "contracts/fixtures/training-reminders-v1.1.json"
    ).read_text(encoding="utf-8")
)


@pytest.mark.parametrize("vector", FINGERPRINT_VECTORS, ids=lambda vector: vector["case"])
async def test_http_matches_the_shared_known_input_fingerprint(vector: dict[str, object]) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(reminder_planner=TrainingReminderPlanner())),
        base_url="http://test",
    ) as client:
        response = await client.post("/api/v1/training-reminders", json=vector["request"])
    assert response.status_code == 200
    assert response.json()["input_fingerprint"] == vector["expected_fingerprint"]
    assert response.json()["contract_version"] == "training-reminders.v1.1"


def test_http_contract_publishes_the_same_bounded_nested_input() -> None:
    operation = create_app().openapi()["paths"]["/api/v1/training-reminders"]["post"]
    schema = operation["requestBody"]["content"]["application/json"]["schema"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["actions"]["maxItems"] == 200
    action = schema["properties"]["actions"]["items"]
    assert action["properties"]["tips"]["maxItems"] == 5
    tip = action["properties"]["tips"]["items"]
    assert tip["properties"]["tip"]["properties"]["text"]["maxLength"] == 80
    assert tip["properties"]["review_status"]["enum"] == ["approved", "unknown", "rejected"]
    assert "$defs" not in json.dumps(schema)


def reminder_payload() -> dict[str, object]:
    return {
        "plan_id": "synthetic-plan",
        "plan_revision": 3,
        "preferences": {"automatic_speech": True, "confirmed": True},
        "actions": [
            {
                "action_id": "synthetic-action",
                "action_revision": 1,
                "name": "深蹲",
                "confirmed": True,
                "source_id": "synthetic-source",
                "source_segment": {"start_seconds": 0, "end_seconds": 20},
                "mode": "duration",
                "sets": 1,
                "duration_seconds": 15,
                "tips": [
                    {
                        "tip_id": "synthetic-tip",
                        "review_status": "approved",
                        "review_action_id": "synthetic-action",
                        "review_action_revision": 1,
                        "source_id": "synthetic-source",
                        "tip": {
                            "text": "起身时呼气",
                            "category": "breathing",
                            "evidence": {"type": "speech", "start_seconds": 5, "end_seconds": 8},
                        },
                    }
                ],
            }
        ],
    }


async def test_http_returns_bound_rule_schedule_without_changing_the_plan() -> None:
    app = create_app(reminder_planner=TrainingReminderPlanner())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/api/v1/training-reminders", json=reminder_payload())
    assert response.status_code == 200
    data = response.json()
    assert data["plan_id"] == "synthetic-plan"
    assert data["plan_revision"] == 3
    assert data["mode"] == "rules"
    assert data["fallback_reason"] == "model_unavailable"
    assert data["reminders"] == [
        {
            "action_id": "synthetic-action",
            "action_revision": 1,
            "tip_id": "synthetic-tip",
            "stage": "training",
            "trigger": "set_start",
            "set_number": 1,
            "active_seconds": None,
            "priority": 1,
            "max_plays": 1,
        }
    ]
    assert response.headers["cache-control"] == "no-store"


async def test_http_fails_closed_and_rejects_cross_origin_oversized_and_invalid_input() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        assert (
            await client.post("/api/v1/training-reminders", json=reminder_payload())
        ).status_code == 503
        assert (
            await client.post(
                "/api/v1/training-reminders",
                json=reminder_payload(),
                headers={"Origin": "https://other.invalid"},
            )
        ).status_code == 403
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(reminder_planner=TrainingReminderPlanner())),
        base_url="http://test",
    ) as client:
        assert (
            await client.post("/api/v1/training-reminders", content=b"x" * 64001)
        ).status_code == 413
        assert (
            await client.post("/api/v1/training-reminders", content=b"not-json")
        ).status_code == 422
        payload = reminder_payload()
        payload["raw_transcript"] = "not allowed"
        assert (await client.post("/api/v1/training-reminders", json=payload)).status_code == 422
        assert (
            await client.post("/api/v1/training-reminders", json=reminder_payload())
        ).status_code == 200


async def test_http_overflow_does_not_queue_and_cancelled_request_releases_capacity() -> None:
    entered: asyncio.Queue[None] = asyncio.Queue()
    release = asyncio.Event()

    async def respond(request: httpx.Request) -> httpx.Response:
        data = json.loads(json.loads(request.content)["input"][0]["content"][0]["text"])
        entered.put_nowait(None)
        await release.wait()
        return httpx.Response(
            200,
            json={
                "output_text": json.dumps(
                    {
                        "input_fingerprint": data["input_fingerprint"],
                        "rankings": [
                            {"action_id": "synthetic-action", "tip_ids": ["synthetic-tip"]}
                        ],
                    }
                )
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as provider:
        planner = TrainingReminderPlanner(
            ArkResponsesClient(
                api_key="synthetic",
                model_id="existing",
                base_url="https://provider.invalid",
                http_client=provider,
            )
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(reminder_planner=planner)),
            base_url="http://test",
        ) as client:
            first = asyncio.create_task(
                client.post("/api/v1/training-reminders", json=reminder_payload())
            )
            second = asyncio.create_task(
                client.post("/api/v1/training-reminders", json=reminder_payload())
            )
            recovery = None
            try:
                await asyncio.wait_for(entered.get(), 1)
                await asyncio.wait_for(entered.get(), 1)
                overflow = await asyncio.wait_for(
                    client.post("/api/v1/training-reminders", json=reminder_payload()), 0.2
                )
                assert overflow.status_code == 429
                first.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await first
                recovery = asyncio.create_task(
                    client.post("/api/v1/training-reminders", json=reminder_payload())
                )
                await asyncio.wait_for(entered.get(), 1)
                release.set()
                assert (await recovery).json()["mode"] == "model"
                assert (await second).status_code == 200
            finally:
                release.set()
                tasks = [first, second, *([recovery] if recovery is not None else [])]
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)


async def test_browser_disconnect_cancels_ranking_and_releases_both_http_slots() -> None:
    entered: asyncio.Queue[None] = asyncio.Queue()
    cancelled: asyncio.Queue[None] = asyncio.Queue()
    release = asyncio.Event()

    async def respond(request: httpx.Request) -> httpx.Response:
        data = json.loads(json.loads(request.content)["input"][0]["content"][0]["text"])
        entered.put_nowait(None)
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.put_nowait(None)
            raise
        return httpx.Response(
            200,
            json={
                "output_text": json.dumps(
                    {
                        "input_fingerprint": data["input_fingerprint"],
                        "rankings": [
                            {"action_id": "synthetic-action", "tip_ids": ["synthetic-tip"]}
                        ],
                    }
                )
            },
        )

    async def discard_message(message: Message) -> None:
        pass

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as provider:
        app = create_app(
            reminder_planner=TrainingReminderPlanner(
                ArkResponsesClient(
                    api_key="synthetic",
                    model_id="existing",
                    base_url="https://provider.invalid",
                    http_client=provider,
                )
            )
        )
        scope: Scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/v1/training-reminders",
            "raw_path": b"/api/v1/training-reminders",
            "query_string": b"",
            "root_path": "",
            "headers": [(b"host", b"test"), (b"content-type", b"application/json")],
            "client": ("127.0.0.1", 1234),
            "server": ("test", 80),
        }
        queues: list[asyncio.Queue[Message]] = [asyncio.Queue(), asyncio.Queue()]
        for queue in queues:
            queue.put_nowait(
                {
                    "type": "http.request",
                    "body": json.dumps(reminder_payload()).encode(),
                    "more_body": False,
                }
            )
        tasks = [asyncio.create_task(app(scope, queue.get, discard_message)) for queue in queues]
        recovery = None
        try:
            for _ in tasks:
                await asyncio.wait_for(entered.get(), 1)
            for queue in queues:
                queue.put_nowait({"type": "http.disconnect"})
            # Only send real ASGI disconnects; do not cancel the caller tasks.
            for _ in tasks:
                await asyncio.wait_for(cancelled.get(), 0.5)
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 1)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                recovery = asyncio.create_task(
                    client.post("/api/v1/training-reminders", json=reminder_payload())
                )
                await asyncio.wait_for(entered.get(), 1)
                release.set()
                assert (await recovery).json()["mode"] == "model"
        finally:
            release.set()
            for task in tasks:
                task.cancel()
            if recovery is not None:
                recovery.cancel()
            await asyncio.gather(
                *tasks, *([recovery] if recovery is not None else []), return_exceptions=True
            )
