"""Appointment routes proxied to the customer-portal with the cookie token.

* ``GET  /api/appointments``                -> list (portal: listAllApppointments)
* ``GET  /api/appointments/{id}``           -> details (portal: getAppointmentDetailsById)
* ``POST /api/appointments/book``           -> booking (portal: appointmentBook)

The portal envelope (``{statusCode, message, data, ...}``) is passed
through untouched so the frontend can read the same contract the portal
documents in Swagger.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import JSONResponse

from modules.portal.portal_http_client import CustomerPortalHttpClient, get_shared_portal_http_client
from modules.portal.portal_token_dependency import resolve_portal_token_from_request
from modules.portal.dtos import (
    AppointmentBookRequestDTO,
    AppointmentSortKey,
    AppointmentSortValues,
    AppointmentsFiltersDto,
    AppointmentsListRequestDTO,
    DtoValidationError,
)

router = APIRouter(prefix="/api/appointments", tags=["Appointments"])


def _dto_error(message: str) -> JSONResponse:
    """Portal-shaped error envelope for DTO validation failures."""
    return JSONResponse(
        status_code=400,
        content={"statusCode": 400, "message": message, "data": None, "errors": message},
    )


@router.get("")
async def list_appointments(
    page: int = Query(1, ge=1),
    perPage: int = Query(25, ge=1, le=200),
    startDate: Optional[str] = Query(None, description="YYYY-MM-DD"),
    endDate: Optional[str] = Query(None, description="YYYY-MM-DD"),
    search: Optional[str] = Query(None, min_length=2),
    status: list[str] = Query(default=[], description="Repeatable status filter"),
    token: str = Depends(resolve_portal_token_from_request),
    client: CustomerPortalHttpClient = Depends(get_shared_portal_http_client),
) -> dict:
    """List appointments for the signed-in user's location.

    Built as ``AppointmentsListRequestDTO`` (portal ``listAllApppointments``
    contract); an invalid status filter returns the portal error envelope.
    """
    filters: dict = {}
    if startDate:
        filters["startDate"] = startDate
    if endDate:
        filters["endDate"] = endDate
    if status:
        filters["status"] = list(status)
    try:
        request = AppointmentsListRequestDTO(
            sortKey=AppointmentSortKey.START_TIME,
            sortValue=AppointmentSortValues.DESC,
            page=page,
            perPage=perPage,
            filter=AppointmentsFiltersDto.from_dict(filters) if filters else None,
            search=search or None,
        ).validated()
    except DtoValidationError as error:
        return _dto_error(str(error))
    return await client.list_appointments(token, request.to_payload())


@router.get("/{appointment_id}")
async def appointment_details(
    appointment_id: int,
    source: str = Query("DAILY", description="DAILY or EMR"),
    token: str = Depends(resolve_portal_token_from_request),
    client: CustomerPortalHttpClient = Depends(get_shared_portal_http_client),
) -> dict:
    """Full details for one appointment."""
    return await client.get_appointment_details(token, appointment_id, source)


@router.post("/book")
async def book_appointment(
    payload: dict = Body(..., description="AppointmentBookRequestDTO shape"),
    token: str = Depends(resolve_portal_token_from_request),
    client: CustomerPortalHttpClient = Depends(get_shared_portal_http_client),
) -> dict:
    """Book an appointment using the signed-in portal token.

    The body is validated as ``AppointmentBookRequestDTO`` first, so missing
    or invalid booking details return the portal's error envelope instead of
    being forwarded.
    """
    try:
        request = AppointmentBookRequestDTO.from_dict(payload)
        print('-------------------this is the request ---------------{request}')
    except DtoValidationError as error:
        return _dto_error(str(error))
    appRes =await client.book_appointment(token, request)
    print('-------this is appRes ------{appRes}')
    return appRes
