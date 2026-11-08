# SPDX-License-Identifier: MIT
"""Print one server's console and player joins as they happen. Ctrl+C to stop.

    python examples/watch_console.py <shard id>
"""

import asyncio
import os
import sys

from portcullis_api import AmpClient, Credentials, watch_shard


async def main(shard_id: str) -> None:
    creds = Credentials(os.environ["AMP_USERNAME"], os.environ["AMP_PASSWORD"])
    async with AmpClient(os.environ["AMP_URL"], creds) as amp:
        async for event in watch_shard(amp, shard_id):
            if event.target == "ConsoleLine":
                line = event.arguments[0]
                print(f"[{line.get('streamId')}] {line.get('text')}")
            elif event.target == "UserJoined":
                print("joined:", event.arguments[1].get("name"))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
