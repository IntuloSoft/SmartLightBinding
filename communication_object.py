from __future__ import annotations

from collections.abc import Callable
from enum import IntFlag, auto
from typing import Any, Generic, Iterable, TypeVar, cast
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


class CommunicationObject(Generic[ValueT]):
    """Represents a KNX communication object similar to the ETS object model.

    The object tracks the active KNX flags, remembers the default configuration,
    and stores the current application value in a DPT-generic manner. The actual
    runtime type of `value` depends on the DPT used by the object.
    """

    READ_RESPONSE_DEDUPLICATION_WINDOW = 0.05  # 50 ms
    SENDING_GROUP_ADDRESS_INDEX = 0

    def __init__(
        self,
        name: str,
        *,
        dpt_class: type[DPTBase] | str | int | None = None,
        flags: Flags | int = Flags.NONE,
        configurable_flags: Flags | int | Iterable[Flags | int] | None = None,
        default_flags: Flags | int | None = None,
        value: ValueT | None = None,
        xknx: XKNX | None = None,
        group_addresses: object | list[object] | tuple[object, ...] | None = None,
        on_write_cb: Callable[[ValueT], None] | None = None,
        on_response_cb: Callable[[ValueT], None] | None = None,
    ) -> None:
        self.name = name
        self._configurable_flags = self._coerce_flag_set(
            configurable_flags
            if configurable_flags is not None
            else tuple(flag for flag in Flags if flag != Flags.NONE)
        )
        self.default_flags = self._coerce_flag_mask(
            default_flags if default_flags is not None else flags
        )
        self._flags = self.default_flags

        if flags is not None and default_flags is None:
            self._flags = self._coerce_flag_mask(flags)

        if self.default_flags & ~self._configurable_mask:
            raise ValueError(
                f"Default flags for '{self.name}' contain non-configurable bits."
            )
        if self._flags & ~self._configurable_mask:
            raise ValueError(
                f"Initial flags for '{self.name}' contain non-configurable bits."
            )

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
        self._last_read_request: dict[str, float] = {}

    @staticmethod
    def _coerce_flag_mask(value: Flags | int | None) -> Flags:
        if value is None:
            return Flags.NONE
        if isinstance(value, bool):
            value = int(value)
        return Flags(value)

    def _coerce_flag_set(self, values: Flags | int | Iterable[Flags | int]) -> set[Flags]:
        if values is None:
            return set()
        if isinstance(values, (Flags, int)) and not isinstance(values, bool):
            mask = self._coerce_flag_mask(values)
            return set(self._iter_flags(mask))
        normalized: set[Flags] = set()
        for value in values:
            normalized.add(self._coerce_flag_mask(value))
        return normalized

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
    def _configurable_mask(self) -> Flags:
        mask = Flags.NONE
        for flag in self._configurable_flags:
            mask |= flag
        return mask

    @property
    def flags(self) -> Flags:
        return self._flags

    @flags.setter
    def flags(self, value: Flags | int) -> None:
        new_flags = self._coerce_flag_mask(value)
        if new_flags & ~self._configurable_mask:
            raise ValueError(f"Flags for '{self.name}' contain non-configurable bits.")
        self._flags = new_flags

    @property
    def configurable_flags(self) -> tuple[Flags, ...]:
        return tuple(flag for flag in Flags if flag in self._configurable_flags)

    @property
    def value(self) -> ValueT | None:
        return self._value

    def is_set(self, flag: Flags | int) -> bool:
        return bool(self._flags & self._coerce_flag_mask(flag))

    def set_flag(self, flag: Flags | int, value: bool = True) -> None:
        mask = self._coerce_flag_mask(flag)
        if mask == Flags.NONE:
            return

        for candidate in self._iter_flags(mask):
            if candidate not in self._configurable_flags:
                raise ValueError(f"Flag '{candidate.name}' is not configurable for '{self.name}'.")
            if value:
                self._flags |= candidate
                if candidate == Flags.COMMUNICATION:
                    self.register()
            else:
                self._flags &= ~candidate
                if candidate == Flags.COMMUNICATION:
                    self.unregister()

    def clear_flag(self, flag: Flags | int) -> None:
        self.set_flag(flag, value=False)

    def update_flags(self, flags: Flags | int, *, enabled: bool = True) -> None:
        mask = self._coerce_flag_mask(flags)
        for flag in self._iter_flags(mask):
            self.set_flag(flag, value=enabled)

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

    def unregister(self) -> None:
        """Unregister telegram callback."""
        if self.xknx is None or self._telegram_cb is None:
            return
        self.xknx.telegram_queue.unregister_telegram_received_cb(self._telegram_cb)
        self._telegram_cb = None

    def _recently_responded_to_groupvalueread(self, ga: str) -> bool:
        now = monotonic()

        last_response = self._last_read_request.get(ga)
        if last_response is None:
            return False

        return now - last_response < self.READ_RESPONSE_DEDUPLICATION_WINDOW

    def _mark_responded_to_groupvalueread(self, ga: str) -> None:
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

            if self.xknx is not None and self.group_addresses is not None and self._value is not None:
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
        if self.group_addresses is None:
            raise RuntimeError("No group_addresses configured for CommunicationObject")
        telegram = Telegram(
            destination_address=group_address, # First GA is the sending GA
            payload=(GroupValueResponse(payload) if response else GroupValueWrite(payload)),
            source_address=self.xknx.current_address,
            direction=TelegramDirection.OUTGOING,
        )
        self.xknx.telegrams.put_nowait(telegram)

    def _transmit(self, value: ValueT):
        if self.is_set(Flags.COMMUNICATION) and self.is_set(Flags.TRANSMIT):
            if self.xknx is not None and self.group_addresses is not None:
                payload = self._to_knx(value)
                self._send_raw(self.group_addresses[CommunicationObject.SENDING_GROUP_ADDRESS_INDEX], payload, response = False)

    def __contains__(self, flag: Flags | int) -> bool:
        return self.is_set(flag)

    def __int__(self) -> int:
        return int(self._flags)

    def __repr__(self) -> str:
        return (
            f"CommunicationObject(name='{self.name}', flags={self._flags!r}, "
            f"configurable={self.configurable_flags}, default={self.default_flags!r}, "
            f"value={self._value!r}, dpt={getattr(self.dpt_class, '__name__', None)})"
        )

    @staticmethod
    def _iter_flags(mask: Flags) -> tuple[Flags, ...]:
        return tuple(
            flag for flag in Flags if flag != Flags.NONE and bool(mask & flag)
        )

    @staticmethod
    def _normalize_group_addresses(
        group_addresses: GroupAddress | list[GroupAddress] | tuple[GroupAddress, ...] | None,
    ) -> list[GroupAddress]:
        values: list[GroupAddress] = []

        if group_addresses is not None:
            if isinstance(group_addresses, (list, tuple, set)):
                values.extend(group_addresses)
            else:
                values.append(group_addresses)

        normalized: list[object] = []
        for item in values:
            if item is None:
                continue
            normalized.append(parse_device_group_address(item))
        return normalized


__all__ = ["Flags", "CommunicationObject"]
