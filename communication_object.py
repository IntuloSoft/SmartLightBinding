from __future__ import annotations

from collections.abc import Callable
from enum import IntFlag, auto
from typing import Any, Generic, TypeVar, cast
from time import monotonic

from xknx import XKNX
from xknx.dpt import DPTArray, DPTBase, DPTBinary
from xknx.telegram import Telegram, TelegramDirection, GroupAddress
from xknx.telegram.apci import GroupValueWrite, GroupValueResponse, GroupValueRead
from xknx.telegram.address import parse_device_group_address

ValueT = TypeVar("ValueT")

class Flags(IntFlag):
    """Bitmask representing the KNX communication object flags."""
    NONE = 0
    COMMUNICATION = auto()
    READ = auto()
    WRITE = auto()
    TRANSMIT = auto()
    UPDATE = auto()

    ALL = COMMUNICATION | READ | WRITE | TRANSMIT | UPDATE


class CommunicationObject(Generic[ValueT]):
    """Represents a KNX communication object similar to the ETS object model.

    The object tracks the active KNX flags, the editable flags,
    and stores the current application value in a DPT-generic manner. The actual
    runtime type of `value` depends on the DPT used by the object.
    """

    GROUPVALUEREAD_DEDUPLICATION_WINDOW = 0.05  # 50 ms

    def __init__(
        self,
        name: str,
        *,
        group_addresses: GroupAddress | str | list[GroupAddress | str] | None = None,
        dpt_class: type[DPTBase] | str | int | None = None,
        flags: Flags | int = Flags.NONE,
        editable_flags: Flags | int = Flags.ALL,
        value: ValueT | None = None,
        xknx: XKNX | None = None,
        on_write_cb: Callable[[ValueT], None] | None = None,
        on_response_cb: Callable[[ValueT], None] | None = None,
    ) -> None:
        self.name = name
        self._flags = self._coerce_flag_mask(flags)
        self._editable_flags = self._coerce_flag_mask(editable_flags)

        CommunicationObject._validate_flags(self.name, self._editable_flags, "editable_flags")
        CommunicationObject._validate_flags(self.name, self._flags, "flags")

        self.dpt_class = self._resolve_dpt_class(dpt_class)
        self._value: ValueT | None = None

        # xknx integration
        self.xknx = xknx
        self.group_addresses = self._normalize_group_addresses(group_addresses)
        self.on_write_cb = on_write_cb
        self.on_response_cb = on_response_cb
        self._telegram_cb = None

        if value is not None:
            self._value = value

        # read request deduplication
        self._last_read_request: dict[GroupAddress, float] = {}

        if self.is_set(Flags.COMMUNICATION):
            self.register()

    @staticmethod
    def _validate_flags(name: str, value: Flags | int, description: str) -> None:
        invalid = int(value) & ~int(Flags.ALL)
        if invalid:
            raise ValueError(
                f"Unknown {description} for {name}: 0x{invalid:X}"
            )

    @staticmethod
    def _coerce_flag_mask(value: Flags | int | None) -> Flags:
        if value is None:
            return Flags.NONE
        return Flags(value)

    @staticmethod
    def _materialize_value(value: Any) -> Any:
        if hasattr(value, "value") and not isinstance(
            value, (str, bytes, int, float, bool, tuple, list, dict)
        ):
            return value.value
        return value

    @staticmethod
    def _resolve_dpt_class(
        dpt_class: type[DPTBase] | str | int | None,
    ) -> type[DPTBase] | None:
        if dpt_class is None:
            return None
        if isinstance(dpt_class, type) and issubclass(dpt_class, DPTBase):
            return dpt_class
        if isinstance(dpt_class, (str, int)):
            return DPTBase.get_dpt(dpt_class)
        raise ValueError(f"Unsupported DPT definition: {dpt_class!r}")

    @property
    def flags(self) -> Flags:
        return self._flags

    @flags.setter
    def flags(self, flags: Flags | int) -> None:
        new_flags = self._coerce_flag_mask(flags)

        changed = self._flags ^ new_flags

        self._validate_editable(changed)

        old_flags = self._flags
        self._flags = new_flags

        self._handle_flag_changes(old_flags, new_flags)

    @property
    def editable_flags(self) -> Flags:
        return self._editable_flags

    @property
    def value(self) -> ValueT | None:
        return self._value

    def is_set(self, flag: Flags | int) -> bool:
        return bool(self._flags & self._coerce_flag_mask(flag))

    def _validate_editable(self, mask: Flags) -> None:
        non_editable = mask & ~self._editable_flags
        if non_editable:
            raise ValueError(
                f"Flags {non_editable!r} are not editable for '{self.name}'."
            )

    def set_flags(self, flags: Flags | int) -> None:
        mask = self._coerce_flag_mask(flags)

        if mask == Flags.NONE:
            return

        self._validate_editable(mask)

        old_flags = self._flags
        self._flags |= mask

        self._handle_flag_changes(old_flags, self._flags)

    def clear_flags(self, flags: Flags | int) -> None:
        mask = self._coerce_flag_mask(flags)

        if mask == Flags.NONE:
            return

        self._validate_editable(mask)

        old_flags = self._flags
        self._flags &= ~mask

        self._handle_flag_changes(old_flags, self._flags)

    def _handle_flag_changes(
        self,
        old_flags: Flags,
        new_flags: Flags,
    ) -> None:
        communication_was_enabled = bool(old_flags & Flags.COMMUNICATION)
        communication_is_enabled = bool(new_flags & Flags.COMMUNICATION)

        if not communication_was_enabled and communication_is_enabled:
            self.register()

        elif communication_was_enabled and not communication_is_enabled:
            self.unregister()

    def _to_knx(self, value: ValueT) -> DPTArray | DPTBinary:
        if self.dpt_class is None:
            raise ValueError(f"Communication object '{self.name}' has no DPT configured.")
        raw = value.value if hasattr(value, "value") else value
        return self.dpt_class.to_knx(raw)

    def _from_knx(self, payload: DPTArray | DPTBinary) -> ValueT:
        if self.dpt_class is None:
            return cast(ValueT, payload)
        return cast(ValueT, self._materialize_value(self.dpt_class.from_knx(payload)))

    def set_value(self, value: ValueT) -> None:
        if value is None:
            raise ValueError(f"CommunicationObject::set_value '{self.name}' has no value.")

        self._value = value
        self._transmit(value)

    def register(self) -> None:
        """Register telegram callback with XKNX if configured."""
        if self._telegram_cb is not None:
            raise RuntimeError(f"{self.name} {self.group_addresses} - cannot register twice")

        if not self.is_set(Flags.COMMUNICATION):
            return

        if self.xknx is None:
            return
        if not self.group_addresses:
            return
        self._telegram_cb = self.xknx.telegram_queue.register_telegram_received_cb(
            self._on_telegram,
            group_addresses=self.group_addresses,
            match_for_outgoing=True,
        )

    @property
    def sending_group_address(self) -> GroupAddress:
        if not self.group_addresses:
            raise RuntimeError(
                f"CommunicationObject '{self.name}' has no group address."
            )

        return self.group_addresses[0]

    def unregister(self) -> None:
        """Unregister telegram callback."""
        if self.xknx is None or self._telegram_cb is None:
            return
        self.xknx.telegram_queue.unregister_telegram_received_cb(self._telegram_cb)
        self._telegram_cb = None

    def _recently_responded_to_groupvalueread(self, ga: GroupAddress) -> bool:
        now = monotonic()

        last_response = self._last_read_request.get(ga)
        if last_response is None:
            return False

        return now - last_response < self.GROUPVALUEREAD_DEDUPLICATION_WINDOW

    def _mark_responded_to_groupvalueread(self, ga: GroupAddress) -> None:
        self._last_read_request[ga] = monotonic()

    def _on_telegram(self, telegram: Telegram) -> None:
        """Internal telegram callback from xknx TelegramQueue."""

        if not self.is_set(Flags.COMMUNICATION):
            return

        # from_bus = telegram.direction == TelegramDirection.INCOMING
        payload = telegram.payload
        if payload is None:
            return

        if isinstance(payload, GroupValueRead) and self.is_set(Flags.READ):
            ga = telegram.destination_address

            if self._recently_responded_to_groupvalueread(ga):
                return

            if self._value is not None:
                self._mark_responded_to_groupvalueread(ga)
                self._send_raw(ga, self._to_knx(self._value), response=True)
            return

        if isinstance(payload, GroupValueWrite):
            new_value = self._from_knx(payload.value)

            if self.is_set(Flags.UPDATE):
                self._value = new_value

            if self.is_set(Flags.WRITE) and self.on_write_cb is not None:
                self.on_write_cb(new_value)

        if isinstance(payload, GroupValueResponse):
            new_value = self._from_knx(payload.value)

            if self.is_set(Flags.UPDATE):
                self._value = new_value
            
            if self.is_set(Flags.WRITE) and self.on_response_cb is not None:
                self.on_response_cb(new_value)


    def _send_raw(self, group_address: GroupAddress, payload: DPTArray | DPTBinary, response: bool = False) -> None:
        """Send payload as telegram to KNX bus using the configured XKNX instance."""
        if self.xknx is None:
            raise RuntimeError("No xknx instance configured for CommunicationObject")
        if not self.group_addresses:
            return
        telegram = Telegram(
            destination_address=group_address, # First GA is the sending GA
            payload=(GroupValueResponse(payload) if response else GroupValueWrite(payload)),
            source_address=self.xknx.current_address,
            direction=TelegramDirection.OUTGOING,
        )
        self.xknx.telegrams.put_nowait(telegram)

    def _transmit(self, value: ValueT) -> None:
        if not (self.is_set(Flags.COMMUNICATION) and self.is_set(Flags.TRANSMIT)):
            return
        if not self.group_addresses:
            return

        self._send_raw(
            self.sending_group_address,
            payload = self._to_knx(value),
            response = False
        )

    def __contains__(self, flag: Flags | int) -> bool:
        return self.is_set(flag)

    def __int__(self) -> int:
        return int(self._flags)

    def __repr__(self) -> str:
        return (
            f"CommunicationObject("
            f"name={self.name!r}, "
            f"flags={self._flags!r}, "
            f"editable_flags={self._editable_flags!r}, "
            f"value={self._value!r}, "
            f"dpt={getattr(self.dpt_class, '__name__', None)!r}"
            f")"
        )

    @staticmethod
    def _normalize_group_addresses(
        group_addresses:
            GroupAddress
            | str
            | list[GroupAddress | str]
            | tuple[GroupAddress | str, ...]
            | None,
    ) -> list[GroupAddress]:
        values: list[GroupAddress | str] = []

        if group_addresses is not None:
            if isinstance(group_addresses, (list, tuple, set)):
                values.extend(group_addresses)
            else:
                values.append(group_addresses)

        normalized: list[GroupAddress] = []
        for item in values:
            if item is None:
                continue
            normalized.append(parse_device_group_address(item))
        return normalized


__all__ = ["Flags", "CommunicationObject"]
