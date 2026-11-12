"""Address search and pedestrian assessment over the local catalogue."""

import sqlite3
from threading import BoundedSemaphore

import httpx
from fastapi import APIRouter

from a2_api.adapters import address_access as access
from a2_api.core.errors import DomainError

router = APIRouter(prefix="/address-access")
route_slots = BoundedSemaphore(2)


@router.post("/addresses")
def addresses(request: access.AddressQuery) -> list[access.Address]:
    try:
        return access.address_search(request.query)
    except sqlite3.OperationalError as exc:
        raise DomainError(503, "engine_unavailable", "Каталог адресов не подключён.") from exc


@router.post("/calculate")
def calculate(request: access.AddressRequest) -> access.AddressResult:
    if not route_slots.acquire(blocking=False):
        raise DomainError(
            429, "budget_exceeded", "Пеший маршрутизатор занят. Повторите расчёт.", retryable=True
        )
    try:
        result = access.address_result(request)
    except sqlite3.OperationalError as exc:
        raise DomainError(503, "engine_unavailable", "Каталог адресов не подключён.") from exc
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        raise DomainError(
            503, "engine_unavailable", "Пеший маршрутизатор не вернул результат. Повторите расчёт."
        ) from exc
    finally:
        route_slots.release()
    if result is None:
        raise DomainError(404, "unknown_id", "Адрес отсутствует в каталоге.")
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
