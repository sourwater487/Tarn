# LOCAL-ADAPTATION: [新增] 整个文件为相对 upstream/main@1dac438 的本地新增。
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from gateway import GatewayService
from persona_event_selection import format_persona_event_trace_line
from raw_events import (
    RawEventStore,
    extract_raw_client_context,
    is_sticker_payload,
    strip_raw_client_context,
)
from reflection_engine import ReflectionEngine


WEB_REQUEST_TEXT = """<dynamic_context>
<memo_context>Che memo that is not Lin's utterance</memo_context>
<current_time>2026-07-14T21:05:00+08:00</current_time>
</dynamic_context>

<lin_message>
[2026-07-14 21:05 | 小雨 28℃ | 上海市徐汇区]
我真正说的话
</lin_message>"""
STICKER_TEXT = '{"type":"sticker","id":"小猫-开心"}'


def test_web_request_keeps_only_the_lin_utterance():
    assert strip_raw_client_context(WEB_REQUEST_TEXT) == "我真正说的话"


def test_web_request_extracts_bounded_client_context_separately():
    assert extract_raw_client_context(WEB_REQUEST_TEXT) == {
        "weather": "小雨 28℃",
        "location": "上海市徐汇区",
        "observed_at": "2026-07-14T21:05:00+08:00",
    }


def test_split_weather_status_fields_are_not_misclassified_as_location():
    context = extract_raw_client_context(
        "<lin_message>[2026-07-14 21:05 | 微风 | 雷雨 | 高新南社区公园]\n正文</lin_message>"
    )

    assert context["weather"] == "微风 | 雷雨"
    assert context["location"] == "高新南社区公园"


def test_lin_weather_and_location_prose_is_not_client_metadata():
    text = "深圳今天有雷雨，我被困在公司了。"

    assert strip_raw_client_context(text) == text
    assert extract_raw_client_context(text) == {}


def test_standalone_client_metadata_xml_is_removed():
    text = """<timestamp>2026-07-14T21:05:00+08:00</timestamp>
<weather>小雨 28℃</weather>
<location>上海市徐汇区</location>
<dynamic>injected activity</dynamic>
用户正文"""

    assert strip_raw_client_context(text) == "用户正文"


def test_legacy_message_time_suffix_is_removed():
    text = "用户正文\n\n<message_time timezone=\"Asia/Shanghai\">2026/07/14 21:05</message_time>"

    assert strip_raw_client_context(text) == "用户正文"


def test_context_only_input_does_not_turn_into_a_recall_query():
    text = "<dynamic_context><memo_context>memo</memo_context></dynamic_context>"

    assert strip_raw_client_context(text) == ""


def test_reserved_context_xml_inside_lin_message_is_preserved_as_user_text():
    text = "<lin_message>请解释 <dynamic_context>foo</dynamic_context> 这个 XML</lin_message>"

    assert strip_raw_client_context(text) == "请解释 <dynamic_context>foo</dynamic_context> 这个 XML"


def test_unrelated_xml_is_not_removed():
    text = "请分析 <example kind=\"demo\">正文</example>"

    assert strip_raw_client_context(text) == text


def test_standalone_sticker_json_is_non_recallable_but_regular_json_is_preserved():
    assert is_sticker_payload(STICKER_TEXT) is True
    assert strip_raw_client_context(STICKER_TEXT) == ""
    assert strip_raw_client_context(
        '<lin_message>{"type":"sticker","id":"小猫-开心"}</lin_message>'
    ) == ""

    regular_json = '{"type":"note","id":"小猫-开心"}'
    sticker_example_in_prose = '格式示例：{"type":"sticker","id":"小猫-开心"}'
    sticker_with_extra_fields = '{"type":"sticker","id":"小猫-开心","caption":"示例"}'
    sticker_library = '[{"id":"小猫-请给我","url":"https://example.com/a.jpg"}]'
    assert is_sticker_payload(regular_json) is False
    assert strip_raw_client_context(regular_json) == regular_json
    assert strip_raw_client_context(sticker_example_in_prose) == sticker_example_in_prose
    assert strip_raw_client_context(sticker_with_extra_fields) == sticker_with_extra_fields
    assert strip_raw_client_context(sticker_library) == sticker_library


def test_lin_message_mentioned_inside_ordinary_prose_is_not_an_envelope():
    text = "请解释 <lin_message>foo</lin_message> 这个 XML"

    assert strip_raw_client_context(text) == text


def test_gateway_current_turn_query_uses_the_same_filter():
    service = object.__new__(GatewayService)

    assert service._extract_current_turn_user_query(
        [{"role": "user", "content": WEB_REQUEST_TEXT}]
    ) == "我真正说的话"


def test_gateway_keeps_sticker_as_raw_current_message_but_not_as_recall_query():
    service = object.__new__(GatewayService)
    messages = [{"role": "user", "content": STICKER_TEXT}]

    assert service._extract_current_turn_user_raw_text(messages) == STICKER_TEXT
    assert service._extract_current_turn_user_query(messages) == ""
    assert messages == [{"role": "user", "content": STICKER_TEXT}]


def test_latest_sticker_does_not_fall_back_to_an_older_persona_message():
    service = object.__new__(GatewayService)
    messages = [
        {"role": "user", "content": "上一轮真正的问题"},
        {"role": "assistant", "content": "上一轮回复"},
        {"role": "user", "content": STICKER_TEXT},
    ]

    assert service._extract_last_user_query(messages) == ""


def test_gateway_filters_split_text_blocks_around_an_image():
    service = object.__new__(GatewayService)
    content = [
        {"type": "text", "text": "<dynamic_context>memo</dynamic_context>\n<lin_message>\n"},
        {"type": "text", "text": "[2026-07-14 21:05 | 小雨 28℃ | 上海]\n带图的一句话"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
        {"type": "text", "text": "\n</lin_message>"},
    ]

    assert service._extract_current_turn_user_query(
        [{"role": "user", "content": content}]
    ) == "带图的一句话"


def test_gateway_tool_continuation_is_not_treated_as_a_new_user_turn():
    service = object.__new__(GatewayService)
    messages = [
        {"role": "user", "content": WEB_REQUEST_TEXT},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call_1"}]},
        {"role": "tool", "tool_call_id": "call_1", "content": "tool result"},
    ]

    assert service._extract_current_turn_user_query(messages) == ""


def test_gateway_does_not_add_plaintext_reasoning_when_structured_details_exist():
    service = object.__new__(GatewayService)
    assistant_message = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "weather", "arguments": '{"city":"Shanghai"}'},
            }
        ],
        "reasoning_details": [
            {
                "type": "reasoning.encrypted",
                "data": "opaque-provider-payload",
                "format": "anthropic-claude-v1",
                "index": 0,
            }
        ],
    }
    signature = service._tool_call_signature(assistant_message)
    service.pending_tool_reasoning = {
        "lin-main": {
            signature: {
                "reasoning_content": "legacy plaintext reasoning",
                "tool_calls": assistant_message["tool_calls"],
            }
        }
    }
    messages = [
        assistant_message,
        {"role": "tool", "tool_call_id": "call_1", "content": "tool result"},
    ]

    service._restore_cached_reasoning_content("lin-main", messages)

    assert "reasoning_content" not in assistant_message
    assert assistant_message["reasoning_details"][0]["data"] == "opaque-provider-payload"


def test_gateway_keeps_legacy_plaintext_reasoning_restore_as_fail_open_fallback():
    service = object.__new__(GatewayService)
    assistant_message = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "weather", "arguments": '{"city":"Shanghai"}'},
            }
        ],
    }
    signature = service._tool_call_signature(assistant_message)
    service.pending_tool_reasoning = {
        "lin-main": {
            signature: {
                "reasoning_content": "legacy plaintext reasoning",
                "tool_calls": assistant_message["tool_calls"],
            }
        }
    }
    messages = [
        assistant_message,
        {"role": "tool", "tool_call_id": "call_1", "content": "tool result"},
    ]

    service._restore_cached_reasoning_content("lin-main", messages)

    assert assistant_message["reasoning_content"] == "legacy plaintext reasoning"


def test_gateway_stored_turn_cleanup_is_role_aware():
    service = object.__new__(GatewayService)
    cleaned_by_persona = []

    class PersonaCleaner:
        @staticmethod
        def _clean_client_status_lines(text):
            cleaned_by_persona.append(text)
            return text

    service.persona_engine = PersonaCleaner()
    assistant_text = "示例：<lin_message>foo</lin_message>"

    assert service._clean_conversation_turn_text(
        WEB_REQUEST_TEXT,
        role="user",
    ) == "我真正说的话"
    assert service._clean_conversation_turn_text(
        assistant_text,
        role="assistant",
    ) == assistant_text
    assert cleaned_by_persona == ["我真正说的话"]


def test_raw_event_ingest_stores_only_the_user_utterance(tmp_path):
    store = RawEventStore({"state_dir": str(tmp_path)})
    result = store.ingest(
        [
            {
                "role": "user",
                "text": WEB_REQUEST_TEXT,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        ],
        source="test",
    )

    assert result["inserted"] == 1
    events = store.list_events_between(
        start_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        end_at=datetime.now(timezone.utc) + timedelta(minutes=1),
    )
    assert [event["text"] for event in events] == ["我真正说的话"]


def test_raw_event_ingest_preserves_assistant_xml_examples(tmp_path):
    store = RawEventStore({"state_dir": str(tmp_path)})
    assistant_text = "示例：<lin_message>foo</lin_message>"
    result = store.ingest(
        [{"role": "assistant", "text": assistant_text}],
        source="test",
    )

    assert result["inserted"] == 1
    events = store.list_events_between(
        start_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        end_at=datetime.now(timezone.utc) + timedelta(minutes=1),
    )
    assert [event["text"] for event in events] == [assistant_text]


def test_raw_event_ingest_rejects_sticker_only_events(tmp_path):
    store = RawEventStore({"state_dir": str(tmp_path)})

    result = store.ingest(
        [{"role": "user", "text": STICKER_TEXT}],
        source="test",
    )

    assert result["inserted"] == 0
    assert result["rejected"] == 1
    assert result["items"][0]["reason"] == "empty_text"


def test_reflection_filters_historical_sticker_rows_from_memory_material():
    conversation_turns = ReflectionEngine._conversation_turn_payloads(
        [
            {
                "id": 1,
                "user_text": STICKER_TEXT,
                "assistant_text": "看到啦。",
            }
        ],
        limit=10,
    )
    raw_turns = ReflectionEngine._raw_event_turn_payloads(
        [
            {
                "id": 1,
                "role": "user",
                "text": STICKER_TEXT,
                "session_id": "lin-main",
                "metadata": {"round_id": 1},
            },
            {
                "id": 2,
                "role": "assistant",
                "text": "看到啦。",
                "session_id": "lin-main",
                "metadata": {"round_id": 1},
            },
        ],
        limit=10,
    )

    assert conversation_turns[0]["user_text"] == ""
    assert raw_turns[0]["user_text"] == ""
    assert STICKER_TEXT not in str(conversation_turns)
    assert STICKER_TEXT not in str(raw_turns)


def test_primary_memory_policy_keeps_current_conversation_authoritative():
    service = object.__new__(GatewayService)
    service.identity = {"user_display_name": "Lin", "ai_name": "Che"}

    policy = service._memory_reading_policy_context()

    assert "以当前对话为准" in policy
    assert "仅在有帮助时使用" in policy
    assert "不必主动提及记忆来源" in policy


def test_date_persona_trace_uses_identity_labels():
    line = format_persona_event_trace_line(
        {"user_excerpt": "你好", "assistant_excerpt": "在呢"},
        user_label="Lin",
        assistant_label="Che",
    )

    assert "Lin: 你好" in line
    assert "Che: 在呢" in line


def test_client_context_snapshots_are_deduped_and_hard_capped(tmp_path):
    store = RawEventStore(
        {
            "state_dir": str(tmp_path),
            "raw_events": {
                "client_context_max_records": 3,
                "client_context_max_per_day": 2,
                "client_context_value_max_chars": 80,
            },
        }
    )

    def ingest_snapshot(day: str, index: int, *, weather: str | None = None):
        value = weather or f"小雨 {20 + index}℃"
        text = (
            f"<lin_message>[{day} 09:0{index} | {value} | 地点{index}]\n"
            f"正文{index}</lin_message>"
        )
        return store.ingest(
            [
                {
                    "source_event_id": f"event-{day}-{index}-{value}",
                    "role": "user",
                    "text": text,
                    "created_at": f"{day}T09:0{index}:00+08:00",
                    "session_id": "lin-main",
                }
            ],
            source="test",
        )

    ingest_snapshot("2026-07-10", 0)
    ingest_snapshot("2026-07-10", 1)
    ingest_snapshot("2026-07-10", 2)
    day_one = store.list_client_context_for_date("2026-07-10")
    assert [item["weather"] for item in day_one] == ["小雨 22℃", "小雨 21℃"]

    duplicate = store.record_client_context(
        "<weather>小雨 22℃</weather><location>地点2</location>",
        date_key="2026-07-10",
    )
    assert duplicate["status"] == "duplicate"

    ingest_snapshot("2026-07-11", 3)
    ingest_snapshot("2026-07-11", 4)
    assert len(store.list_client_context_for_date("2026-07-10")) == 1
    assert len(store.list_client_context_for_date("2026-07-11")) == 2


def test_date_recall_dedupes_only_substantive_pairs_in_current_window():
    service = object.__new__(GatewayService)
    service.persona_engine = SimpleNamespace()
    duplicate_turn = {
        "id": 1,
        "user_text": "我们昨天认真讨论了夜梦的结构化元数据保存方案。",
        "assistant_text": "我建议把自动天气和位置放进有上限的独立快照表。",
    }
    short_turn = {"id": 2, "user_text": "晚安", "assistant_text": "晚安宝宝"}
    novel_turn = {
        "id": 3,
        "user_text": "后来我们还讨论了窗口去重应该怎么做。",
        "assistant_text": "最后决定使用成对文本指纹，不调用模型。",
    }
    messages = [
        {"role": "user", "content": duplicate_turn["user_text"]},
        {"role": "assistant", "content": duplicate_turn["assistant_text"]},
        {"role": "user", "content": short_turn["user_text"]},
        {"role": "assistant", "content": short_turn["assistant_text"]},
    ]

    kept, debug = service._dedupe_date_recall_turns(
        [duplicate_turn, short_turn, novel_turn],
        messages,
    )

    assert [turn["id"] for turn in kept] == [2, 3]
    assert debug["dropped_turn_count"] == 1
    assert debug["dedupe_ms"] >= 0


def test_date_recall_keeps_topic_terms_debug_only():
    service = object.__new__(GatewayService)
    service.identity = {"user_display_name": "Lin", "ai_name": "Che"}
    service.persona_engine = SimpleNamespace()
    service.gateway_tz = ZoneInfo("Asia/Shanghai")
    service.date_recall_enabled = True
    service.date_recall_budget = 520
    service.date_recall_max_buckets = 4
    service.date_recall_max_client_contexts = 6
    service._query_date_recall_hint = lambda _query: {"date": "2026-07-14", "label": "昨天"}
    service._date_recall_protected_topic_terms = lambda _query: []
    service._date_recall_topic_terms = lambda _query: ["夜梦"]
    service._query_requires_role_safe_date_transcript = lambda _query: False
    service._date_recall_turns_for_range = lambda *_args, **_kwargs: (
        [
            {
                "id": 7,
                "created_at": "2026-07-14T21:00:00+08:00",
                "session_id": "lin-main",
                "user_text": "昨天我们讨论了夜梦应该如何保存环境快照。",
                "assistant_text": "我建议给快照设置全局和每日双重上限。",
            }
        ],
        "raw_events",
    )
    service._date_recall_buckets_for_date = lambda *_args: []
    service._date_recall_client_contexts_for_query = lambda *_args: []

    text, debug, _bucket_ids = service._build_date_recall_context(
        "昨天夜梦聊了什么",
        [],
        current_messages=[],
    )

    assert "chat_transcript:" in text
    assert "topic_filter:" not in text
    assert debug["topic_terms"] == ["夜梦"]


def test_date_recall_client_context_requires_direct_relevance(tmp_path):
    store = RawEventStore({"state_dir": str(tmp_path)})
    store.record_client_context(
        "<weather>雷雨 29℃</weather><location>高新南社区公园</location>",
        date_key="2026-07-14",
    )
    service = object.__new__(GatewayService)
    service.raw_event_store = store
    service.date_recall_max_client_contexts = 6

    weather = service._date_recall_client_contexts_for_query(
        "2026-07-14",
        "昨天天气怎么样",
        ["天气"],
    )
    location = service._date_recall_client_contexts_for_query(
        "2026-07-14",
        "昨天在高新南社区公园聊了什么",
        ["高新南社区公园"],
    )
    unrelated = service._date_recall_client_contexts_for_query(
        "2026-07-14",
        "昨天聊了夜梦",
        ["夜梦"],
    )

    assert weather[0]["weather"] == "雷雨 29℃"
    assert weather[0]["location"] == ""
    assert location[0]["location"] == "高新南社区公园"
    assert location[0]["weather"] == ""
    assert unrelated == []


def test_explicit_date_weather_or_location_question_can_trigger_recall():
    service = object.__new__(GatewayService)
    service.gateway_tz = ZoneInfo("Asia/Shanghai")
    service.identity = {
        "ai_name": "Che",
        "user_name": "Lin",
        "user_display_name": "Lin",
        "user_aliases": [],
        "relationship_terms": [],
    }

    assert service._query_requests_date_recall("昨天天气怎么样") is True
    assert service._query_requests_date_recall("昨天在哪里") is True
    assert service._query_requests_date_recall("今天状态怎么样") is False


def test_recent_context_explicit_only_disables_passive_triggers():
    service = object.__new__(GatewayService)
    service.recent_budget = 300
    service.head_recent_hours = 24
    service.recent_context_mode = "explicit_only"
    service._query_requests_recent_context = lambda query: "最近" in query

    assert service._should_inject_recent_context("lin-main", "最近聊了什么") is True
    assert service._should_inject_recent_context("lin-main", "今天有点累") is False
