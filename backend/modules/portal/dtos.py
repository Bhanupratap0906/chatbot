"""Typed request DTOs mirroring the customer-portal's TypeScript DTOs.

Source of truth (a field rename/requirement change there is a one-place
diff here)::

    customer-portal/src/
      modules/appointments/dto/appointment-book.request.dto.ts          -> AppointmentBookRequestDTO
      modules/appointments/dto/get-appointment-slots-request.dto.ts     -> GetAppointmentSlotsRequestDto
      modules/appointments/dto/appointment-list-request.dto.ts          -> AppointmentsListRequestDTO
                                                                   + AppointmentsFiltersDto
      modules/appointments/dto/get-service-by-location-id-request.dto.ts -> GetServiceByLocationIdRequestDto
      modules/appointments/dto/get-appointment-details-by-appointment-id-request.dto.ts
                                                                       -> GetAppointmentDetailsByIdRequestDTO
      modules/patient-module/dto/get-patient-details-by-mobilenumber-request.dto.ts
                                                                       -> GetPatientDetailsByMobileNumberRequestDto
      shared/common/enums/appointment.enums.ts                          -> AppointmentStatusType / AppointmentSource
      modules/appointments/dto/appointment-list-request.dto.ts (Statuses) -> AppointmentListStatus

The class-validator rules in those files (``@IsNotEmpty``, ``@IsNumber``,
``@IsEnum``, ``@IsArray``) are mirrored as ``REQUIRED`` field sets and enum
membership checks, so an incomplete booking fails *here* with a precise
message instead of a 400 from the portal mid-call.

Field names stay camelCase on purpose: they are the exact wire contract,
and ``to_payload()`` serializes them unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from enum import Enum
from functools import lru_cache
from typing import Any, Mapping, Optional, get_args, get_origin, get_type_hints


class DtoValidationError(ValueError):
    """A payload did not satisfy the portal DTO's class-validator rules."""


# ---------- enums (shared/common/enums/appointment.enums.ts) ----------


class AppointmentStatusType(str, Enum):
    """``AppointmentStatusType`` — required on every booked appointment."""

    CONFIRMED = "Confirmed"
    COMPLETED = "Completed"
    SCHEDULED = "Scheduled"
    CANCELLED = "Cancelled"
    UNCONFIRMED = "Unconfirmed"
    NO_SHOW = "No Show"
    UNKNOWN = "Unknown"


class AppointmentSource(str, Enum):
    """``AppointmentSource`` — which system an appointment/detail comes from."""

    DAILY = "DAILY"
    ONLINE = "ONLINE"
    EMR = "EMR"
    ALL = "ALL"


class AppointmentListStatus(str, Enum):
    """``Statuses`` from appointment-list-request.dto.ts (list filter values)."""

    CONFIRMED = "Confirmed"
    COMPLETED = "Completed"
    SCHEDULED = "Scheduled"
    CANCELLED = "Cancelled"
    UNCONFIRMED = "Unconfirmed"
    NOSHOW = "No Show"
    UNKNOWN = "Unknown"
    DECLINED = "Declined"
    AWAITING = "Awaiting"


class AppointmentSortKey(str, Enum):
    """``AppointmentSortKey`` — allowed ``sortKey`` values."""

    PATIENT_NAME = "patientName"
    PROVIDER_NAME = "providerName"
    SERVICES = "services"
    START_TIME = "startTime"
    SOURCE = "source"
    STATUS = "status"
    CREATED_AT = "createdAt"
    REMINDER_START_TIME = "reminderStartTime"


class AppointmentSortValues(str, Enum):
    """``AppointmentSortValues`` — allowed ``sortValue`` values."""

    DESC = "desc"
    ASC = "asc"


class SlotViewType(str, Enum):
    """``viewTypeEnum`` — slot calendar view granularity."""

    MONTH = "month"
    DAY = "day"


# ---------- shared validation / serialization ----------


@lru_cache(maxsize=None)
def _hints(cls: type) -> dict[str, Any]:
    """Resolved annotations for a DTO class (cached; ``from __future__`` strings)."""
    return get_type_hints(cls)


class PortalDto:
    """Mixin: class-validator-style checks + whitelist serialization.

    * ``REQUIRED`` mirrors ``@IsNotEmpty`` fields (plus enums/arrays that
      the portal's validators reject when absent).
    * ``to_payload()`` drops ``None`` optionals and enum-encodes values —
      the same shape the portal's whitelist would accept.
    * ``from_dict()`` coerces enums/nested DTOs and raises
      :class:`DtoValidationError` on missing/invalid fields. Unknown keys
      are ignored, matching class-validator's ``whitelist`` stripping.
    """

    REQUIRED: frozenset[str] = frozenset()

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PortalDto":
        """Build and validate a DTO from a decoded JSON body."""
        hints = _hints(cls)
        known = {f.name for f in fields(cls)}  # type: ignore[arg-type]
        kwargs: dict[str, Any] = {}
        for key, value in payload.items():
            if key not in known or value is None:
                continue
            hint = hints.get(key)
            try:
                if isinstance(hint, type) and issubclass(hint, Enum) and not isinstance(value, hint):
                    value = hint(value)
                elif isinstance(hint, type) and issubclass(hint, PortalDto) and isinstance(value, Mapping):
                    value = hint.from_dict(value)  # type: ignore[attr-defined]
            except ValueError as error:
                raise DtoValidationError(f"{cls.__name__}.{key}: {error}") from error
            kwargs[key] = value
        try:
            instance = cls(**kwargs)  # type: ignore[call-arg]
        except TypeError as error:
            missing = sorted(name for name in cls.REQUIRED if name not in kwargs)
            detail = (
                "missing required field(s): " + ", ".join(missing)
                if missing
                else str(error)
            )
            raise DtoValidationError(f"{cls.__name__}: {detail}") from error
        instance.validate()
        return instance

    def validate(self) -> None:
        """Raise :class:`DtoValidationError` when required/enum rules fail."""
        cls = type(self)
        problems: list[str] = []
        missing = [
            name
            for name in sorted(self.REQUIRED)
            if _is_empty(getattr(self, name, None))
        ]
        if missing:
            problems.append("missing required field(s): " + ", ".join(missing))
        for f in fields(self):  # type: ignore[arg-type]
            value = getattr(self, f.name, None)
            if value is None:
                continue
            hint = _hints(cls).get(f.name)
            if _enum_of(hint) is not None and not isinstance(value, _enum_of(hint)):
                allowed = ", ".join(repr(m.value) for m in _enum_of(hint))  # type: ignore[arg-type]
                problems.append(f"{f.name} must be one of: {allowed}")
        if problems:
            raise DtoValidationError(f"{cls.__name__}: " + "; ".join(problems))

    def validated(self) -> "PortalDto":
        """Validate and return ``self`` (fluent construction helper)."""
        self.validate()
        return self

    def to_payload(self) -> dict[str, Any]:
        """Serialize to the exact JSON body/query dict the portal expects."""
        self.validate()
        payload: dict[str, Any] = {}
        for f in fields(self):  # type: ignore[arg-type]
            value = getattr(self, f.name)
            if value is None:
                continue
            payload[f.name] = _encode(value)
        return payload


def _is_empty(value: Any) -> bool:
    """True for None, blank strings, and empty lists (``@IsNotEmpty`` semantics)."""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict)):
        return not value
    return False


def _encode(value: Any) -> Any:
    """Recursively convert enums/DTOs/lists into JSON-ready primitives."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, PortalDto):
        return value.to_payload()
    if isinstance(value, (list, tuple)):
        return [_encode(item) for item in value]
    if isinstance(value, dict):
        return {key: _encode(item) for key, item in value.items()}
    return value


def _enum_of(hint: Any) -> Optional[type[Enum]]:
    """Return the enum class behind ``hint`` (direct or Optional/Union)."""
    if isinstance(hint, type) and issubclass(hint, Enum):
        return hint
    if get_origin(hint) is not None:
        for arg in get_args(hint):
            enum_cls = _enum_of(arg)
            if enum_cls is not None:
                return enum_cls
    return None


# ---------- appointment booking ----------


@dataclass
class AppointmentBookRequestDTO(PortalDto):
    """``POST /appointments/appointmentBook`` body.

    Source: ``modules/appointments/dto/appointment-book.request.dto.ts``.
    Required there: ``patientId``, ``providerId``, ``reason`` (procedure ids),
    ``startTime``, ``endTime``, ``appointmentStatus``.
    """

    patientId: int
    providerId: int
    reason: list[int]
    startTime: str
    endTime: str
    appointmentStatus: AppointmentStatusType
    preferredName: Optional[str] = None
    mobileNumber: Optional[str] = None
    dateOfBirth: Optional[str] = None
    operatoryId: Optional[int] = None
    appointmentDescription: Optional[str] = None
    sendConfirmationMessage: Optional[bool] = None

    REQUIRED = frozenset({
        "patientId", "providerId", "reason", "startTime", "endTime",
        "appointmentStatus",
    })


# ---------- slot availability ----------


@dataclass
class GetAppointmentSlotsRequestDto(PortalDto):
    """``POST /appointments/getAppointmentSlots`` body.

    Source: ``modules/appointments/dto/get-appointment-slots-request.dto.ts``.
    """

    viewType: SlotViewType
    serviceId: int
    locationId: str
    startDate: str
    endDate: str
    providerId: Optional[int] = None

    REQUIRED = frozenset({"viewType", "serviceId", "locationId", "startDate", "endDate"})


# ---------- appointment listing ----------


@dataclass
class AppointmentsFiltersDto(PortalDto):
    """``filter`` object of the list request (``AppointmentsFiltersDto``)."""

    startDate: Optional[str] = None
    endDate: Optional[str] = None
    status: Optional[list[str]] = None
    providerName: Optional[list[str]] = None
    services: Optional[list[str]] = None

    def validate(self) -> None:
        """Additionally enforce the ``Statuses`` enum on each status entry."""
        super().validate()
        allowed = {status.value for status in AppointmentListStatus}
        invalid = sorted(set(self.status or []) - allowed)
        if invalid:
            raise DtoValidationError(
                "AppointmentsFiltersDto: status must be one of: "
                + ", ".join(sorted(allowed))
                + f" (got {', '.join(invalid)})"
            )


@dataclass
class AppointmentsListRequestDTO(PortalDto):
    """``POST /appointments/listAllApppointments`` body.

    Source: ``modules/appointments/dto/appointment-list-request.dto.ts``.
    ``sortKey``/``sortValue`` carry ``@IsEnum`` without ``@IsOptional``, so
    they are required here as well.
    """

    sortKey: AppointmentSortKey
    sortValue: AppointmentSortValues
    page: Optional[int] = None
    perPage: Optional[int] = None
    filter: Optional[AppointmentsFiltersDto] = None
    search: Optional[str] = None

    REQUIRED = frozenset({"sortKey", "sortValue"})


# ---------- lookups ----------


@dataclass
class GetServiceByLocationIdRequestDto(PortalDto):
    """``GET /appointments/getServiceByLocationId`` query.

    Source: ``modules/appointments/dto/get-service-by-location-id-request.dto.ts``.
    """

    locationId: str
    providerId: Optional[int] = None

    REQUIRED = frozenset({"locationId"})


@dataclass
class GetAppointmentDetailsByIdRequestDTO(PortalDto):
    """``GET /appointments/getAppointmentDetailsById/{id}`` query.

    Source: ``modules/appointments/dto/get-appointment-details-by-appointment-id-request.dto.ts``.
    """

    source: str

    REQUIRED = frozenset({"source"})


@dataclass
class GetPatientDetailsByMobileNumberRequestDto(PortalDto):
    """``POST /patient/by-mobile`` body.

    Source: ``modules/patient-module/dto/get-patient-details-by-mobilenumber-request.dto.ts``.
    """

    mobileNumber: str
    name: Optional[str] = None

    REQUIRED = frozenset({"mobileNumber"})


__all__ = [
    "AppointmentBookRequestDTO",
    "AppointmentListStatus",
    "AppointmentSortKey",
    "AppointmentSortValues",
    "AppointmentSource",
    "AppointmentStatusType",
    "AppointmentsFiltersDto",
    "AppointmentsListRequestDTO",
    "DtoValidationError",
    "GetAppointmentDetailsByIdRequestDTO",
    "GetAppointmentSlotsRequestDto",
    "GetPatientDetailsByMobileNumberRequestDto",
    "GetServiceByLocationIdRequestDto",
    "PortalDto",
    "SlotViewType",
]
