import pytest
from unittest.mock import AsyncMock

from xknx import XKNX
from xknx.devices import Light, Switch
from xknx.dpt.dpt import DPTBinary
from xknx.telegram import Telegram, TelegramDirection
from xknx.telegram.address import GroupAddress
from xknx.telegram.apci import GroupValueWrite


@pytest.mark.asyncio
async def test_incoming_telegram_updates_light():
    xknx = XKNX()

    light = Light(
        xknx=xknx,
        name="Test Light",
        group_address_switch="1/1/1",
        group_address_switch_state="1/1/2",
    )
    xknx.devices.async_add(light)

    telegram = Telegram(
        destination_address=GroupAddress("1/1/2"),
        direction=TelegramDirection.INCOMING,
        payload=GroupValueWrite(DPTBinary(1)),
    )

    xknx.telegrams.put_nowait(telegram)
    await xknx.telegram_queue._process_all_telegrams()

    assert light.state is True


@pytest.mark.asyncio
async def test_switch_controls_light_through_bus_actuator(monkeypatch):
    xknx = XKNX()
    monkeypatch.setattr(type(xknx.cemi_handler), "send_telegram", AsyncMock())

    light = Light(
        xknx=xknx,
        name="Test Light",
        group_address_switch="1/1/1",
        group_address_switch_state="1/1/2",
    )
    switch = Switch(
        xknx=xknx,
        name="Test Switch",
        group_address="1/1/1",
    )
    xknx.devices.async_add(light)
    xknx.devices.async_add(switch)

    switch_ga = GroupAddress("1/1/1")
    light_state_ga = GroupAddress("1/1/2")
    actuator_commands = []

    def actuator(telegram):
        if telegram.destination_address != switch_ga:
            return
        actuator_commands.append(telegram.payload.value.value)
        xknx.telegrams.put_nowait(
            Telegram(
                destination_address=light_state_ga,
                direction=TelegramDirection.INCOMING,
                payload=GroupValueWrite(telegram.payload.value),
            )
        )

    xknx.telegram_queue.register_telegram_received_cb(
        actuator,
        group_addresses=[switch_ga],
        match_for_outgoing=True,
    )

    await switch.set_on()
    await xknx.telegram_queue._process_all_telegrams()
    assert light.state is True

    await switch.set_off()
    await xknx.telegram_queue._process_all_telegrams()
    assert light.state is False
    assert actuator_commands == [1, 0]
