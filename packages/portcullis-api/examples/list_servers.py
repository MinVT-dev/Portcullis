# SPDX-License-Identifier: MIT
"""List every server on a panel with its state and player count.

    AMP_URL=https://panel.example:8443 AMP_USERNAME=bot AMP_PASSWORD=... python examples/list_servers.py
"""

import asyncio
import os

from portcullis_api import AmpClient, Credentials


async def main() -> None:
    creds = Credentials(os.environ["AMP_USERNAME"], os.environ["AMP_PASSWORD"])
    async with AmpClient(os.environ["AMP_URL"], creds) as amp:
        for shard in await amp.shards.get_shards():
            if shard.deleted:
                continue
            state = await amp.shards.get_shards_state(shard.id)
            users = await amp.shards.get_shards_users(shard.id)
            running = "running" if state.is_running else "stopped"
            print(f"{shard.display_name or shard.name}: {running}, {len(users)} player(s)")


if __name__ == "__main__":
    asyncio.run(main())
