import pytest
from unittest.mock import AsyncMock

from xknx import XKNX
from xknx.dpt.dpt_1 import DPTBool
from xknx.dpt.dpt_9 import DPTTemperature

from communication_object import CommunicationObject, Flags


def test_default_flags_are_applied_and_exposed():
    obj = CommunicationObject(
        name="Switch Object",
        flags=Flags.READ,
        configurable_flags=Flags.READ | Flags.WRITE | Flags.TRANSMIT,
        default_flags=Flags.READ | Flags.WRITE,
    )

    assert obj.flags == (Flags.READ | Flags.WRITE)
    assert obj.default_flags == (Flags.READ | Flags.WRITE)
    assert obj.default_value == int(Flags.READ | Flags.WRITE)


def test_only_configurable_flags_can_be_changed():
    obj = CommunicationObject(
        name="Light Object",
        flags=Flags.READ,
        configurable_flags=Flags.READ | Flags.WRITE | Flags.TRANSMIT,
    )

    obj.set_flag(Flags.WRITE)
    assert obj.is_set(Flags.WRITE)

    with pytest.raises(ValueError, match="not configurable"):
        obj.set_flag(Flags.UPDATE)

    obj.clear_flag(Flags.READ)
    assert not obj.is_set(Flags.READ)


def test_reset_restores_default_flags():
    obj = CommunicationObject(
        name="Temperature Object",
        flags=Flags.NONE,
        configurable_flags=Flags.READ | Flags.WRITE | Flags.UPDATE,
        default_flags=Flags.READ | Flags.UPDATE,
    )

    obj.set_flag(Flags.WRITE)
    obj.reset()

    assert obj.flags == (Flags.READ | Flags.UPDATE)


def test_update_flags_supports_batch_changes():
    obj = CommunicationObject(
        name="Dimmable Object",
        flags=Flags.NONE,
        configurable_flags=Flags.READ | Flags.WRITE | Flags.TRANSMIT | Flags.UPDATE,
        default_flags=Flags.READ,
    )

    obj.update_flags(Flags.WRITE | Flags.TRANSMIT)
    assert obj.flags == (Flags.WRITE | Flags.TRANSMIT | Flags.READ)

    obj.update_flags(Flags.WRITE | Flags.TRANSMIT, enabled=False)
    assert obj.flags == Flags.READ


def test_knx_bitmask_behavior_matches_expected_int_value():
    obj = CommunicationObject(
        name="Bitmask Object",
        flags=Flags.READ | Flags.WRITE,
        configurable_flags=tuple(flag for flag in Flags if flag != Flags.NONE),
    )

    assert int(obj) == int(Flags.READ | Flags.WRITE)
    assert obj.flags == Flags.READ | Flags.WRITE
    assert Flags.READ in obj
    assert Flags.WRITE in obj


def test_dpt_generic_value_roundtrips_and_tracks_internal_value():
    obj = CommunicationObject(
        name="Temperature",
        dpt_class=DPTTemperature,
        flags=Flags.READ | Flags.WRITE | Flags.TRANSMIT | Flags.UPDATE,
        configurable_flags={
            Flags.READ,
            Flags.WRITE,
            Flags.TRANSMIT,
            Flags.UPDATE,
        },
        value=21.5,
    )

    payload = obj.to_knx(21.5)
    decoded = obj.from_knx(payload)
    assert decoded == 21.5
    assert obj.value == 21.5
    assert obj.read() == 21.5

    obj.apply_telegram_value(DPTTemperature.to_knx(22.0))
    assert obj.value == 22.0


def test_flagged_behavior_blocks_writes_and_updates_when_disabled():
    obj = CommunicationObject(
        name="Switch",
        dpt_class=DPTBool,
        flags=Flags.READ,
        configurable_flags={Flags.READ, Flags.WRITE, Flags.TRANSMIT, Flags.UPDATE},
    )

    with pytest.raises(ValueError, match="WRITE is disabled"):
        obj.set_value(True)

    with pytest.raises(ValueError, match="UPDATE is disabled"):
        obj.apply_telegram_value(DPTBool.to_knx(True))

    obj.set_flag(Flags.WRITE)
    obj.set_value(True)
    assert obj.value is True
    assert obj.to_knx(True).value == 1


@pytest.mark.asyncio
async def test_virtual_knx_bus_updates_status_receiver_from_light_status_object(monkeypatch):
    xknx = XKNX()
    monkeypatch.setattr(type(xknx.cemi_handler), "send_telegram", AsyncMock())

    # Object representing the actual light-state communication object on the KNX bus.
    light_status = CommunicationObject(
        name="Light Status",
        dpt_class=DPTBool,
        flags=Flags.READ | Flags.WRITE | Flags.TRANSMIT | Flags.UPDATE,
        configurable_flags=Flags.READ | Flags.WRITE | Flags.TRANSMIT | Flags.UPDATE,
        xknx=xknx,
        group_address="1/1/1",
        value=False,
    )

    # Object listening to the same status GA and updating itself via the registered xknx bus.
    status_receiver = CommunicationObject(
        name="Light Status Receiver",
        dpt_class=DPTBool,
        flags=Flags.READ | Flags.UPDATE,
        configurable_flags=Flags.READ | Flags.WRITE | Flags.TRANSMIT | Flags.UPDATE,
        xknx=xknx,
        group_address="1/1/1",
        value=False,
    )

    light_status.register()
    status_receiver.register()

    light_status.set_value(True)
    await xknx.telegram_queue._process_all_telegrams()

    assert light_status.value is True
    assert status_receiver.value is True
    assert status_receiver.read() is True
