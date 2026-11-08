# SPDX-License-Identifier: MIT
"""Send one console command to a server, then show the last console lines.

    python examples/send_command.py <shard id> "say Hello from Python"
"""

import asyncio
import os
import sys

from portcullis_api import AmpClient, Credentials, models


async def main(shard_id: str, command: str) -> None:
    creds = Credentials(os.environ["AMP_USERNAME"], os.environ["AMP_PASSWORD"])
    async with AmpClient(os.environ["AMP_URL"], creds) as amp:
        await amp.shards.post_shards_console(shard_id, models.ConsoleInputBody(command=command))
        await asyncio.sleep(1)
        page = await amp.shards.get_shards_console_history(shard_id, limit=10)
        for line in page.lines:
            print(line.text)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2]))
