import copy
from unittest.mock import AsyncMock

import pytest

from xknx import XKNX
from xknx.telegram import Telegram
from xknx.telegram.address import GroupAddress
from xknx.telegram.apci import GroupValueWrite
from xknx.telegram.telegram import TelegramDirection

@pytest.mark.asyncio
async def test_outgoing_plus_echo(monkeypatch):
    xknx = XKNX()

    async def send_telegram_mock(telegram):
        # Simulate a KNX/IP interface that echoes the telegram back.
        
        echoed = copy.deepcopy(telegram)
        assert telegram.direction == TelegramDirection.OUTGOING
        echoed.direction = TelegramDirection.INCOMING

        xknx.telegrams.put_nowait(echoed)

    monkeypatch.setattr(type(xknx.cemi_handler), "send_telegram", AsyncMock(side_effect=send_telegram_mock))

    received = []

    def on_telegram(telegram):
        received.append(
            (
                telegram.direction,
                str(telegram.destination_address),
            )
        )

    xknx.telegram_queue.register_telegram_received_cb(
        on_telegram,
        group_addresses=["1/1/1"],
        match_for_outgoing=True,
    )

    xknx.telegrams.put_nowait(
        Telegram(
            destination_address="1/1/1",
            direction=TelegramDirection.INCOMING,
            payload=GroupValueWrite(b"\x01"),
        )
    )

    await xknx.telegram_queue._process_all_telegrams()

    assert len(received) == 1
    assert received == [
        (TelegramDirection.INCOMING, "1/1/1"),
    ]

    xknx.telegrams.put_nowait(
        Telegram(
            destination_address=ga,
            direction=TelegramDirection.OUTGOING,
            payload=GroupValueWrite(b"\x01"),
        )
    )

    await xknx.telegram_queue._process_all_telegrams()

    assert len(received) == 3
    assert received == [
        (TelegramDirection.INCOMING, "1/1/1"),
        (TelegramDirection.OUTGOING, "1/1/1"),
        (TelegramDirection.INCOMING, "1/1/1"),
    ]

