"""Synthetic audited inputs at the public Skill seam, never real media evidence."""

import asyncio
import json

import httpx
import pytest

from hakimi_analysis.providers.ark import ArkResponsesClient
from hakimi_analysis.reminders import ReminderPlanRequest, TrainingReminderPlanner


def short_plan() -> ReminderPlanRequest:
    return ReminderPlanRequest.model_validate(
        {
            "plan_id": "plan-example",
            "plan_revision": 7,
            "preferences": {"automatic_speech": True, "confirmed": True},
            "actions": [
                {
                    "action_id": "action-squat",
                    "action_revision": 2,
                    "name": "深蹲",
                    "confirmed": True,
                    "source_id": "source-example",
                    "source_segment": {"start_seconds": 0, "end_seconds": 60},
                    "mode": "duration",
                    "sets": 1,
                    "reps": None,
                    "duration_seconds": 15,
                    "tips": [
                        {
                            "tip_id": f"tip-{number}",
                            "review_status": "approved",
                            "review_action_id": "action-squat",
                            "review_action_revision": 2,
                            "source_id": "source-example",
                            "tip": {
                                "text": text,
                                "category": "path",
                                "evidence": {
                                    "type": "speech",
                                    "start_seconds": number * 2,
                                    "end_seconds": number * 2 + 1,
                                },
                            },
                        }
                        for number, text in enumerate(
                            [
                                "保持脚掌稳定",
                                "注意下降的路径",
                                "保持动作平稳",
                                "起身时呼气",
                                "膝盖跟随脚尖方向",
                            ],
                            1,
                        )
                    ],
                }
            ],
        }
    )


async def test_short_set_uses_ranked_fifth_tip_without_squeezing_in_all_five() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        wire = json.loads(request.content)
        data = json.loads(wire["input"][0]["content"][0]["text"])
        # The fifth input remains available to the ranking model.
        assert [tip["tip_id"] for tip in data["actions"][0]["tips"]] == [
            "tip-1",
            "tip-2",
            "tip-3",
            "tip-4",
            "tip-5",
        ]
        assert wire["store"] is False
        return httpx.Response(
            200,
            json={
                "output_text": json.dumps(
                    {
                        "input_fingerprint": data["input_fingerprint"],
                        "rankings": [
                            {
                                "action_id": "action-squat",
                                "tip_ids": ["tip-5", "tip-1", "tip-2", "tip-3", "tip-4"],
                            }
                        ],
                    }
                )
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        provider = ArkResponsesClient(
            api_key="synthetic-key",
            model_id="existing-test-model",
            base_url="https://provider.invalid",
            http_client=http,
        )
        result = await TrainingReminderPlanner(provider).plan(short_plan())
    assert result.mode == "model"
    assert result.plan_id == "plan-example"
    assert result.plan_revision == 7
    assert result.contract_version == "training-reminders.v1.1"
    assert result.skill_version == "1.0.0"
    assert result.policy_version == "ReminderPolicy.v1"
    assert [budget.model_dump() for budget in result.budgets] == [
        {
            "action_id": "action-squat",
            "utterance_limit": 1,
            "active_interval_seconds": 30,
            "end_quiet_seconds": 10,
        }
    ]
    assert [event.model_dump() for event in result.reminders] == [
        {
            "action_id": "action-squat",
            "action_revision": 2,
            "tip_id": "tip-5",
            "stage": "training",
            "trigger": "set_start",
            "set_number": 1,
            "active_seconds": None,
            "priority": 1,
            "max_plays": 1,
        }
    ]


async def test_long_multiset_spreads_different_tips_and_keeps_setup_before_training() -> None:
    request = short_plan()
    action = request.actions[0]
    action.sets = 3
    action.duration_seconds = 60
    action.tips[0].tip.category = "setup"
    result = await TrainingReminderPlanner().plan(request)
    assert [
        (event.tip_id, event.stage, event.trigger, event.set_number, event.active_seconds)
        for event in result.reminders
    ] == [
        ("tip-1", "prepare", "action_prepare", 1, None),
        ("tip-2", "training", "set_start", 1, None),
        ("tip-3", "training", "set_start", 2, None),
        ("tip-4", "training", "set_start", 3, None),
        ("tip-5", "training", "active_elapsed", 1, 30),
    ]


async def test_only_audited_current_action_source_tips_reach_the_model() -> None:
    request = short_plan()
    action = request.actions[0]
    action.tips[0].review_status = "unknown"
    action.tips[1].review_action_id = "different-action"
    action.tips[2].source_id = "different-source"
    action.tips[3].review_action_revision = 1

    def respond(http_request: httpx.Request) -> httpx.Response:
        data = json.loads(json.loads(http_request.content)["input"][0]["content"][0]["text"])
        assert [tip["tip_id"] for tip in data["actions"][0]["tips"]] == ["tip-5"]
        return httpx.Response(
            200,
            json={
                "output_text": json.dumps(
                    {
                        "input_fingerprint": data["input_fingerprint"],
                        "rankings": [{"action_id": "action-squat", "tip_ids": ["tip-5"]}],
                    }
                )
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        result = await TrainingReminderPlanner(
            ArkResponsesClient(
                api_key="synthetic",
                model_id="existing",
                base_url="https://provider.invalid",
                http_client=http,
            )
        ).plan(request)
    assert result.mode == "model"
    assert [event.tip_id for event in result.reminders] == ["tip-5"]


@pytest.mark.parametrize("failure", ["unknown", "duplicate", "missing", "stale", "http"])
async def test_model_failure_uses_rules_without_retries_or_changing_training(failure: str) -> None:
    request = short_plan()
    before = request.model_dump()
    calls: list[httpx.Request] = []

    def respond(http_request: httpx.Request) -> httpx.Response:
        calls.append(http_request)
        if failure == "http":
            return httpx.Response(500)
        data = json.loads(json.loads(http_request.content)["input"][0]["content"][0]["text"])
        ids = ["tip-1", "tip-2", "tip-3", "tip-4", "tip-5"]
        if failure == "unknown":
            ids[-1] = "invented"
        elif failure == "duplicate":
            ids[-1] = "tip-1"
        elif failure == "missing":
            ids.pop()
        return httpx.Response(
            200,
            json={
                "output_text": json.dumps(
                    {
                        "input_fingerprint": "0" * 64
                        if failure == "stale"
                        else data["input_fingerprint"],
                        "rankings": [{"action_id": "action-squat", "tip_ids": ids}],
                    }
                )
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        result = await TrainingReminderPlanner(
            ArkResponsesClient(
                api_key="synthetic",
                model_id="existing",
                base_url="https://provider.invalid",
                http_client=http,
            )
        ).plan(request)
    assert result.mode == "rules"
    assert result.fallback_reason == ("model_failure" if failure == "http" else "invalid_selection")
    assert [event.tip_id for event in result.reminders] == ["tip-1"]
    assert len(calls) == 1
    assert request.model_dump() == before


@pytest.mark.parametrize(
    "condition", ["unconfirmed_preference", "muted", "pending", "empty", "legacy", "too_short"]
)
async def test_no_active_content_or_permission_means_zero_reminders_and_no_model(
    condition: str,
) -> None:
    request = short_plan()
    if condition == "unconfirmed_preference":
        request.preferences.confirmed = False
    elif condition == "muted":
        request.preferences.automatic_speech = False
    elif condition == "pending":
        request.actions[0].confirmed = False
    elif condition == "empty":
        request.actions[0].tips = []
    elif condition == "too_short":
        request.actions[0].duration_seconds = 10
    else:
        for tip in request.actions[0].tips:
            tip.review_status = "unknown"
    calls: list[httpx.Request] = []

    def refuse(http_request: httpx.Request) -> httpx.Response:
        calls.append(http_request)
        raise AssertionError("disabled reminder planning must not call a Provider")

    async with httpx.AsyncClient(transport=httpx.MockTransport(refuse)) as http:
        result = await TrainingReminderPlanner(
            ArkResponsesClient(
                api_key="synthetic",
                model_id="existing",
                base_url="https://provider.invalid",
                http_client=http,
            )
        ).plan(request)
    assert result.mode == "disabled"
    assert result.reminders == []
    assert calls == []


@pytest.mark.parametrize(
    "mode,sets,duration,tip_count,expected",
    [
        ("duration", 1, 10, 5, []),
        ("duration", 1, 15, 5, [("tip-1", 1, None)]),
        (
            "duration",
            3,
            60,
            5,
            [
                ("tip-1", 1, None),
                ("tip-2", 2, None),
                ("tip-3", 3, None),
                ("tip-4", 1, 30),
                ("tip-5", 2, 30),
            ],
        ),
        ("duration", 3, 60, 2, [("tip-1", 1, None), ("tip-2", 2, None)]),
        ("reps", 3, None, 5, [("tip-1", 1, None), ("tip-2", 2, None), ("tip-3", 3, None)]),
    ],
)
async def test_policy_contrasts_use_training_not_source_video_length(
    mode: str,
    sets: int,
    duration: int | None,
    tip_count: int,
    expected: list[tuple[str, int, int | None]],
) -> None:
    raw = short_plan().model_dump()
    action = raw["actions"][0]
    action.update(
        mode=mode, sets=sets, duration_seconds=duration, reps=12 if mode == "reps" else None
    )
    action["tips"] = action["tips"][:tip_count]
    result = await TrainingReminderPlanner().plan(ReminderPlanRequest.model_validate(raw))
    assert [
        (event.tip_id, event.set_number, event.active_seconds) for event in result.reminders
    ] == expected


@pytest.mark.parametrize(
    "bad_input", ["duplicate_action", "duplicate_tip", "missing_reps", "mixed_mode"]
)
def test_invalid_arrangements_are_not_repaired_or_silently_overwritten(bad_input: str) -> None:
    raw = short_plan().model_dump()
    action = raw["actions"][0]
    if bad_input == "duplicate_action":
        raw["actions"].append(action)
    elif bad_input == "duplicate_tip":
        action["tips"][1]["tip_id"] = "tip-1"
    elif bad_input == "missing_reps":
        action.update(mode="reps", duration_seconds=None)
    else:
        action["reps"] = 12
    with pytest.raises(ValueError):
        ReminderPlanRequest.model_validate(raw)


async def test_slow_model_has_bounded_fallback_and_does_not_retry() -> None:
    calls: list[httpx.Request] = []

    async def slow(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        await asyncio.sleep(1)
        raise AssertionError("request should already be cancelled by the deadline")

    async with httpx.AsyncClient(transport=httpx.MockTransport(slow)) as http:
        result = await TrainingReminderPlanner(
            ArkResponsesClient(
                api_key="synthetic",
                model_id="existing",
                base_url="https://provider.invalid",
                http_client=http,
            ),
            timeout_seconds=0.01,
        ).plan(short_plan())
    assert result.mode == "rules"
    assert result.fallback_reason == "model_timeout"
    assert [event.tip_id for event in result.reminders] == ["tip-1"]
    assert len(calls) == 1


@pytest.mark.parametrize("invalid_source", ["outside", "unsafe", "duplicate", "absent"])
async def test_no_new_evidence_or_budget_from_bad_sources(invalid_source: str) -> None:
    request = short_plan()
    action = request.actions[0]
    action.sets = 5
    if invalid_source == "absent":
        action.source_segment = None
        expected: list[str] = []
    elif invalid_source == "duplicate":
        action.tips[4].tip.text = action.tips[0].tip.text
        expected = ["tip-1", "tip-2", "tip-3", "tip-4"]
    else:
        if invalid_source == "outside":
            action.tips[0].tip.evidence.end_seconds = 70
        else:
            action.tips[0].tip.text = "保证治疗膝盖疼痛"
        expected = ["tip-2", "tip-3", "tip-4", "tip-5"]
    result = await TrainingReminderPlanner().plan(request)
    assert [event.tip_id for event in result.reminders] == expected


async def test_input_edit_changes_binding_without_rewriting_an_inflight_snapshot() -> None:
    request = short_plan()
    original = await TrainingReminderPlanner().plan(request)

    def respond(http_request: httpx.Request) -> httpx.Response:
        data = json.loads(json.loads(http_request.content)["input"][0]["content"][0]["text"])
        request.plan_revision = 8
        request.actions[0].duration_seconds = 120
        return httpx.Response(
            200,
            json={
                "output_text": json.dumps(
                    {
                        "input_fingerprint": data["input_fingerprint"],
                        "rankings": [
                            {
                                "action_id": "action-squat",
                                "tip_ids": ["tip-5", "tip-1", "tip-2", "tip-3", "tip-4"],
                            }
                        ],
                    }
                )
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        frozen = await TrainingReminderPlanner(
            ArkResponsesClient(
                api_key="synthetic",
                model_id="existing",
                base_url="https://provider.invalid",
                http_client=http,
            )
        ).plan(request)
    updated = await TrainingReminderPlanner().plan(request)
    assert frozen.plan_revision == 7
    assert frozen.input_fingerprint == original.input_fingerprint
    assert len(frozen.reminders) == 1
    assert updated.plan_revision == 8
    assert updated.input_fingerprint != frozen.input_fingerprint
    assert len(updated.reminders) == 4


async def test_cancellation_does_not_return_a_late_rule_plan() -> None:
    started = asyncio.Event()

    async def pending(_: httpx.Request) -> httpx.Response:
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("cancelled request must not complete")

    async with httpx.AsyncClient(transport=httpx.MockTransport(pending)) as http:
        task = asyncio.create_task(
            TrainingReminderPlanner(
                ArkResponsesClient(
                    api_key="synthetic",
                    model_id="existing",
                    base_url="https://provider.invalid",
                    http_client=http,
                )
            ).plan(short_plan())
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


def test_oversized_input_is_rejected_without_silent_trimming() -> None:
    raw = short_plan().model_dump()
    template = raw["actions"][0]
    raw["actions"] = []
    for number in range(100):
        action = json.loads(json.dumps(template))
        action["action_id"] = f"action-{number}"
        for tip in action["tips"]:
            tip["review_action_id"] = action["action_id"]
        raw["actions"].append(action)
    with pytest.raises(ValueError, match="bounded capacity"):
        ReminderPlanRequest.model_validate(raw)


async def test_near_limit_valid_input_stays_bounded_in_the_actual_model_wire() -> None:
    raw = short_plan().model_dump()
    template = raw["actions"][0]
    raw["actions"] = []
    for number in range(28):
        action = json.loads(json.dumps(template))
        action.update(action_id=f"action-{number:02d}", sets=3, duration_seconds=60)
        for tip_number, tip in enumerate(action["tips"]):
            tip["review_action_id"] = action["action_id"]
            tip["tip"]["text"] = "保持脚掌稳定" + "稳" * 50 + str(tip_number)
        raw["actions"].append(action)
    request = ReminderPlanRequest.model_validate(raw)
    assert 60_000 < len(request.model_dump_json().encode("utf-8")) <= 64_000
    wire_sizes: list[int] = []

    def respond(http_request: httpx.Request) -> httpx.Response:
        text = json.loads(http_request.content)["input"][0]["content"][0]["text"]
        wire_sizes.append(len(text.encode("utf-8")))
        data = json.loads(text)
        assert len(data["actions"]) == 28
        return httpx.Response(
            200,
            json={
                "output_text": json.dumps(
                    {
                        "input_fingerprint": data["input_fingerprint"],
                        "rankings": [
                            {
                                "action_id": action["action_id"],
                                "tip_ids": [tip["tip_id"] for tip in action["tips"]],
                            }
                            for action in data["actions"]
                        ],
                    }
                )
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        result = await TrainingReminderPlanner(
            ArkResponsesClient(
                api_key="synthetic",
                model_id="existing",
                base_url="https://provider.invalid",
                http_client=http,
            )
        ).plan(request)
    assert len(wire_sizes) == 1
    assert 60_000 < wire_sizes[0] <= 64_000
    assert result.mode == "model"
    assert len(result.reminders) == 28 * 5


async def test_provider_rejects_oversized_wire_input_before_any_http_request() -> None:
    calls: list[httpx.Request] = []

    def refuse(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(refuse)) as http:
        provider = ArkResponsesClient(
            api_key="synthetic",
            model_id="existing",
            base_url="https://provider.invalid",
            http_client=http,
        )
        with pytest.raises(ValueError, match="bounded capacity"):
            await provider.rank_training_reminders(
                payload={"synthetic_overflow": "稳" * 22_000}, instructions="synthetic"
            )
    assert calls == []
