import struct
import uuid

from voxtype.windows.power_events import (
    PBT_APMPOWERSTATUSCHANGE,
    PBT_APMRESUMEAUTOMATIC,
    PBT_APMSUSPEND,
    PBT_POWERSETTINGCHANGE,
    WM_POWERBROADCAST,
    PowerEventCoordinator,
    PowerEventDispatcher,
    PowerEventKind,
    decode_power_event,
    parse_power_setting_payload,
)


def test_suspend_resume_and_status_codes_are_decoded() -> None:
    assert decode_power_event(PBT_APMSUSPEND).kind is PowerEventKind.SUSPEND
    assert decode_power_event(PBT_APMRESUMEAUTOMATIC).is_resume
    assert decode_power_event(PBT_APMPOWERSTATUSCHANGE).kind is PowerEventKind.POWER_STATUS_CHANGE
    assert decode_power_event(0xDEAD).kind is PowerEventKind.UNKNOWN


def test_power_setting_payload_parses_guid_and_binary_data() -> None:
    setting_id = uuid.UUID("5d3e9a59-e9d5-4b00-a6bd-ff34ff516548")
    data = struct.pack("<I", 2)
    change = parse_power_setting_payload(setting_id.bytes_le + struct.pack("<I", len(data)) + data)
    assert change.setting == setting_id
    assert change.data == data
    assert change.as_uint32() == 2


def test_power_setting_payload_rejects_truncation() -> None:
    payload = uuid.uuid4().bytes_le + struct.pack("<I", 8) + b"1234"
    try:
        parse_power_setting_payload(payload)
    except ValueError as error:
        assert "length" in str(error)
    else:
        raise AssertionError("truncated payload must fail")


def test_dispatcher_routes_only_power_messages() -> None:
    dispatcher = PowerEventDispatcher()
    received: list[PowerEventKind] = []
    dispatcher.subscribe(lambda event: received.append(event.kind))
    assert dispatcher.handle_message(0x1234, PBT_APMSUSPEND) is None
    event = dispatcher.handle_message(WM_POWERBROADCAST, PBT_APMSUSPEND)
    assert event is not None and event.kind is PowerEventKind.SUSPEND
    assert received == [PowerEventKind.SUSPEND]


def test_dispatcher_reports_callback_errors_without_skipping_other_callbacks() -> None:
    dispatcher = PowerEventDispatcher()
    received: list[str] = []

    def broken_callback(event: object) -> None:
        del event
        raise RuntimeError("boom")

    dispatcher.subscribe(broken_callback)
    dispatcher.subscribe(lambda event: received.append(event.kind.value))
    errors = dispatcher.dispatch(decode_power_event(PBT_APMSUSPEND))
    assert len(errors) == 1
    assert received == ["suspend"]


def test_coordinator_maps_native_suspend_and_resume_to_lifecycle_callbacks() -> None:
    actions: list[str] = []
    coordinator = PowerEventCoordinator(
        on_suspend=lambda: actions.append("suspend"),
        on_resume=lambda: actions.append("resume"),
    )
    coordinator.handle_message(WM_POWERBROADCAST, PBT_APMSUSPEND)
    coordinator.handle_message(WM_POWERBROADCAST, PBT_APMRESUMEAUTOMATIC)
    assert actions == ["suspend", "resume"]


def test_setting_change_without_pointer_is_still_reported_safely() -> None:
    event = decode_power_event(PBT_POWERSETTINGCHANGE, 0)
    assert event.kind is PowerEventKind.SETTING_CHANGE
    assert event.setting_change is None
