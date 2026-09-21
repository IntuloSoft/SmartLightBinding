import pytest
from unittest.mock import AsyncMock
import copy
from enum import Enum
from xknx import XKNX
from xknx import telegram
from xknx.dpt.dpt_1 import DPTBool
from xknx.dpt.dpt_9 import DPTTemperature
from xknx.telegram import Telegram, TelegramDirection
from xknx.telegram.address import GroupAddress
from xknx.telegram.apci import GroupValueRead, GroupValueResponse, GroupValueWrite

from communication_object import CommunicationObject, Flags

class GatewayMode(Enum):
    ECHO = "echo"
    NO_ECHO = "no_echo"

@pytest.fixture(params=[GatewayMode.ECHO, GatewayMode.NO_ECHO])
def xknx_env(request, monkeypatch):
    xknx = XKNX()

    mode = request.param
    
    sent_telegrams = []

    async def send_telegram_mock(telegram):
        sent_telegrams.append(telegram) 
        assert telegram.direction == TelegramDirection.OUTGOING
        if mode == GatewayMode.ECHO:
            echoed = copy.deepcopy(telegram)
            echoed.direction = TelegramDirection.INCOMING
            xknx.telegrams.put_nowait(echoed)

    monkeypatch.setattr(
        type(xknx.cemi_handler),
        "send_telegram",
        AsyncMock(side_effect=send_telegram_mock),
    )

    return xknx, mode, sent_telegrams


class TestFlags:

    @pytest.mark.asyncio
    async def test_flag_communication(self, xknx_env):
        xknx, _, sent_telegrams = xknx_env

    @pytest.mark.asyncio
    async def test_flag_read(self, xknx_env):
        xknx, _, sent_telegrams = xknx_env
        
        # Test READ disabled: no response to read request
        obj = CommunicationObject(
            name="TestObject",
            dpt_class=DPTBool,
            flags=Flags.COMMUNICATION | Flags.READ,
            configurable_flags=Flags.COMMUNICATION | Flags.READ,
            xknx=xknx,
            group_addresses=["1/1/1"],
            value=True,
        )
        obj.register()

        xknx.telegrams.put_nowait(
            Telegram(
                destination_address=GroupAddress("1/1/1"),
                direction=TelegramDirection.INCOMING,
                payload=GroupValueRead(),
            )
        )
    
        await xknx.telegram_queue._process_all_telegrams()
        assert len(sent_telegrams) == 1
        assert sent_telegrams.pop() == Telegram(
            destination_address=GroupAddress("1/1/1"),
            direction=TelegramDirection.OUTGOING,
            payload=GroupValueResponse(DPTBool.to_knx(True)),
        )

        obj.clear_flag(Flags.READ)
        xknx.telegrams.put_nowait(
            Telegram(
                destination_address=GroupAddress("1/1/1"),
                direction=TelegramDirection.INCOMING,
                payload=GroupValueRead(),
            )
        )

        await xknx.telegram_queue._process_all_telegrams()
        assert len(sent_telegrams) == 0

    @pytest.mark.asyncio
    async def test_flag_write(self, xknx_env):
        xknx, _, sent_telegrams = xknx_env

        values = []
        def on_write(value):
            values.append(value)
        
        # Test READ disabled: no response to read request
        obj = CommunicationObject(
            name="TestObject",
            dpt_class=DPTBool,
            flags=Flags.COMMUNICATION | Flags.WRITE,
            configurable_flags=Flags.COMMUNICATION | Flags.WRITE | Flags.UPDATE,
            xknx=xknx,
            group_addresses=["1/1/1"],
            value=True,
            on_write_cb=on_write
        )
        obj.register()

        xknx.telegrams.put_nowait(
            Telegram(
                destination_address=GroupAddress("1/1/1"),
                direction=TelegramDirection.INCOMING,
                payload=GroupValueWrite(DPTBool.to_knx(False)),
            )
        )

        await xknx.telegram_queue._process_all_telegrams()
        assert len(sent_telegrams) == 0
        assert len(values) == 1
        assert values.pop() == False
        assert obj.value is True

        # use update flag in combination with write flag

        obj.set_flag(Flags.UPDATE)
        xknx.telegrams.put_nowait(
            Telegram(
                destination_address=GroupAddress("1/1/1"),
                direction=TelegramDirection.INCOMING,
                payload=GroupValueWrite(DPTBool.to_knx(False)),
            )
        )

        await xknx.telegram_queue._process_all_telegrams()
        assert len(sent_telegrams) == 0
        assert len(values) == 1
        assert values.pop() == False
        assert obj.value is False

        # Disable write flag
        obj.clear_flag(Flags.UPDATE)
        obj.clear_flag(Flags.WRITE)
        xknx.telegrams.put_nowait(
            Telegram(
                destination_address=GroupAddress("1/1/1"),
                direction=TelegramDirection.INCOMING,
                payload=GroupValueWrite(DPTBool.to_knx(True)),
            )
        )

        await xknx.telegram_queue._process_all_telegrams()
        assert len(sent_telegrams) == 0
        assert len(values) == 0
        assert obj.value is False

    @pytest.mark.asyncio
    async def test_flag_transmit(self, xknx_env):
        xknx, mode, sent_telegrams = xknx_env

        values = []
        def on_write(value):
            values.append(value)
        
        # Test READ disabled: no response to read request
        obj = CommunicationObject(
            name="TestObject",
            dpt_class=DPTBool,
            flags=Flags.COMMUNICATION | Flags.TRANSMIT | Flags.WRITE,
            configurable_flags=Flags.COMMUNICATION | Flags.TRANSMIT | Flags.WRITE,
            xknx=xknx,
            group_addresses=["1/1/1"],
            value=True,
            on_write_cb=on_write
        )
        obj.register()

        obj.set_value(False)

        await xknx.telegram_queue._process_all_telegrams()
        assert len(sent_telegrams) == 1
        assert sent_telegrams.pop() == Telegram(
            destination_address=GroupAddress("1/1/1"),
            direction=TelegramDirection.OUTGOING,
            payload=GroupValueWrite(DPTBool.to_knx(False)),
        )
        if mode == GatewayMode.ECHO:
            assert len(values) == 2
            assert values == [False,False]
        if mode == GatewayMode.NO_ECHO:
            assert len(values) == 1
            assert values == [False]
        values.clear()

        # # use update flag in combination with write flag
        obj.clear_flag(Flags.TRANSMIT)
        obj.set_value(True)
        
        await xknx.telegram_queue._process_all_telegrams()
        assert len(sent_telegrams) == 0
        assert len(values) == 0


    @pytest.mark.asyncio
    async def test_flag_update(self, xknx_env):
        xknx, _, sent_telegrams = xknx_env

        values = []
        def on_write(value):
            values.append(value)
        
        # Test READ disabled: no response to read request
        obj = CommunicationObject(
            name="TestObject",
            dpt_class=DPTBool,
            flags=Flags.COMMUNICATION | Flags.UPDATE,
            configurable_flags=Flags.COMMUNICATION | Flags.WRITE | Flags.UPDATE,
            xknx=xknx,
            group_addresses=["1/1/1"],
            value=True,
            on_write_cb=on_write
        )
        obj.register()

        xknx.telegrams.put_nowait(
            Telegram(
                destination_address=GroupAddress("1/1/1"),
                direction=TelegramDirection.INCOMING,
                payload=GroupValueWrite(DPTBool.to_knx(False)),
            )
        )

        await xknx.telegram_queue._process_all_telegrams()
        assert len(sent_telegrams) == 0
        assert len(values) == 0
        assert obj.value is False

        # use update flag in combination with write flag
        obj.set_flag(Flags.WRITE)
        xknx.telegrams.put_nowait(
            Telegram(
                destination_address=GroupAddress("1/1/1"),
                direction=TelegramDirection.INCOMING,
                payload=GroupValueWrite(DPTBool.to_knx(True)),
            )
        )

        await xknx.telegram_queue._process_all_telegrams()
        assert len(sent_telegrams) == 0
        assert len(values) == 1
        assert values.pop() == True
        assert obj.value is True

        # use update flag in combination with write flag
        obj.clear_flag(Flags.UPDATE)
        xknx.telegrams.put_nowait(
            Telegram(
                destination_address=GroupAddress("1/1/1"),
                direction=TelegramDirection.INCOMING,
                payload=GroupValueWrite(DPTBool.to_knx(False)),
            )
        )

        await xknx.telegram_queue._process_all_telegrams()
        assert len(sent_telegrams) == 0
        assert len(values) == 1
        assert values.pop() == False
        assert obj.value is True
        

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
            flags=Flags.COMMUNICATION | Flags.READ | Flags.WRITE | Flags.TRANSMIT | Flags.UPDATE,
            configurable_flags=Flags.COMMUNICATION | Flags.READ | Flags.WRITE | Flags.TRANSMIT | Flags.UPDATE,
            xknx=xknx,
            group_addresses=["1/1/1"],
            value=False,
        )

        # Object listening to the same status GA and updating itself via the registered xknx bus.
        status_receiver = CommunicationObject(
            name="Status Receiver",
            dpt_class=DPTBool,
            flags=Flags.COMMUNICATION | Flags.READ | Flags.UPDATE,
            configurable_flags=Flags.COMMUNICATION | Flags.READ | Flags.WRITE | Flags.TRANSMIT | Flags.UPDATE,
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
            flags=Flags.COMMUNICATION | Flags.READ | Flags.WRITE | Flags.TRANSMIT | Flags.UPDATE,
            configurable_flags=Flags.COMMUNICATION | Flags.READ | Flags.WRITE | Flags.TRANSMIT | Flags.UPDATE,
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

