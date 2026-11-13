"""Address search and accessibility assessment over the local catalogue."""

import sqlite3

import httpx
from anyio import CapacityLimiter, to_thread
from fastapi import APIRouter

from a2_api.adapters import address_access as access
from a2_api.core.errors import DomainError

router = APIRouter(prefix="/address-access")
route_slots = CapacityLimiter(2)


@router.post("/addresses")
def addresses(request: access.AddressQuery) -> list[access.Address]:
    try:
        return access.address_search(request.query)
    except sqlite3.OperationalError as exc:
        raise DomainError(503, "engine_unavailable", "Каталог адресов не подключён.") from exc


@router.post("/calculate")
async def calculate(request: access.AddressRequest) -> access.AddressResult:
    try:
        result = await to_thread.run_sync(access.address_result, request, limiter=route_slots)
    except sqlite3.OperationalError as exc:
        raise DomainError(503, "engine_unavailable", "Каталог адресов не подключён.") from exc
    except access.TransitAccessUnavailable as exc:
        raise DomainError(422, "data_unavailable", str(exc)) from exc
    except access.DrivingAccessUnavailable as exc:
        raise DomainError(
            422,
            "data_unavailable",
            "Для выбранной точки не подтверждён автомобильный проезд через КПП.",
        ) from exc
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        raise DomainError(
            503, "engine_unavailable", "Маршрутизатор не вернул результат. Повторите расчёт."
        ) from exc
    if result is None:
        raise DomainError(
            404,
            "unknown_id",
            "Точка вне покрытия. Выберите место в Москве или Московской области."
            if request.address_id is None
            else "Адрес отсутствует в каталоге.",
        )
    return result


@router.get("/districts")
def districts() -> list[access.District]:
    try:
        return access.districts()
    except sqlite3.OperationalError as exc:
        raise DomainError(
            503, "engine_unavailable", "Каталог районов и муниципалитетов не подключён."
        ) from exc


@router.get("/districts/{district_id}")
def district(district_id: str) -> access.DistrictResult:
    result = access.district_result(district_id)
    if result is None:
        raise DomainError(
            404, "unknown_id", "Для этой территории ещё нет расчёта по ячейкам населения."
        )
    return result
