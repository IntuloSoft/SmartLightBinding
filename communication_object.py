from __future__ import annotations

from enum import IntFlag, auto
from typing import Any, Generic, Iterable, TypeVar, cast

from xknx import XKNX
from xknx.dpt import DPTArray, DPTBase, DPTBinary
from xknx.telegram import Telegram, TelegramDirection
from xknx.telegram.apci import GroupValueWrite, GroupValueResponse, GroupValueRead
from xknx.telegram.address import parse_device_group_address

ValueT = TypeVar("ValueT")


class Flags(IntFlag):
    """Bitmask representing the KNX communication object flags."""

    NONE = 0
    READ = auto()
    WRITE = auto()
    TRANSMIT = auto()
    UPDATE = auto()
    READ_ON_INIT = auto()
    WRITE_ON_INIT = auto()


class CommunicationObject(Generic[ValueT]):
    """Represents a KNX communication object similar to the ETS object model.

    The object tracks the active KNX flags, remembers the default configuration,
    and stores the current application value in a DPT-generic manner. The actual
    runtime type of `value` depends on the DPT used by the object.
    """

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
        after_update_cb: callable | None = None,
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
        self._payload: DPTArray | DPTBinary | None = None
        self._value: ValueT | None = None

        # xknx integration
        self.xknx = xknx
        self.group_addresses = self._normalize_group_addresses(group_addresses)
        self.after_update_cb = after_update_cb
        self._telegram_cb = None

        if value is not None:
            self.value = value

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
    def default_value(self) -> int:
        return int(self.default_flags)

    @property
    def readable(self) -> bool:
        return self.is_set(Flags.READ)

    @property
    def writable(self) -> bool:
        return self.is_set(Flags.WRITE)

    @property
    def transmittable(self) -> bool:
        return self.is_set(Flags.TRANSMIT)

    @property
    def updateable(self) -> bool:
        return self.is_set(Flags.UPDATE)

    @property
    def read_on_init(self) -> bool:
        return self.is_set(Flags.READ_ON_INIT)

    @property
    def write_on_init(self) -> bool:
        return self.is_set(Flags.WRITE_ON_INIT)

    @property
    def value(self) -> ValueT | None:
        return self._value

    @value.setter
    def value(self, value: ValueT | None) -> None:
        if value is None:
            self._value = None
            self._payload = None
            return

        if self.dpt_class is not None:
            self._payload = self.to_knx(value)
        self._value = value

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
            else:
                self._flags &= ~candidate

    def clear_flag(self, flag: Flags | int) -> None:
        self.set_flag(flag, value=False)

    def reset(self) -> None:
        self._flags = self.default_flags

    def update_flags(self, flags: Flags | int, *, enabled: bool = True) -> None:
        mask = self._coerce_flag_mask(flags)
        for flag in self._iter_flags(mask):
            self.set_flag(flag, value=enabled)

    def to_knx(self, value: ValueT) -> DPTArray | DPTBinary:
        if self.dpt_class is None:
            raise ValueError(f"Communication object '{self.name}' has no DPT configured.")
        raw = value.value if hasattr(value, "value") else value
        return self.dpt_class.to_knx(raw)

    def from_knx(self, payload: DPTArray | DPTBinary) -> ValueT:
        if self.dpt_class is None:
            return cast(ValueT, payload)
        return cast(ValueT, self._materialize_value(self.dpt_class.from_knx(payload)))

    def set_value(self, value: ValueT, *, from_bus: bool = False) -> ValueT:
        if from_bus:
            if not self.updateable:
                raise ValueError(
                    f"Object '{self.name}' does not allow incoming updates because UPDATE is disabled."
                )
        elif not self.writable:
            raise ValueError(
                f"Object '{self.name}' does not allow application writes because WRITE is disabled."
            )

        self._store_value(value)

        if not from_bus and self.transmittable and self.xknx is not None and self.group_address is not None:
            self.transmit(value)

        return value

    def apply_telegram_value(
        self, payload: DPTArray | DPTBinary, *, from_bus: bool = True
    ) -> ValueT:
        if from_bus and not self.updateable:
            raise ValueError(
                f"Object '{self.name}' does not accept new values because UPDATE is disabled."
            )
        decoded = self.from_knx(payload)
        self._store_value(decoded)
        self._payload = payload
        return decoded

    @property
    def group_address(self) -> object | None:
        return self.group_addresses[0] if self.group_addresses else None

    @group_address.setter
    def group_address(self, value: object | None) -> None:
        if value is None:
            self.group_addresses = []
            return
        self.group_addresses = [parse_device_group_address(value)]

    def register(self) -> None:
        """Register telegram callback with XKNX if configured."""
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
        try:
            self.xknx.telegram_queue.unregister_telegram_received_cb(self._telegram_cb)
        except Exception:
            pass
        self._telegram_cb = None

    def _on_telegram(self, telegram: Telegram) -> None:
        """Internal telegram callback from xknx TelegramQueue."""
        from_bus = telegram.direction == TelegramDirection.INCOMING
        try:
            self._process_telegram(telegram, from_bus=from_bus)
        except Exception:
            # swallow errors to avoid breaking telegram processing
            pass

    def send_raw(self, payload: DPTArray | DPTBinary, response: bool = False) -> None:
        """Send payload as telegram to KNX bus using the configured XKNX instance."""
        self._send_raw(payload, response=response)

    def _send_raw(self, payload: DPTArray | DPTBinary, response: bool = False) -> None:
        """Send payload as telegram to KNX bus using the configured XKNX instance."""
        if self.xknx is None:
            raise RuntimeError("No xknx instance configured for CommunicationObject")
        if self.group_address is None:
            raise RuntimeError("No group_address configured for CommunicationObject")
        telegram = Telegram(
            destination_address=self.group_address,
            payload=(GroupValueResponse(payload) if response else GroupValueWrite(payload)),
            source_address=self.xknx.current_address,
            direction=TelegramDirection.OUTGOING,
        )
        self.xknx.telegrams.put_nowait(telegram)

    def process_telegram(self, telegram: Any, *, from_bus: bool = True) -> ValueT | None:
        """Public entry point for processing telegrams from the KNX bus."""
        return self._process_telegram(telegram, from_bus=from_bus)

    def _process_telegram(self, telegram: Any, *, from_bus: bool = True) -> ValueT | None:
        payload = getattr(telegram, "payload", None)
        if payload is None:
            return self.value

        if isinstance(payload, (DPTArray, DPTBinary)):
            raw_payload = payload
        elif hasattr(payload, "value") and isinstance(payload.value, (DPTArray, DPTBinary)):
            raw_payload = payload.value
        else:
            raw_payload = payload.value if hasattr(payload, "value") else payload

        if not isinstance(raw_payload, (DPTArray, DPTBinary)):
            if self.dpt_class is None:
                self._value = cast(ValueT, raw_payload)
                return self._value
            raise TypeError(
                f"Telegram payload for '{self.name}' is not a valid DPT payload: {raw_payload!r}"
            )

        return self.apply_telegram_value(raw_payload, from_bus=from_bus)

    def _store_value(self, value: ValueT) -> ValueT:
        self.value = value
        if self.after_update_cb is not None:
            try:
                self.after_update_cb(value)
            except Exception:
                pass
        return value

    def transmit(self, value: ValueT | None = None) -> DPTArray | DPTBinary:
        if not self.transmittable:
            raise ValueError(
                f"Object '{self.name}' cannot transmit because TRANSMIT is disabled."
            )
        current_value = self._value if value is None else value
        if current_value is None:
            raise ValueError(f"Object '{self.name}' has no value to transmit.")
        payload = self.to_knx(current_value)
        self._payload = payload
        self._value = current_value
        if self.xknx is not None and self.group_address is not None:
            self._send_raw(payload)
        return payload

    def init(self) -> None:
        """Perform initialization actions based on flags: read_on_init / write_on_init."""
        if self.xknx is None:
            return
        if self.write_on_init and self._value is not None:
            try:
                self.transmit(self._value)
            except Exception:
                pass
        if self.read_on_init and self.group_address is not None:
            telegram = Telegram(
                destination_address=self.group_address,
                payload=GroupValueRead(),
                source_address=self.xknx.current_address,
                direction=TelegramDirection.OUTGOING,
            )
            self.xknx.telegrams.put_nowait(telegram)

    def read(self) -> ValueT | None:
        if not self.readable:
            raise ValueError(f"Object '{self.name}' cannot be read because READ is disabled.")
        return self._value

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
        group_addresses: object | list[object] | tuple[object, ...] | None,
        group_address: object | None = None,
    ) -> list[object]:
        values: list[object] = []

        if group_addresses is not None:
            if isinstance(group_addresses, (list, tuple, set)):
                values.extend(group_addresses)
            else:
                values.append(group_addresses)

        if group_address is not None:
            values.append(group_address)

        normalized: list[object] = []
        for item in values:
            if item is None:
                continue
            normalized.append(parse_device_group_address(item))
        return normalized


__all__ = ["Flags", "CommunicationObject"]
