import pytest
from unittest.mock import AsyncMock

from xknx import XKNX
from xknx.dpt.dpt_1 import DPTBool
from xknx.dpt.dpt_9 import DPTTemperature
from xknx.telegram import Telegram, TelegramDirection
from xknx.telegram.address import GroupAddress
from xknx.telegram.apci import GroupValueWrite

from communication_object import CommunicationObject, Flags


class TestFlags:
    def test_default_flags_are_applied_and_exposed(self):
        obj = CommunicationObject(
            name="Switch Object",
            flags=Flags.READ,
            configurable_flags=Flags.READ | Flags.WRITE | Flags.TRANSMIT,
            default_flags=Flags.READ | Flags.WRITE,
        )

        assert obj.flags == (Flags.READ | Flags.WRITE)
        assert obj.default_flags == (Flags.READ | Flags.WRITE)
        assert obj.default_value == int(Flags.READ | Flags.WRITE)

    def test_only_configurable_flags_can_be_changed(self):
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

    def test_reset_restores_default_flags(self):
        obj = CommunicationObject(
            name="Temperature Object",
            flags=Flags.NONE,
            configurable_flags=Flags.READ | Flags.WRITE | Flags.UPDATE,
            default_flags=Flags.READ | Flags.UPDATE,
        )

        obj.set_flag(Flags.WRITE)
        obj.reset()

        assert obj.flags == (Flags.READ | Flags.UPDATE)

    def test_update_flags_supports_batch_changes(self):
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

    def test_knx_bitmask_behavior_matches_expected_int_value(self):
        obj = CommunicationObject(
            name="Bitmask Object",
            flags=Flags.READ | Flags.WRITE,
            configurable_flags=tuple(flag for flag in Flags if flag != Flags.NONE),
        )

        assert int(obj) == int(Flags.READ | Flags.WRITE)
        assert obj.flags == Flags.READ | Flags.WRITE
        assert Flags.READ in obj
        assert Flags.WRITE in obj


class TestValueConversions:
    def test_dpt_generic_value_roundtrips_and_tracks_internal_value(self):
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

    def test_flagged_behavior_blocks_writes_and_updates_when_disabled(self):
        obj = CommunicationObject(
            name="Switch",
            dpt_class=DPTBool,
            flags=Flags.READ,
            configurable_flags={Flags.READ, Flags.WRITE, Flags.TRANSMIT, Flags.UPDATE},
        )

        obj.set_value(True)
        assert obj.value is True
        assert obj.read() is True
        assert obj.to_knx(True).value == 1

        with pytest.raises(ValueError, match="UPDATE is disabled"):
            obj.apply_telegram_value(DPTBool.to_knx(True))

    def test_write_and_transmit_flags_control_write_and_transmission(self, monkeypatch):
        xknx = XKNX()
        monkeypatch.setattr(type(xknx.cemi_handler), "send_telegram", AsyncMock())

        writable_and_transmitting = CommunicationObject(
            name="Writable Transmitter",
            dpt_class=DPTBool,
            flags=Flags.WRITE | Flags.TRANSMIT,
            configurable_flags={Flags.READ, Flags.WRITE, Flags.TRANSMIT, Flags.UPDATE},
            xknx=xknx,
            group_addresses=["1/1/1"],
        )

        writable_and_transmitting.set_value(True)
        assert writable_and_transmitting.value is True
        assert not xknx.telegrams.empty()
        xknx.telegrams.get_nowait()

        write_only = CommunicationObject(
            name="Write Only",
            dpt_class=DPTBool,
            flags=Flags.WRITE,
            configurable_flags={Flags.READ, Flags.WRITE, Flags.TRANSMIT, Flags.UPDATE},
            xknx=xknx,
            group_addresses=["1/1/1"],
        )

        write_only.set_value(False)
        assert write_only.value is False
        assert xknx.telegrams.empty()

        transmit_only = CommunicationObject(
            name="Transmit Only",
            dpt_class=DPTBool,
            flags=Flags.TRANSMIT,
            configurable_flags={Flags.READ, Flags.WRITE, Flags.TRANSMIT, Flags.UPDATE},
            xknx=xknx,
            group_addresses=["1/1/1"],
        )

        transmit_only.set_value(True)
        assert transmit_only.value is True
        assert not xknx.telegrams.empty()
        xknx.telegrams.get_nowait()

    @pytest.mark.asyncio
    async def test_virtual_knx_bus_updates_status_receiver_from_light_status_object(
        self, monkeypatch
    ):
        xknx = XKNX()
        monkeypatch.setattr(type(xknx.cemi_handler), "send_telegram", AsyncMock())

        # Object representing the actual light-state communication object on the KNX bus.
        light_status = CommunicationObject(
            name="Status",
            dpt_class=DPTBool,
            flags=Flags.READ | Flags.WRITE | Flags.TRANSMIT | Flags.UPDATE,
            configurable_flags=Flags.READ | Flags.WRITE | Flags.TRANSMIT | Flags.UPDATE,
            xknx=xknx,
            group_addresses=["1/1/1"],
            value=False,
        )

        # Object listening to the same status GA and updating itself via the registered xknx bus.
        status_receiver = CommunicationObject(
            name="Status Receiver",
            dpt_class=DPTBool,
            flags=Flags.READ | Flags.UPDATE,
            configurable_flags=Flags.READ | Flags.WRITE | Flags.TRANSMIT | Flags.UPDATE,
            xknx=xknx,
            group_addresses=["1/1/1"],
            value=False,
        )

        light_status.register()
        status_receiver.register()

        light_status.set_value(True)
        await xknx.telegram_queue._process_all_telegrams()

        assert light_status.value is True
        assert status_receiver.value is True
        assert status_receiver.read() is True

    @pytest.mark.asyncio
    async def test_status_object_uses_first_ga_for_transmit_and_keeps_secondary_ga_assigned(
        self, monkeypatch
    ):
        xknx = XKNX()
        monkeypatch.setattr(type(xknx.cemi_handler), "send_telegram", AsyncMock())

        status_object = CommunicationObject(
            name="Status Object",
            dpt_class=DPTBool,
            flags=Flags.READ | Flags.WRITE | Flags.TRANSMIT | Flags.UPDATE,
            configurable_flags=Flags.READ | Flags.WRITE | Flags.TRANSMIT | Flags.UPDATE,
            xknx=xknx,
            group_addresses=["1/1/1", "1/1/2"],
            value=False,
        )

        status_object.register()

        assert len(status_object.group_addresses) == 2
        assert status_object.group_address == status_object.group_addresses[0]

        status_object.set_value(True)
        telegram = xknx.telegrams.get_nowait()
        assert telegram.destination_address == status_object.group_address
        assert telegram.destination_address != status_object.group_addresses[1]

        await xknx.telegram_queue._process_all_telegrams()
        assert status_object.value is True

    @pytest.mark.asyncio
    async def test_single_object_with_switch_and_state_ga_updates_from_state_telegram(
        self, monkeypatch
    ):
        xknx = XKNX()
        monkeypatch.setattr(type(xknx.cemi_handler), "send_telegram", AsyncMock())

        light_object = CommunicationObject(
            name="Light Object",
            dpt_class=DPTBool,
            flags=Flags.READ | Flags.WRITE | Flags.TRANSMIT | Flags.UPDATE,
            configurable_flags=Flags.READ | Flags.WRITE | Flags.TRANSMIT | Flags.UPDATE,
            xknx=xknx,
            group_addresses=["1/1/1", "1/1/2"],
            value=False,
        )

        light_object.register()

        # Simulate application command: switch command is sent on the first GA.
        light_object.set_value(True)
        await xknx.telegram_queue._process_all_telegrams()

        assert light_object.value is True
        assert str(light_object.group_addresses[0]) == "1/1/1"

        # Simulate incoming state telegram on the second GA; the object updates itself.
        state_telegram = Telegram(
            destination_address=GroupAddress("1/1/2"),
            direction=TelegramDirection.INCOMING,
            payload=GroupValueWrite(DPTBool.to_knx(False)),
        )
        xknx.telegrams.put_nowait(state_telegram)
        await xknx.telegram_queue._process_all_telegrams()

        assert light_object.value is False
        assert light_object.read() is False

    def test_disabled_communication_object_does_not_participate_in_any_communication(
        self, monkeypatch
    ):
        xknx = XKNX()
        monkeypatch.setattr(type(xknx.cemi_handler), "send_telegram", AsyncMock())

        disabled_object = CommunicationObject(
            name="Disabled Object",
            dpt_class=DPTBool,
            flags=Flags.NONE,
            configurable_flags={Flags.READ, Flags.WRITE, Flags.TRANSMIT, Flags.UPDATE},
            xknx=xknx,
            group_addresses=["1/1/1"],
            value=False,
        )

        assert not disabled_object.readable
        assert not disabled_object.writable
        assert not disabled_object.updateable
        assert not disabled_object.transmittable

        disabled_object.set_value(True)
        assert disabled_object.value is True
        assert disabled_object.read() is True
        assert xknx.telegrams.empty()

        with pytest.raises(ValueError, match="UPDATE is disabled"):
            disabled_object.apply_telegram_value(DPTBool.to_knx(True))

        assert disabled_object.value is True
