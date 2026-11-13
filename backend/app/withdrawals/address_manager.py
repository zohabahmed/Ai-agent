"""Destination address & supported network management for withdrawals."""

from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import DestinationAddress

SUPPORTED_NETWORKS: dict[str, Decimal | float] = {
    "TRC20": 1.0,
    "BEP20": 0.3,
    "ERC20": 4.0,
    "POLYGON": 0.2,
    "ARBITRUM": 0.2,
    "SOL": 0.1,
}

_EVM_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
_TRON_RE = re.compile(r"^T[1-9A-HJ-NP-Za-km-z]{33}$")


class AddressManager:
    """Validates destination networks & addresses and prevents sending to unverified addresses."""

    @staticmethod
    def is_network_supported(network: str) -> bool:
        return network.upper() in SUPPORTED_NETWORKS

    @staticmethod
    def get_network_fee(network: str) -> float:
        return float(SUPPORTED_NETWORKS.get(network.upper(), 5.0))

    @staticmethod
    def validate_address_format(address: str, network: str) -> bool:
        net = network.upper()
        if net in {"ERC20", "BEP20", "POLYGON", "ARBITRUM"}:
            return bool(_EVM_RE.match(address))
        if net == "TRC20":
            return bool(_TRON_RE.match(address))
        return 26 <= len(address) <= 128

    @staticmethod
    async def register_address(
        session: AsyncSession,
        *,
        label: str,
        asset: str,
        network: str,
        address: str,
        destination_type: str = "WALLET",
        verified: bool = False,
    ) -> DestinationAddress:
        net = network.upper()
        if not AddressManager.is_network_supported(net):
            raise ValueError(f"Unsupported network: {network}")
        if not AddressManager.validate_address_format(address, net):
            raise ValueError(f"Invalid address format for network {net}")

        dest = DestinationAddress(
            label=label,
            asset=asset.upper(),
            network=net,
            address=address,
            destination_type=destination_type.upper(),
            is_verified=verified,
        )
        session.add(dest)
        await session.flush()
        return dest

    @staticmethod
    async def get_verified_address(
        session: AsyncSession, address: str, network: str
    ) -> DestinationAddress | None:
        stmt = select(DestinationAddress).where(
            DestinationAddress.address == address,
            DestinationAddress.network == network.upper(),
            DestinationAddress.is_verified.is_(True),
        )
        res = await session.execute(stmt)
        return res.scalar_one_or_none()

    @staticmethod
    async def verify_address(session: AsyncSession, destination_id: uuid.UUID) -> DestinationAddress:
        stmt = select(DestinationAddress).where(DestinationAddress.id == destination_id)
        res = await session.execute(stmt)
        dest = res.scalar_one_or_none()
        if dest is None:
            raise ValueError("Destination address not found.")
        dest.is_verified = True
        await session.flush()
        return dest
