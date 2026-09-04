"""The queue itself: an ordered, sliceable, shufflable deque of ``Track``."""

import asyncio
import itertools
import random
from collections import deque


class SongQueue:
    """Async-friendly queue that supports inserting items at the front."""

    def __init__(self):
        self._queue = deque()
        self._event = asyncio.Event()

    async def get(self):
        while not self._queue:
            self._event.clear()
            await self._event.wait()
        item = self._queue.popleft()
        return item

    def put_nowait(self, item):
        self._queue.append(item)
        self._event.set()

    def add_to_front(self, item):
        self._queue.appendleft(item)
        self._event.set()

    def __getitem__(self, item):
        if isinstance(item, slice):
            return list(itertools.islice(self._queue, item.start, item.stop, item.step))
        return self._queue[item]

    def __iter__(self):
        return self._queue.__iter__()

    def __len__(self):
        return len(self._queue)

    def clear(self):
        self._queue.clear()

    def shuffle(self):
        # Shuffle a list, not the deque: random.shuffle does O(n) random access, which
        # is O(n) per element on a deque and so O(n^2) overall -- noticeable on a
        # few hundred tracks.
        items = list(self._queue)
        random.shuffle(items)
        self._queue = deque(items)

    def remove(self, index: int):
        del self._queue[index]

    def move(self, from_index: int, to_index: int):
        if from_index < 0 or from_index >= len(self._queue):
            raise IndexError("Invalid source index")
        if to_index < 0 or to_index >= len(self._queue):
            raise IndexError("Invalid destination index")
        item = self._queue[from_index]
        del self._queue[from_index]
        self._queue.insert(to_index, item)
