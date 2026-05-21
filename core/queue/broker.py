# core/queue/broker.py
from __future__ import annotations

import asyncio
import contextlib
import heapq
import random
import time
import uuid

from abc import ABC, abstractmethod
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field, replace
from enum import Enum
from types import MappingProxyType
from typing import (
    Any,
    AsyncIterator,
    Callable,
    Dict,
    List,
    Mapping,
    Optional,
    Tuple,
)
from core.runtime.orchestrator import RuntimeOrchestrator
# ============================================================
# CONSTANTS
# ============================================================

MONOTONIC = time.monotonic
UUID4 = uuid.uuid4

MAX_RETRY_DELAY = 300.0
DEFAULT_PRIORITY = 100
HEARTBEAT_GRACE_MULTIPLIER = 2.0

# ============================================================
# ENUMS
# ============================================================


class BrokerHealth(str, Enum):
    STARTING = "starting"
    RUNNING = "running"
    DEGRADED = "degraded"
    STOPPING = "stopping"
    DRAINING = "draining"
    STOPPED = "stopped"


class MessageLifecycle(str, Enum):
    QUEUED = "queued"
    LEASED = "leased"
    RETRY_WAIT = "retry_wait"
    COMPLETED = "completed"
    DEAD_LETTERED = "dead_lettered"
    EXPIRED = "expired"


class PublishPolicy(str, Enum):
    BLOCK = "block"
    REJECT = "reject"
    DROP = "drop"


# ============================================================
# HELPERS
# ============================================================


def _freeze_mapping(
    value: Optional[Mapping[str, Any]],
) -> Mapping[str, Any]:

    if value is None:
        return MappingProxyType({})

    return MappingProxyType(dict(value))


# ============================================================
# MESSAGE
# ============================================================


@dataclass(slots=True, frozen=True)
class Message:

    topic: str
    type: str

    payload: Mapping[str, Any]

    id: str = field(default_factory=lambda: str(UUID4()))
    trace_id: str = field(default_factory=lambda: str(UUID4()))

    retries: int = 0
    max_retries: int = 5

    priority: int = DEFAULT_PRIORITY

    created_at: float = field(default_factory=MONOTONIC)

    ttl_seconds: Optional[float] = None

    retry_history: Tuple[float, ...] = field(default_factory=tuple)

    metadata: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )

    idempotency_key: Optional[str] = None

    lineage: Tuple[str, ...] = field(default_factory=tuple)

    original_topic: Optional[str] = None

    dead_letter_reason: Optional[str] = None
    dead_lettered_at: Optional[float] = None

    def __post_init__(self) -> None:

        object.__setattr__(
            self,
            "payload",
            _freeze_mapping(self.payload),
        )

        object.__setattr__(
            self,
            "metadata",
            _freeze_mapping(self.metadata),
        )

    def copy_with(self, **changes: Any) -> "Message":
        return replace(self, **changes)

    def expired(self) -> bool:

        if self.ttl_seconds is None:
            return False

        return (
            MONOTONIC() - self.created_at
        ) >= self.ttl_seconds

    def next_retry(self) -> "Message":

        return self.copy_with(
            retries=self.retries + 1,
            retry_history=(
                *self.retry_history,
                MONOTONIC(),
            ),
        )


# ============================================================
# DELIVERY LEASE
# ============================================================


@dataclass(slots=True)
class DeliveryLease:

    lease_id: str

    lease_epoch: int

    message_id: str

    topic: str

    consumer_id: str

    leased_at: float

    expires_at: float

    heartbeat_at: float

    acknowledged: bool = False

    completed: bool = False


# ============================================================
# MESSAGE STATE
# ============================================================


@dataclass(slots=True)
class MessageState:

    message: Message

    state: MessageLifecycle

    updated_at: float

    sequence: int

    retry_generation: int = 0

    lease_epoch: int = 0

    active_lease_id: Optional[str] = None

    consumer_id: Optional[str] = None

    delivery_count: int = 0

    last_error: Optional[str] = None


# ============================================================
# COMPLETED WINDOW
# ============================================================


class CompletedWindow:

    def __init__(
        self,
        *,
        ttl_seconds: float = 3600,
        max_size: int = 500_000,
    ):

        self._ttl = ttl_seconds
        self._max_size = max_size

        self._store: OrderedDict[
            str,
            float,
        ] = OrderedDict()

        self._lock = asyncio.Lock()

    async def check_and_put(
        self,
        key: str,
    ) -> bool:

        now = MONOTONIC()

        async with self._lock:

            ts = self._store.get(key)

            if ts is not None:

                if now - ts <= self._ttl:
                    return False

                self._store.pop(key, None)

            self._store[key] = now

            self._store.move_to_end(key)

            while len(self._store) > self._max_size:
                self._store.popitem(last=False)

            return True

    async def cleanup(self) -> None:

        now = MONOTONIC()

        async with self._lock:

            expired = [
                key
                for key, ts in self._store.items()
                if now - ts > self._ttl
            ]

            for key in expired:
                self._store.pop(key, None)


# ============================================================
# PRIORITY QUEUE
# ============================================================


class PriorityMessageQueue:

    def __init__(
        self,
        *,
        maxsize: int,
    ):

        self._heap: List[
            Tuple[int, int, str]
        ] = []

        self._maxsize = maxsize

        self._event = asyncio.Event()

        self._lock = asyncio.Lock()

    async def put(
        self,
        *,
        priority: int,
        sequence: int,
        message_id: str,
        policy: PublishPolicy,
    ) -> bool:

        async with self._lock:

            if len(self._heap) >= self._maxsize:

                if policy == PublishPolicy.DROP:
                    return False

                if policy == PublishPolicy.REJECT:
                    raise asyncio.QueueFull

                while len(self._heap) >= self._maxsize:
                    await asyncio.sleep(0.001)

            heapq.heappush(
                self._heap,
                (
                    priority,
                    sequence,
                    message_id,
                ),
            )

            self._event.set()

            return True

    async def get(self) -> str:

        while True:

            async with self._lock:

                if self._heap:

                    _, _, message_id = heapq.heappop(
                        self._heap
                    )

                    return message_id

                self._event.clear()

            await self._event.wait()

    async def qsize(self) -> int:

        async with self._lock:
            return len(self._heap)


# ============================================================
# TOPIC CHANNEL
# ============================================================


class TopicChannel:

    __slots__ = (
        "queue",
        "lock",
        "message_state",
        "stats_snapshot",
    )

    def __init__(
        self,
        *,
        max_queue_size: int,
    ):

        self.queue = PriorityMessageQueue(
            maxsize=max_queue_size,
        )

        self.lock = asyncio.Lock()

        self.message_state: Dict[
            str,
            MessageState,
        ] = {}

        self.stats_snapshot: Dict[
            str,
            Any,
        ] = {}


# ============================================================
# BROKER CONTRACT
# ============================================================


class Broker(ABC):

    @abstractmethod
    async def publish(
        self,
        topic: str,
        message: Message,
    ) -> bool:
        ...

    @abstractmethod
    async def consume(
        self,
        topic: str,
        *,
        consumer_id: Optional[str] = None,
    ) -> AsyncIterator[
        Tuple[Message, DeliveryLease]
    ]:
        ...

    @abstractmethod
    async def ack(
        self,
        message_id: str,
        lease_id: str,
    ) -> bool:
        ...

    @abstractmethod
    async def nack(
        self,
        message_id: str,
        lease_id: str,
        *,
        reason: Optional[str] = None,
        requeue: bool = True,
    ) -> bool:
        ...


# ============================================================
# IN-MEMORY BROKER
# ============================================================


class InMemoryBroker(Broker):

    def __init__(
        self,
        *,
        max_queue_size: int = 100_000,
        visibility_timeout: float = 30.0,
        reclaim_interval: float = 1.0,
        retry_base_delay: float = 2.0,
        completed_ttl: float = 3600,
        max_inflight: int = 250_000,
        cleanup_interval: float = 60.0,
        enable_dlq: bool = True,
        publish_policy: PublishPolicy = PublishPolicy.REJECT,
        telemetry: Optional[Any] = None,
    ):

        self._max_queue_size = max_queue_size
        self.orchestrator = orchestrator
        self._visibility_timeout = visibility_timeout

        self._reclaim_interval = reclaim_interval

        self._retry_base_delay = retry_base_delay

        self._max_inflight = max_inflight

        self._cleanup_interval = cleanup_interval

        self._enable_dlq = enable_dlq

        self._publish_policy = publish_policy

        self._telemetry = telemetry

        self._health = BrokerHealth.STOPPED

        self._running = False

        self._accepting_publishes = True

        self._sequence = 0

        self._topics: Dict[
            str,
            TopicChannel,
        ] = {}

        self._global_lock = asyncio.Lock()

        self._retry_lock = asyncio.Lock()

        self._lease_index: Dict[
            str,
            DeliveryLease,
        ] = {}

        self._message_topic_index: Dict[
            str,
            str,
        ] = {}

        self._consumer_heartbeats: Dict[
            str,
            float,
        ] = {}

        self._idempotency_window = (
            CompletedWindow(
                ttl_seconds=completed_ttl,
            )
        )

        self._completed_window = (
            CompletedWindow(
                ttl_seconds=completed_ttl,
            )
        )

        self._retry_heap: List[
            Tuple[
                float,
                int,
                str,
                str,
                int,
            ]
        ] = []

        self._retry_event = asyncio.Event()

        self._inflight_semaphore = (
            asyncio.Semaphore(
                max_inflight
            )
        )

        self._tasks: List[
            asyncio.Task
        ] = []

    # ========================================================
    # LIFECYCLE
    # ========================================================

    async def start(self) -> None:

        if self._running:
            return

        self._health = BrokerHealth.STARTING

        self._running = True

        self._accepting_publishes = True

        self._tasks = [
            asyncio.create_task(
                self._lease_reclaim_loop(),
                name="broker-reclaim",
            ),
            asyncio.create_task(
                self._retry_scheduler_loop(),
                name="broker-retry",
            ),
            asyncio.create_task(
                self._cleanup_loop(),
                name="broker-cleanup",
            ),
            asyncio.create_task(
                self._consumer_monitor_loop(),
                name="broker-heartbeats",
            ),
        ]

        self._health = BrokerHealth.RUNNING

    async def stop(self) -> None:

        if not self._running:
            return

        self._health = BrokerHealth.DRAINING

        self._accepting_publishes = False

        deadline = MONOTONIC() + 30.0

        while (
            self._lease_index
            and MONOTONIC() < deadline
        ):
            await asyncio.sleep(0.1)

        self._health = BrokerHealth.STOPPING

        self._running = False

        for task in self._tasks:
            task.cancel()

        for task in self._tasks:
            with contextlib.suppress(
                asyncio.CancelledError
            ):
                await task

        self._tasks.clear()

        self._health = BrokerHealth.STOPPED

    # ========================================================
    # TOPICS
    # ========================================================

    async def _get_topic(
        self,
        topic: str,
    ) -> TopicChannel:

        existing = self._topics.get(topic)

        if existing:
            return existing

        async with self._global_lock:

            existing = self._topics.get(topic)

            if existing:
                return existing

            channel = TopicChannel(
                max_queue_size=self._max_queue_size,
            )

            self._topics[topic] = channel

            return channel

    # ========================================================
    # SEQUENCE
    # ========================================================

    def _next_sequence(self) -> int:

        self._sequence += 1

        return self._sequence

    # ========================================================
    # PUBLISH
    # ========================================================

    async def publish(
        self,
        topic: str,
        message: Message,
    ) -> bool:

        if not self._running:
            return False

        if not self._accepting_publishes:
            return False

        if message.expired():
            return False

        if message.idempotency_key:

            inserted = (
                await self._idempotency_window.check_and_put(
                    message.idempotency_key
                )
            )

            if not inserted:
                return False

        channel = await self._get_topic(topic)

        sequence = self._next_sequence()

        async with channel.lock:

            if message.id in channel.message_state:
                return False

            state = MessageState(
                message=message,
                state=MessageLifecycle.QUEUED,
                updated_at=MONOTONIC(),
                sequence=sequence,
            )

            channel.message_state[
                message.id
            ] = state

        self._message_topic_index[
            message.id
        ] = topic

        queued = await channel.queue.put(
            priority=message.priority,
            sequence=sequence,
            message_id=message.id,
            policy=self._publish_policy,
        )

        return queued

    # ========================================================
    # CONSUME
    # ========================================================

    async def consume(
        self,
        topic: str,
        *,
        consumer_id: Optional[str] = None,
    ) -> AsyncIterator[
        Tuple[Message, DeliveryLease]
    ]:

        consumer_id = (
            consumer_id or str(UUID4())
        )

        self._consumer_heartbeats[
            consumer_id
        ] = MONOTONIC()

        channel = await self._get_topic(topic)

        while self._running:

            message_id = await channel.queue.get()

            async with channel.lock:

                state = (
                    channel.message_state.get(
                        message_id
                    )
                )

                if state is None:
                    continue

                message = state.message

                if message.expired():

                    state.state = (
                        MessageLifecycle.EXPIRED
                    )

                    continue

                if state.state not in (
                    MessageLifecycle.QUEUED,
                    MessageLifecycle.RETRY_WAIT,
                ):
                    continue

                await self._inflight_semaphore.acquire()

                now = MONOTONIC()

                state.lease_epoch += 1

                lease = DeliveryLease(
                    lease_id=str(UUID4()),
                    lease_epoch=state.lease_epoch,
                    message_id=message.id,
                    topic=topic,
                    consumer_id=consumer_id,
                    leased_at=now,
                    expires_at=(
                        now
                        + self._visibility_timeout
                    ),
                    heartbeat_at=now,
                )

                state.state = (
                    MessageLifecycle.LEASED
                )

                state.updated_at = now

                state.consumer_id = consumer_id

                state.active_lease_id = (
                    lease.lease_id
                )

                state.delivery_count += 1

                self._lease_index[
                    lease.lease_id
                ] = lease

            yield message, lease

    # ========================================================
    # HEARTBEAT
    # ========================================================

    async def heartbeat(
        self,
        lease_id: str,
    ) -> bool:

        lease = self._lease_index.get(
            lease_id
        )

        if lease is None:
            return False

        now = MONOTONIC()

        lease.heartbeat_at = now

        lease.expires_at = (
            now + self._visibility_timeout
        )

        self._consumer_heartbeats[
            lease.consumer_id
        ] = now

        return True

    # ========================================================
    # ACK
    # ========================================================

    async def ack(
        self,
        message_id: str,
        lease_id: str,
    ) -> bool:

        lease = self._lease_index.get(
            lease_id
        )

        if lease is None:
            return False

        if lease.message_id != message_id:
            return False

        topic = lease.topic

        channel = self._topics.get(topic)

        if channel is None:
            return False

        async with channel.lock:

            state = (
                channel.message_state.get(
                    message_id
                )
            )

            if state is None:
                return False

            if (
                state.active_lease_id
                != lease_id
            ):
                return False

            state.state = (
                MessageLifecycle.COMPLETED
            )

            state.updated_at = MONOTONIC()

            self._lease_index.pop(
                lease_id,
                None,
            )

        self._inflight_semaphore.release()

        return True

    # ========================================================
    # NACK
    # ========================================================

    async def nack(
        self,
        message_id: str,
        lease_id: str,
        *,
        reason: Optional[str] = None,
        requeue: bool = True,
    ) -> bool:

        lease = self._lease_index.get(
            lease_id
        )

        if lease is None:
            return False

        topic = lease.topic

        channel = self._topics.get(topic)

        if channel is None:
            return False

        async with channel.lock:

            state = (
                channel.message_state.get(
                    message_id
                )
            )

            if state is None:
                return False

            if (
                state.active_lease_id
                != lease_id
            ):
                return False

            self._lease_index.pop(
                lease_id,
                None,
            )

            self._inflight_semaphore.release()

            if not requeue:

                state.state = (
                    MessageLifecycle.COMPLETED
                )

                return True

            next_message = (
                state.message.next_retry()
            )

            if (
                next_message.retries
                >= next_message.max_retries
            ):

                await self._send_to_dlq(
                    next_message,
                    reason=(
                        reason
                        or "max_retries"
                    ),
                )

                return True

            state.retry_generation += 1

            generation = (
                state.retry_generation
            )

            state.message = next_message

            state.state = (
                MessageLifecycle.RETRY_WAIT
            )

            state.updated_at = MONOTONIC()

            delay = self._compute_retry_delay(
                next_message.retries
            )

            async with self._retry_lock:

                heapq.heappush(
                    self._retry_heap,
                    (
                        MONOTONIC() + delay,
                        state.sequence,
                        topic,
                        next_message.id,
                        generation,
                    ),
                )

                self._retry_event.set()

        return True

    # ========================================================
    # RETRY DELAY
    # ========================================================

    def _compute_retry_delay(
        self,
        retry: int,
    ) -> float:

        base = (
            self._retry_base_delay
            * (2 ** retry)
        )

        jitter = random.uniform(
            0.0,
            base * 0.20,
        )

        return min(
            base + jitter,
            MAX_RETRY_DELAY,
        )

    # ========================================================
    # RETRY LOOP
    # ========================================================

    async def _retry_scheduler_loop(
        self,
    ) -> None:

        while self._running:

            try:

                async with self._retry_lock:

                    if not self._retry_heap:

                        self._retry_event.clear()

                        wait_required = True

                    else:

                        wait_required = False

                        wake_at, _, topic, message_id, generation = (
                            self._retry_heap[0]
                        )

                if wait_required:

                    await self._retry_event.wait()

                    continue

                now = MONOTONIC()

                if wake_at > now:

                    try:

                        await asyncio.wait_for(
                            self._retry_event.wait(),
                            timeout=(
                                wake_at - now
                            ),
                        )

                    except asyncio.TimeoutError:
                        pass

                    self._retry_event.clear()

                    continue

                async with self._retry_lock:

                    if not self._retry_heap:
                        continue

                    (
                        _,
                        _,
                        topic,
                        message_id,
                        generation,
                    ) = heapq.heappop(
                        self._retry_heap
                    )

                channel = (
                    await self._get_topic(
                        topic
                    )
                )

                async with channel.lock:

                    state = (
                        channel.message_state.get(
                            message_id
                        )
                    )

                    if state is None:
                        continue

                    if (
                        state.retry_generation
                        != generation
                    ):
                        continue

                    if state.state != (
                        MessageLifecycle.RETRY_WAIT
                    ):
                        continue

                    state.state = (
                        MessageLifecycle.QUEUED
                    )

                    state.updated_at = MONOTONIC()

                    message = state.message

                await channel.queue.put(
                    priority=message.priority,
                    sequence=state.sequence,
                    message_id=message.id,
                    policy=self._publish_policy,
                )

                self._health = (
                    BrokerHealth.RUNNING
                )

            except asyncio.CancelledError:
                raise

            except Exception:

                self._health = (
                    BrokerHealth.DEGRADED
                )

                await asyncio.sleep(1)

    # ========================================================
    # RECLAIM LOOP
    # ========================================================

    async def _lease_reclaim_loop(
        self,
    ) -> None:

        while self._running:

            try:

                now = MONOTONIC()

                expired: List[
                    DeliveryLease
                ] = []

                for lease in list(
                    self._lease_index.values()
                ):

                    if now >= lease.expires_at:
                        expired.append(lease)

                for lease in expired:

                    topic = lease.topic

                    channel = (
                        self._topics.get(topic)
                    )

                    if channel is None:
                        continue

                    async with channel.lock:

                        state = (
                            channel.message_state.get(
                                lease.message_id
                            )
                        )

                        if state is None:
                            continue

                        if (
                            state.active_lease_id
                            != lease.lease_id
                        ):
                            continue

                        self._lease_index.pop(
                            lease.lease_id,
                            None,
                        )

                        self._inflight_semaphore.release()

                        next_message = (
                            state.message.next_retry()
                        )

                        if (
                            next_message.retries
                            >= next_message.max_retries
                        ):

                            await self._send_to_dlq(
                                next_message,
                                reason="lease_expired",
                            )

                            continue

                        state.retry_generation += 1

                        state.message = (
                            next_message
                        )

                        state.state = (
                            MessageLifecycle.QUEUED
                        )

                        state.updated_at = now

                        generation = (
                            state.retry_generation
                        )

                    await channel.queue.put(
                        priority=next_message.priority,
                        sequence=state.sequence,
                        message_id=next_message.id,
                        policy=self._publish_policy,
                    )

                self._health = (
                    BrokerHealth.RUNNING
                )

                await asyncio.sleep(
                    self._reclaim_interval
                )

            except asyncio.CancelledError:
                raise

            except Exception:

                self._health = (
                    BrokerHealth.DEGRADED
                )

                await asyncio.sleep(1)

    # ========================================================
    # DLQ
    # ========================================================

    async def _send_to_dlq(
        self,
        message: Message,
        *,
        reason: str,
    ) -> None:

        if not self._enable_dlq:
            return

        dlq_topic = (
            f"{message.topic}.DLQ"
        )

        dlq_message = message.copy_with(
            topic=dlq_topic,
            original_topic=message.topic,
            dead_letter_reason=reason,
            dead_lettered_at=MONOTONIC(),
            lineage=(
                *message.lineage,
                message.topic,
            ),
        )

        await self.publish(
            dlq_topic,
            dlq_message,
        )

    # ========================================================
    # HEARTBEAT MONITOR
    # ========================================================

    async def _consumer_monitor_loop(
        self,
    ) -> None:

        while self._running:

            try:

                now = MONOTONIC()

                expired_consumers = []

                for (
                    consumer_id,
                    ts,
                ) in list(
                    self._consumer_heartbeats.items()
                ):

                    if (
                        now - ts
                    ) > (
                        self._visibility_timeout
                        * HEARTBEAT_GRACE_MULTIPLIER
                    ):
                        expired_consumers.append(
                            consumer_id
                        )

                for consumer_id in expired_consumers:
                    self._consumer_heartbeats.pop(
                        consumer_id,
                        None,
                    )

                await asyncio.sleep(5)

            except asyncio.CancelledError:
                raise

            except Exception:
                await asyncio.sleep(1)

    # ========================================================
    # CLEANUP LOOP
    # ========================================================

    async def _cleanup_loop(
        self,
    ) -> None:

        while self._running:

            try:

                await self._completed_window.cleanup()

                await self._idempotency_window.cleanup()

                for channel in (
                    self._topics.values()
                ):

                    async with channel.lock:

                        removable = []

                        for (
                            message_id,
                            state,
                        ) in (
                            channel.message_state.items()
                        ):

                            if state.state not in (
                                MessageLifecycle.COMPLETED,
                                MessageLifecycle.EXPIRED,
                                MessageLifecycle.DEAD_LETTERED,
                            ):
                                continue

                            if (
                                MONOTONIC()
                                - state.updated_at
                            ) > 3600:
                                removable.append(
                                    message_id
                                )

                        for key in removable:

                            channel.message_state.pop(
                                key,
                                None,
                            )

                            self._message_topic_index.pop(
                                key,
                                None,
                            )

                self._health = (
                    BrokerHealth.RUNNING
                )

                await asyncio.sleep(
                    self._cleanup_interval
                )

            except asyncio.CancelledError:
                raise

            except Exception:

                self._health = (
                    BrokerHealth.DEGRADED
                )

                await asyncio.sleep(5)

    # ========================================================
    # STATS SNAPSHOT
    # ========================================================

    async def stats(
        self,
    ) -> Dict[str, Any]:

        topics = {}

        for (
            topic,
            channel,
        ) in self._topics.items():

            lifecycle_counts = defaultdict(
                int
            )

            async with channel.lock:

                for state in (
                    channel.message_state.values()
                ):
                    lifecycle_counts[
                        state.state.value
                    ] += 1

                queue_size = (
                    await channel.queue.qsize()
                )

            topics[topic] = {
                "queued": queue_size,
                "states": dict(
                    lifecycle_counts
                ),
            }

        return {
            "health": self._health.value,
            "running": self._running,
            "topics": topics,
            "inflight": len(
                self._lease_index
            ),
            "retry_heap": len(
                self._retry_heap
            ),
            "consumers": len(
                self._consumer_heartbeats
            ),
        }


# ============================================================
# FACTORY
# ============================================================


class BrokerFactory:

    _registry: Dict[
        str,
        Callable[..., Broker],
    ] = {}

    @classmethod
    def register(
        cls,
        name: str,
        broker_cls: Callable[..., Broker],
    ) -> None:

        cls._registry[name] = broker_cls

    @classmethod
    def create(
        cls,
        name: str,
        **kwargs: Any,
    ) -> Broker:

        broker_cls = cls._registry.get(name)

        if broker_cls is None:
            raise ValueError(
                f"Broker '{name}' not registered"
            )

        return broker_cls(**kwargs)


BrokerFactory.register(
    "memory",
    InMemoryBroker,
)
