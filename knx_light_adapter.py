from __future__ import annotations

from typing import Callable

from xknx import XKNX

from communication_object import (
    CommunicationObject,
    Flags,
)

from xknx.dpt.dpt_1 import DPTBool
from xknx.dpt.dpt_5 import DPTValue1ByteUnsigned
from xknx.dpt.dpt_7 import DPT2ByteUnsigned
from xknx.dpt.dpt_232 import DPTColorRGB
from xknx.dpt.dpt_3 import DPTControlDimming

class KnxLightAdapter:

    def __init__(
        self,
        *,
        xknx: XKNX,
        entity_id: str,
        switch_addresses: list[str] | None = None,
        switch_state_addresses: list[str] | None = None,
        relative_dimming_addresses: list[str] | None = None,
        brightness_addresses: list[str] | None = None,
        brightness_state_addresses: list[str] | None = None,
        color_temperature_addresses: list[str] | None = None,
        color_temperature_state_addresses: list[str] | None = None,
        rgb_addresses: list[str] | None = None,
        rgb_state_addresses: list[str] | None = None,
        # Relative dimming behaviour
        dimming_time: float = 5.0,
        dimming_steps: int = 20,
        dimming_no_off: bool = False,
        dimming_no_on: bool = False,
    ) -> None:

        self._xknx = xknx
        self._entity_id = entity_id

        self._hass = None
        self._unsubscribe_state_listener: (Callable[[], None] | None) = None

        self._dimming_time = dimming_time
        self._dimming_steps = dimming_steps

        self._dimming_no_off = dimming_no_off
        self._dimming_no_on = dimming_no_on

        command_flags = (
            Flags.COMMUNICATION
            | Flags.WRITE
        )

        state_flags = (
            Flags.COMMUNICATION
            | Flags.READ
            | Flags.UPDATE
            | Flags.TRANSMIT
        )

        non_editable_flags = Flags.NONE

        self._switch_object = (
            CommunicationObject(
                name=f"{entity_id} Switch",
                xknx=xknx,
                dpt_class=DPTBool,
                group_addresses=switch_addresses,
                flags=command_flags,
                editable_flags=non_editable_flags,
                on_write_cb=self._on_switch_command,
            )
            if switch_addresses
            else None
        )

        self._switch_state_object = (
            CommunicationObject(
                name=f"{entity_id} Switch State",
                xknx=xknx,
                dpt_class=DPTBool,
                group_addresses=switch_state_addresses,
                flags=state_flags,
                editable_flags=non_editable_flags,
                value=False,
            )
            if switch_state_addresses
            else None
        )

        self._relative_dimming_object = (
            CommunicationObject(
                name=f"{entity_id} Relative Dimming",
                xknx=xknx,
                dpt_class=DPTControlDimming,
                group_addresses=relative_dimming_addresses,
                flags=command_flags,
                editable_flags=non_editable_flags,
                on_write_cb=self._on_relative_dimming_command,
            )
            if relative_dimming_addresses
            else None
        )

        self._brightness_object = (
            CommunicationObject(
                name=f"{entity_id} Brightness",
                xknx=xknx,
                dpt_class=DPTValue1ByteUnsigned,
                group_addresses=brightness_addresses,
                flags=command_flags,
                editable_flags=non_editable_flags,
                on_write_cb=self._on_brightness_command,
            )
            if brightness_addresses
            else None
        )

        self._brightness_state_object = (
            CommunicationObject(
                name=f"{entity_id} Brightness State",
                xknx=xknx,
                dpt_class=DPTValue1ByteUnsigned,
                group_addresses=brightness_state_addresses,
                flags=state_flags,
                editable_flags=non_editable_flags,
                value=0,
            )
            if brightness_state_addresses
            else None
        )

        self._color_temperature_object = (
            CommunicationObject(
                name=f"{entity_id} Color Temperature",
                xknx=xknx,
                dpt_class=DPT2ByteUnsigned,
                group_addresses=color_temperature_addresses,
                flags=command_flags,
                editable_flags=non_editable_flags,
                on_write_cb=self._on_color_temperature_command,
            )
            if color_temperature_addresses
            else None
        )

        self._color_temperature_state_object = (
            CommunicationObject(
                name=f"{entity_id} Color Temperature State",
                xknx=xknx,
                dpt_class=DPT2ByteUnsigned,
                group_addresses=color_temperature_state_addresses,
                flags=state_flags,
                editable_flags=non_editable_flags,
                value=0,
            )
            if color_temperature_state_addresses
            else None
        )

        self._rgb_object = (
            CommunicationObject(
                name=f"{entity_id} RGB",
                xknx=xknx,
                dpt_class=DPTColorRGB,
                group_addresses=rgb_addresses,
                flags=command_flags,
                editable_flags=non_editable_flags,
                on_write_cb=self._on_rgb_command,
            )
            if rgb_addresses
            else None
        )

        self._rgb_state_object = (
            CommunicationObject(
                name=f"{entity_id} RGB State",
                xknx=xknx,
                dpt_class=DPTColorRGB,
                group_addresses=rgb_state_addresses,
                flags=state_flags,
                editable_flags=non_editable_flags,
                value=(0, 0, 0),
            )
            if rgb_state_addresses
            else None
        )

    async def attach(
        self,
        hass,
    ) -> None:
        """
        Bind adapter to Home Assistant.
        """
        raise NotImplementedError

    async def detach(
        self,
    ) -> None:
        """
        Cleanup subscriptions.
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # KNX object callbacks
    # ------------------------------------------------------------------

    def _on_switch_command(
        self,
        value: bool,
    ) -> None:
        """Handle KNX switch command."""
        raise NotImplementedError


    def _on_relative_dimming_command(
        self,
        value,
    ) -> None:
        """Handle KNX relative dimming command."""
        raise NotImplementedError


    def _on_brightness_command(
        self,
        value: int,
    ) -> None:
        """Handle KNX absolute brightness command."""
        raise NotImplementedError


    def _on_color_temperature_command(
        self,
        value: int,
    ) -> None:
        """Handle KNX color temperature command."""
        raise NotImplementedError


    def _on_rgb_command(
        self,
        value,
    ) -> None:
        """Handle KNX RGB command."""
        raise NotImplementedError


    # ------------------------------------------------------------------
    # Home Assistant Light API
    # ------------------------------------------------------------------

    def _on_entity_state_changed(
        self,
        old_state,
        new_state,
    ) -> None:
        raise NotImplementedError
