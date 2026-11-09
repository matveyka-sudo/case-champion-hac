"""PostgreSQL deployment-wide reservations for the existing Flash assistant protocol.

The ledger accounts conservatively: cached input is charged at the ordinary input
rate, and uncertain provider usage retains the entire reservation indefinitely.
No row, monetary ceiling, token ceiling, or live authorization is created here.
"""

import re
from dataclasses import dataclass
from decimal import Decimal

import asyncpg  # type: ignore[import-untyped]

TARIFF_ID = "aliceai-llm-flash-sync-rub-2026-11-09"
MODEL_PATTERN = re.compile(r"^gpt://[a-zA-Z0-9_-]{1,128}/aliceai-llm-flash$")
MAX_INPUT_TOKENS = 30000
MAX_OUTPUT_TOKENS = 2000
MAX_BIGINT = 2**63 - 1


@dataclass(frozen=True)
class Tariff:
    currency: str
    input_per_1000: Decimal
    output_per_1000: Decimal
    cached_input_per_1000: Decimal


# Add future verified revisions/currencies here; config selects a known revision.
TARIFFS = {TARIFF_ID: Tariff("RUB", Decimal("0.10"), Decimal("0.20"), Decimal("0.025"))}


class BudgetUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Deployment budget is unavailable or inconsistent")


@dataclass(frozen=True)
class BudgetSnapshot:
    currency: str
    tariff_id: str
    model_uri: str
    limit_amount: Decimal
    charged_amount: Decimal
    held_amount: Decimal
    token_limit: int
    charged_tokens: int
    held_tokens: int
    reservation_count: int
    blocked: bool

    @property
    def available_amount(self) -> Decimal:
        return max(Decimal("0"), self.limit_amount - self.charged_amount - self.held_amount)

    @property
    def available_tokens(self) -> int:
        return max(0, self.token_limit - self.charged_tokens - self.held_tokens)


def _tariff(tariff_id: str, currency: str) -> Tariff | None:
    selected = TARIFFS.get(tariff_id)
    return selected if selected is not None and selected.currency == currency else None


def _amount(tariff: Tariff, input_tokens: int, output_tokens: int) -> Decimal:
    # Usage does not identify cached tokens reliably, so charge every input at
    # the higher ordinary-input rate and never assume a cache discount.
    return (tariff.input_per_1000 * input_tokens + tariff.output_per_1000 * output_tokens) / 1000


def _valid_config(
    limit_amount: Decimal, token_limit: int, model_uri: str, currency: str, tariff_id: str
) -> bool:
    return bool(
        isinstance(limit_amount, Decimal)
        and limit_amount.is_finite()
        and limit_amount > 0
        and isinstance(token_limit, int)
        and not isinstance(token_limit, bool)
        and 0 < token_limit <= MAX_BIGINT
        and MODEL_PATTERN.fullmatch(model_uri)
        and _tariff(tariff_id, currency) is not None
    )


async def configure_budget(
    pool: asyncpg.Pool,
    *,
    limit_amount: Decimal,
    token_limit: int,
    model_uri: str,
    currency: str,
    tariff_id: str,
) -> None:
    """Explicit admin operation; can only raise existing ceilings, never reset totals."""
    if not _valid_config(limit_amount, token_limit, model_uri, currency, tariff_id):
        raise BudgetUnavailable()
    try:
        async with pool.acquire() as connection, connection.transaction():
            row = await connection.fetchrow(
                "SELECT * FROM assistant_budget WHERE budget_key='deployment' FOR UPDATE"
            )
            if row is None:
                await connection.execute(
                    """INSERT INTO assistant_budget
                    (budget_key, model_uri, currency, tariff_id, limit_amount, token_limit)
                    VALUES ('deployment',$1,$2,$3,$4,$5)""",
                    model_uri,
                    currency,
                    tariff_id,
                    limit_amount,
                    token_limit,
                )
                return
            if (
                row["model_uri"] != model_uri
                or row["currency"] != currency
                or row["tariff_id"] != tariff_id
                or limit_amount < row["limit_amount"]
                or token_limit < row["token_limit"]
            ):
                raise BudgetUnavailable()
            await connection.execute(
                """UPDATE assistant_budget SET limit_amount=$1, token_limit=$2,
                updated_at=now() WHERE budget_key='deployment'""",
                limit_amount,
                token_limit,
            )
    except BudgetUnavailable:
        raise
    except Exception:
        raise BudgetUnavailable() from None


class PostgresDeploymentBudget:
    """One singleton budget row serializes all API-process reservations."""

    durable = True

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        model_uri: str,
        limit_amount: Decimal,
        token_limit: int,
        currency: str,
        tariff_id: str,
    ) -> None:
        self.pool = pool
        self.model_uri = model_uri
        self.limit_amount = limit_amount
        self.token_limit = token_limit
        self.currency = currency
        self.tariff_id = tariff_id

    def _matches(self, row: asyncpg.Record | None) -> bool:
        return bool(
            row is not None
            and row["model_uri"] == self.model_uri
            and row["currency"] == self.currency
            and row["tariff_id"] == self.tariff_id
            and row["limit_amount"] == self.limit_amount
            and row["token_limit"] == self.token_limit
        )

    async def reserve(
        self, *, reservation_id: str, model: str, max_input_tokens: int, max_output_tokens: int
    ) -> bool:
        tariff = _tariff(self.tariff_id, self.currency)
        if (
            tariff is None
            or not _valid_config(
                self.limit_amount, self.token_limit, self.model_uri, self.currency, self.tariff_id
            )
            or model != self.model_uri
            or not isinstance(reservation_id, str)
            or not 0 < len(reservation_id) <= 200
            or not isinstance(max_input_tokens, int)
            or not isinstance(max_output_tokens, int)
            or isinstance(max_input_tokens, bool)
            or isinstance(max_output_tokens, bool)
            or not 0 < max_input_tokens <= MAX_INPUT_TOKENS
            or not 0 < max_output_tokens <= MAX_OUTPUT_TOKENS
        ):
            return False
        amount = _amount(tariff, max_input_tokens, max_output_tokens)
        tokens = max_input_tokens + max_output_tokens
        try:
            async with self.pool.acquire() as connection, connection.transaction():
                row = await connection.fetchrow(
                    "SELECT * FROM assistant_budget WHERE budget_key='deployment' FOR UPDATE"
                )
                if not self._matches(row) or row["blocked"]:
                    return False
                if await connection.fetchval(
                    "SELECT 1 FROM assistant_reservations WHERE reservation_id=$1", reservation_id
                ):
                    # Reusing an ID must not authorize a second paid request.
                    return False
                if (
                    row["charged_amount"] + row["held_amount"] + amount > row["limit_amount"]
                    or row["charged_tokens"] + row["held_tokens"] + tokens > row["token_limit"]
                ):
                    return False
                await connection.execute(
                    """INSERT INTO assistant_reservations
                    (reservation_id, budget_key, model_uri, currency, tariff_id,
                     max_input_tokens, max_output_tokens, reserved_amount, reserved_tokens)
                    VALUES ($1,'deployment',$2,$3,$4,$5,$6,$7,$8)""",
                    reservation_id,
                    model,
                    self.currency,
                    self.tariff_id,
                    max_input_tokens,
                    max_output_tokens,
                    amount,
                    tokens,
                )
                await connection.execute(
                    """UPDATE assistant_budget SET held_amount=held_amount+$1,
                    held_tokens=held_tokens+$2, updated_at=now()
                    WHERE budget_key='deployment'""",
                    amount,
                    tokens,
                )
                return True
        except Exception:
            # Includes unavailable DB, missing migration and ambiguous commit outcome.
            return False

    async def record_usage(
        self, *, reservation_id: str, input_tokens: int | None, output_tokens: int | None
    ) -> None:
        tariff = _tariff(self.tariff_id, self.currency)
        if tariff is None or not isinstance(reservation_id, str):
            raise BudgetUnavailable()
        try:
            async with self.pool.acquire() as connection, connection.transaction():
                row = await connection.fetchrow(
                    "SELECT * FROM assistant_budget WHERE budget_key='deployment' FOR UPDATE"
                )
                if not self._matches(row):
                    raise BudgetUnavailable()
                reservation = await connection.fetchrow(
                    "SELECT * FROM assistant_reservations WHERE reservation_id=$1 FOR UPDATE",
                    reservation_id,
                )
                if reservation is None or (
                    reservation["model_uri"] != self.model_uri
                    or reservation["currency"] != self.currency
                    or reservation["tariff_id"] != self.tariff_id
                ):
                    raise BudgetUnavailable()
                if reservation["state"] == "settled":
                    if (
                        reservation["input_tokens"] == input_tokens
                        and reservation["output_tokens"] == output_tokens
                    ):
                        return
                    raise BudgetUnavailable()
                for value in (input_tokens, output_tokens):
                    if value is not None and (
                        not isinstance(value, int) or isinstance(value, bool) or value < 0
                    ):
                        raise BudgetUnavailable()
                if reservation["state"] == "unknown" and (
                    reservation["input_tokens"] is not None
                    and reservation["input_tokens"] != input_tokens
                    or reservation["output_tokens"] is not None
                    and reservation["output_tokens"] != output_tokens
                ):
                    raise BudgetUnavailable()
                if input_tokens is None or output_tokens is None:
                    if reservation["state"] == "held":
                        await connection.execute(
                            """UPDATE assistant_reservations SET state='unknown',
                            input_tokens=$2, output_tokens=$3, updated_at=now()
                            WHERE reservation_id=$1""",
                            reservation_id,
                            input_tokens,
                            output_tokens,
                        )
                    return
                amount = _amount(tariff, input_tokens, output_tokens)
                tokens = input_tokens + output_tokens
                exceeded = bool(
                    input_tokens > reservation["max_input_tokens"]
                    or output_tokens > reservation["max_output_tokens"]
                    or amount > reservation["reserved_amount"]
                    or tokens > reservation["reserved_tokens"]
                )
                await connection.execute(
                    """UPDATE assistant_budget SET
                    held_amount=held_amount-$1, held_tokens=held_tokens-$2,
                    charged_amount=charged_amount+$3, charged_tokens=charged_tokens+$4,
                    blocked=blocked OR $5, updated_at=now()
                    WHERE budget_key='deployment'""",
                    reservation["reserved_amount"],
                    reservation["reserved_tokens"],
                    amount,
                    tokens,
                    exceeded,
                )
                await connection.execute(
                    """UPDATE assistant_reservations SET state='settled',
                    input_tokens=$2, output_tokens=$3, charged_amount=$4,
                    charged_tokens=$5, updated_at=now() WHERE reservation_id=$1""",
                    reservation_id,
                    input_tokens,
                    output_tokens,
                    amount,
                    tokens,
                )
        except BudgetUnavailable:
            raise
        except Exception:
            raise BudgetUnavailable() from None

    async def status(self) -> BudgetSnapshot | None:
        return await read_budget_status(self.pool)


async def read_budget_status(pool: asyncpg.Pool) -> BudgetSnapshot | None:
    """Admin-only aggregate view; never lists request or reservation identifiers."""
    try:
        row = await pool.fetchrow(
            """SELECT b.*, (SELECT count(*) FROM assistant_reservations)
            AS reservation_count FROM assistant_budget b WHERE budget_key='deployment'"""
        )
    except Exception:
        raise BudgetUnavailable() from None
    if row is None:
        return None
    return BudgetSnapshot(
        currency=row["currency"],
        tariff_id=row["tariff_id"],
        model_uri=row["model_uri"],
        limit_amount=row["limit_amount"],
        charged_amount=row["charged_amount"],
        held_amount=row["held_amount"],
        token_limit=row["token_limit"],
        charged_tokens=row["charged_tokens"],
        held_tokens=row["held_tokens"],
        reservation_count=row["reservation_count"],
        blocked=row["blocked"],
    )
