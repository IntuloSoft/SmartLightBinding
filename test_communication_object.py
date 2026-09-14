import pytest
from unittest.mock import AsyncMock

from xknx import XKNX
from xknx.dpt.dpt_1 import DPTBool
from xknx.dpt.dpt_9 import DPTTemperature
from xknx.telegram import Telegram, TelegramDirection
from xknx.telegram.address import GroupAddress
from xknx.telegram.apci import GroupValueRead, GroupValueResponse, GroupValueWrite

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

    @pytest.mark.asyncio
    async def test_flag_read(self, monkeypatch):
        xknx = XKNX()
        monkeypatch.setattr(type(xknx.cemi_handler), "send_telegram", AsyncMock())

        # Test READ disabled: no response to read request
        obj_disabled = CommunicationObject(
            name="Read Object Disabled",
            dpt_class=DPTBool,
            flags=Flags.COMMUNICATION,
            configurable_flags=Flags.COMMUNICATION | Flags.READ,
            xknx=xknx,
            group_addresses=["1/1/1"],
            value=True,
        )

        read_request = Telegram(
            destination_address=GroupAddress("1/1/1"),
            direction=TelegramDirection.INCOMING,
            payload=GroupValueRead(),
        )

        assert obj_disabled.process_telegram(read_request, from_bus=True) is True
        assert xknx.telegrams.empty()

        # Test READ enabled with COMMUNICATION: generates response
        obj_enabled = CommunicationObject(
            name="Read Object Enabled",
            dpt_class=DPTBool,
            flags=Flags.COMMUNICATION | Flags.READ,
            configurable_flags=Flags.COMMUNICATION | Flags.READ,
            xknx=xknx,
            group_addresses=["1/1/1"],
            value=True,
        )

        assert obj_enabled.process_telegram(read_request, from_bus=True) is True

        response = xknx.telegrams.get_nowait()
        assert response.destination_address == obj_enabled.group_address
        assert isinstance(response.payload, GroupValueResponse)
        assert response.payload.value == DPTBool.to_knx(True)

    @pytest.mark.asyncio
    async def test_flag_write(self, monkeypatch):
        xknx = XKNX()
        monkeypatch.setattr(type(xknx.cemi_handler), "send_telegram", AsyncMock())

        # Test WRITE disabled: still allows internal value change
        obj = CommunicationObject(
            name="Write Object Disabled",
            dpt_class=DPTBool,
            flags=Flags.COMMUNICATION,
            configurable_flags=Flags.COMMUNICATION | Flags.WRITE,
            xknx=xknx,
            group_addresses=["1/1/1"],
            value=True,
        )
        
        obj.register()

        state_telegram = Telegram(
            destination_address=GroupAddress("1/1/1"),
            direction=TelegramDirection.INCOMING,
            payload=GroupValueWrite(DPTBool.to_knx(False)),
        )
        xknx.telegrams.put_nowait(state_telegram)
        await xknx.telegram_queue._process_all_telegrams()
        assert obj.value is True


        # Test WRITE enabled: allows internal value change
        obj.set_flag(Flags.WRITE)
        
        xknx.telegrams.put_nowait(state_telegram)
        await xknx.telegram_queue._process_all_telegrams()
        assert obj.value is False

    def test_flag_transmit(self, monkeypatch):
        xknx = XKNX()
        monkeypatch.setattr(type(xknx.cemi_handler), "send_telegram", AsyncMock())

        # Test TRANSMIT disabled: value changes but no telegram sent
        obj_disabled = CommunicationObject(
            name="Transmit Object Disabled",
            dpt_class=DPTBool,
            flags=Flags.NONE,
            configurable_flags={Flags.TRANSMIT, Flags.COMMUNICATION},
            xknx=xknx,
            group_addresses=["1/1/1"],
        )

        obj_disabled.set_value(True)
        assert obj_disabled.value is True
        assert xknx.telegrams.empty()

        # Test TRANSMIT enabled with COMMUNICATION: telegram is sent
        obj_enabled = CommunicationObject(
            name="Transmit Object Enabled",
            dpt_class=DPTBool,
            flags=Flags.TRANSMIT | Flags.COMMUNICATION,
            configurable_flags={Flags.TRANSMIT, Flags.COMMUNICATION},
            xknx=xknx,
            group_addresses=["1/1/1"],
        )

        obj_enabled.set_value(True)
        assert obj_enabled.value is True
        assert not xknx.telegrams.empty()

    def test_flag_update(self):
        # Test UPDATE disabled: raises error when applying telegram value
        obj_disabled = CommunicationObject(
            name="Update Object Disabled",
            dpt_class=DPTBool,
            flags=Flags.NONE,
            configurable_flags=Flags.UPDATE,
            value=False,
        )

        with pytest.raises(ValueError, match="UPDATE is disabled"):
            obj_disabled.apply_telegram_value(DPTBool.to_knx(True))
        assert obj_disabled.value is False

        # Test UPDATE enabled: telegram value applied successfully
        obj_enabled = CommunicationObject(
            name="Update Object Enabled",
            dpt_class=DPTBool,
            flags=Flags.UPDATE,
            configurable_flags=Flags.UPDATE,
            value=False,
        )

        obj_enabled.apply_telegram_value(DPTBool.to_knx(True))
        assert obj_enabled.value is True

    def test_flag_read_on_init(self, monkeypatch):
        xknx = XKNX()
        monkeypatch.setattr(type(xknx.cemi_handler), "send_telegram", AsyncMock())

        # Test READ_ON_INIT disabled: no read request sent
        obj_disabled = CommunicationObject(
            name="Read On Init Object Disabled",
            dpt_class=DPTBool,
            flags=Flags.NONE,
            configurable_flags={Flags.READ_ON_INIT, Flags.COMMUNICATION},
            xknx=xknx,
            group_addresses=["1/1/2"],
            value=False,
        )

        obj_disabled.init()
        assert xknx.telegrams.empty()

        # Test READ_ON_INIT enabled with COMMUNICATION: read request is sent
        obj_enabled = CommunicationObject(
            name="Read On Init Object Enabled",
            dpt_class=DPTBool,
            flags=Flags.READ_ON_INIT | Flags.COMMUNICATION,
            configurable_flags={Flags.READ_ON_INIT, Flags.COMMUNICATION},
            xknx=xknx,
            group_addresses=["1/1/2"],
            value=False,
        )

        obj_enabled.init()
        assert not xknx.telegrams.empty()

    def test_flag_communication(self, monkeypatch):
        xknx = XKNX()
        monkeypatch.setattr(type(xknx.cemi_handler), "send_telegram", AsyncMock())

        # Test COMMUNICATION disabled: no bus operations, but internal access works
        obj_disabled = CommunicationObject(
            name="Communication Disabled Object",
            dpt_class=DPTBool,
            flags=Flags.NONE,
            configurable_flags={Flags.READ, Flags.WRITE, Flags.TRANSMIT, Flags.UPDATE, Flags.COMMUNICATION},
            xknx=xknx,
            group_addresses=["1/1/1"],
            value=False,
        )

        assert obj_disabled.read() is False
        obj_disabled.set_value(True)
        assert obj_disabled.value is True
        assert xknx.telegrams.empty()

        with pytest.raises(ValueError, match="UPDATE is disabled"):
            obj_disabled.apply_telegram_value(DPTBool.to_knx(True))

        obj_disabled.init()
        assert xknx.telegrams.empty()

        # Test COMMUNICATION enabled: bus operations work with appropriate flags
        obj_enabled = CommunicationObject(
            name="Communication Enabled Object",
            dpt_class=DPTBool,
            flags=Flags.COMMUNICATION | Flags.TRANSMIT,
            configurable_flags={Flags.READ, Flags.WRITE, Flags.TRANSMIT, Flags.UPDATE, Flags.COMMUNICATION},
            xknx=xknx,
            group_addresses=["1/1/2"],
            value=False,
        )

        obj_enabled.set_value(True)
        assert obj_enabled.value is True
        assert not xknx.telegrams.empty()

class TestScenarios:

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

