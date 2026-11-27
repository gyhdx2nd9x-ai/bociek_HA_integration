"""Bounded LLM work does not queue fast-path commands."""

import asyncio


class FallbackBusy(Exception):
    pass


class LimitedFallback:
    def __init__(self, fallback, limit=1, timeout=5):
        self.fallback, self.limit, self.timeout = fallback, limit, timeout
        self.active = 0

    async def propose(self, *args):
        if self.active >= self.limit:
            raise FallbackBusy
        self.active += 1
        try:
            async with asyncio.timeout(self.timeout):
                return await self.fallback.propose(*args)
        finally:
            self.active -= 1
