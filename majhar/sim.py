"""Simulated ticket platform with payments, seats, confirmations, refunds and nine failure types."""

from __future__ import annotations

import random
from collections import Counter, deque
from dataclasses import dataclass, field

FAULT_IDS = [
    "hold_ttl_bug",
    "seat_assign_timeout",
    "duplicate_sale",
    "confirmation_worker_down",
    "refund_worker_failing",
    "ticket_render_bug",
    "payment_webhook_drop",
    "seat_sync_corruption",
    "queue_overflow_drop",
]

HOLD, PAID_NO_SEAT, SEATED, CONFIRMED, REFUND_REQUESTED, REFUNDED, PAYMENT_UNRECORDED = (
    "HOLD",
    "PAID_NO_SEAT",
    "SEATED",
    "CONFIRMED",
    "REFUND_REQUESTED",
    "REFUNDED",
    "PAYMENT_UNRECORDED",
)

PRICES = {"A": 450, "B": 300, "C": 150}
HOLD_TTL_OK = 10
HOLD_TTL_BUG = 2
HOT_POOL = 12
HOT_SHARE = 0.85
MSG_TTL = 5
WORKER_CAPACITY = 100
GRACE = 3
REFUND_SLA = 5
QUEUE_RATE_CAP = 20
PAYMENT_DROP_PCT = 50
SYNC_EVERY = 3
LOG_CAP = 6000


@dataclass
class Seat:
    id: str
    section: str
    status: str = "free"
    holder: int | None = None
    hold_expires: int = 0
    sold_to: list[int] = field(default_factory=list)


@dataclass
class Order:
    id: int
    customer: str
    seat: str
    section: str
    amount: int
    lang: str
    pay_at: int
    status: str = HOLD
    paid_at: int | None = None
    seated_at: int | None = None
    confirmed_at: int | None = None
    delivered: bool = False
    refund_at: int | None = None
    reason: str = ""
    gate_ok: bool = True
    corrupted_at: int | None = None


class Sim:
    def __init__(self, seed: int = 1, arrivals: int = 6, seats_per_section: int = 200, start_hour: int = 20):
        self.seed = seed
        self.arrivals = arrivals
        self.start_minutes = start_hour * 60
        self.t = 0
        self.seats = {f"{sec}{i}": Seat(f"{sec}{i}", sec) for sec in "ABC" for i in range(1, seats_per_section + 1)}
        self.orders: dict[int, Order] = {}
        self.paid: list[int] = []
        self.next_id = 8000
        self.queue: deque[tuple[int, int]] = deque()
        self.faults: set[str] = set()
        self.effects: set[str] = set()
        self.hold_ttl = HOLD_TTL_OK
        self.logs: list[str] = []
        self.changes: list[str] = [f"{self.clock()} deploy v2.3.0 (weekly release train)"]
        self.window: deque[Counter] = deque(maxlen=3)
        self.timeline: list[dict] = []
        self.refund_batch = 0
        self.refunds_requested = 0
        self.unrecorded: list[int] = []
        self._enq_tick = self._dropped_tick = 0
        self._in_tick = False

    def clock(self) -> str:
        m = self.start_minutes + self.t
        return f"{(m // 60) % 24:02d}:{m % 60:02d}"

    def log(self, level: str, comp: str, msg: str) -> None:
        self.logs.append(f"{self.clock()} {level:<5} {comp}: {msg}")
        if len(self.logs) > LOG_CAP:
            del self.logs[: len(self.logs) - LOG_CAP]

    def _ws(self, key: str) -> int:
        return sum(w[key] for w in self.window)

    def inject(self, fault: str) -> None:
        if fault not in FAULT_IDS:
            raise ValueError(f"unknown fault {fault}")
        if fault == "hold_ttl_bug":
            self.hold_ttl = HOLD_TTL_BUG
            self.changes.append(f"{self.clock()} deploy v2.3.1 (hold-service: timer units refactor)")
        elif fault == "duplicate_sale":
            self.faults.add(fault)
            self.changes.append(f"{self.clock()} db migration 0042 (seat_assignments: drop unique index on seat_id)")
        elif fault == "ticket_render_bug":
            self.faults.add(fault)
            self.changes.append(f"{self.clock()} deploy ticket-template v7 (RTL layout update)")
        elif fault == "confirmation_worker_down":
            self.faults.add(fault)
            self.log(
                "ERROR", "confirm-worker", "unhandled exception in consume loop (Timeout), worker stopped consuming"
            )
        elif fault == "payment_webhook_drop":
            self.faults.add(fault)
            self.changes.append(f"{self.clock()} deploy payment-webhook v1.9 (batched processing + new dedupe cache)")
        elif fault == "seat_sync_corruption":
            self.faults.add(fault)
            self.changes.append(f"{self.clock()} deploy seat-sync v3.1 (incremental delta sync, new row-id mapping)")
        elif fault == "queue_overflow_drop":
            self.faults.add(fault)
            self.changes.append(
                f"{self.clock()} config confirm-queue: publish_limit_per_min={QUEUE_RATE_CAP} policy=drop-new (cost saving)"
            )
        else:
            self.faults.add(fault)

    def active_faults(self) -> set[str]:
        out = set(self.faults)
        if self.hold_ttl < 5:
            out.add("hold_ttl_bug")
        return out

    def unresolved_faults(self) -> set[str]:
        """Active faults that have actually affected customers (latent ones can't be seen)."""
        return self.active_faults() & self.effects

    def tick(self) -> None:
        self.t += 1
        st: Counter = Counter()
        self._enq_tick = self._dropped_tick = 0
        self._in_tick = True
        self._arrivals(st)
        self._expire_holds(st)
        self._payments(st)
        st["queue_dropped"] += self._dropped_tick
        self._in_tick = False
        self._confirm_worker(st)
        self._sync_job(st)
        self._refund_worker(st)
        self.window.append(st)
        self._summary(st)
        s = self.stranded()
        self.timeline.append(
            {
                "t": self.t,
                "time": self.clock(),
                "stranded": len(s),
                "refunds_pending": self._refunds_pending(),
                "queue": len(self.queue),
                "healthy": self.truth_ok(s),
            }
        )

    def _arrivals(self, st: Counter) -> None:
        rng = random.Random(self.seed * 100_003 + self.t)
        n = max(0, round(self.arrivals * rng.uniform(0.7, 1.3)))
        free = [s.id for s in self.seats.values() if s.status == "free"]
        for _ in range(n):
            if not free:
                break
            pool = free[:HOT_POOL] if rng.random() < HOT_SHARE else free
            sid = rng.choice(pool)
            free.remove(sid)
            seat = self.seats[sid]
            oid = self.next_id
            self.next_id += 1
            lat = rng.choice([1, 1, 2, 2, 3, 4])
            lang = "ar" if rng.random() < 0.6 else "en"
            self.orders[oid] = Order(
                oid, f"cust-{oid}", sid, seat.section, PRICES[seat.section], lang, pay_at=self.t + lat
            )
            seat.status, seat.holder, seat.hold_expires = "held", oid, self.t + self.hold_ttl
            st["arrivals"] += 1

    def _expire_holds(self, st: Counter) -> None:
        expired = [s for s in self.seats.values() if s.status == "held" and s.hold_expires <= self.t]
        unpaid, example = 0, None
        for s in expired:
            o = self.orders.get(s.holder)
            s.status, s.holder = "free", None
            if o is not None and o.status == HOLD:
                unpaid += 1
                example = example or s.id
        if unpaid:
            st["expired"] += unpaid
            if self.hold_ttl < 5:
                self.effects.add("hold_ttl_bug")
            self.log(
                "WARN",
                "hold-service",
                f"released {unpaid} holds after ttl={self.hold_ttl}m, payment not yet complete "
                f"(e.g. seat={example})",
            )

    def _payments(self, st: Counter) -> None:
        due = sorted((o for o in self.orders.values() if o.status == HOLD and o.pay_at <= self.t), key=lambda o: o.id)
        for o in due:
            o.paid_at = self.t
            st["captured"] += 1
            if "payment_webhook_drop" in self.faults and (o.id * 2246822519) % 100 < PAYMENT_DROP_PCT:
                o.status, o.reason = PAYMENT_UNRECORDED, "payment_unrecorded"
                self.unrecorded.append(o.id)
                self.effects.add("payment_webhook_drop")
                continue
            self.paid.append(o.id)
            st["paid"] += 1
            self._assign(o, st)

    def _assign(self, o: Order, st: Counter) -> None:
        if "seat_assign_timeout" in self.faults and (o.id * 2654435761) % 100 < 60:
            o.status, o.reason = PAID_NO_SEAT, "assign_timeout"
            st["assign_err"] += 1
            self.effects.add("seat_assign_timeout")
            self.log(
                "ERROR", "seat-assigner", f"timeout after 5000ms order={o.id} seat={o.seat} (payment already captured)"
            )
            return
        seat = self.seats[o.seat]
        if (seat.status == "held" and seat.holder == o.id) or seat.status == "free":
            seat.status, seat.holder, seat.sold_to = "sold", None, [o.id]
            self._seated(o)
            return
        holder = seat.holder if seat.status == "held" else (seat.sold_to[0] if seat.sold_to else None)
        st["conflicts"] += 1
        self.log(
            "WARN",
            "seat-assigner",
            f"seat={o.seat} not held by order={o.id} (state={seat.status}, other_order={holder})",
        )
        if "duplicate_sale" in self.faults:
            seat.status, seat.holder = "sold", None
            seat.sold_to.append(o.id)
            self.effects.add("duplicate_sale")
            self._seated(o)
        else:
            o.status, o.reason = PAID_NO_SEAT, "seat_taken"
            st["assign_err"] += 1
            self.log("ERROR", "seat-assigner", f"seat conflict, order={o.id} left unseated (payment already captured)")

    def _seated(self, o: Order) -> None:
        o.status, o.seated_at, o.reason = SEATED, self.t, ""
        if self._in_tick and "queue_overflow_drop" in self.faults and self._enq_tick >= QUEUE_RATE_CAP:
            self._dropped_tick += 1
            self.effects.add("queue_overflow_drop")
            return
        self._enq_tick += 1
        self.queue.append((o.id, self.t))

    def _confirm_worker(self, st: Counter) -> None:
        if "confirmation_worker_down" in self.faults:
            if self.queue:
                self.effects.add("confirmation_worker_down")
                oldest = self.t - self.queue[0][1]
                self.log("WARN", "confirm-queue", f"depth={len(self.queue)} oldest={oldest}m active_consumers=0")
            return
        render_logged = 0
        for _ in range(min(len(self.queue), WORKER_CAPACITY)):
            oid, _enq = self.queue.popleft()
            o = self.orders[oid]
            if o.status != SEATED:
                continue
            o.status, o.confirmed_at = CONFIRMED, self.t
            if "ticket_render_bug" in self.faults and o.lang == "ar":
                o.delivered = False
                st["render_err"] += 1
                self.effects.add("ticket_render_bug")
                if render_logged < 2:
                    render_logged += 1
                    self.log("ERROR", "ticket-renderer", f"render failed order={o.id} locale=ar-SA template=v7")
            else:
                o.delivered = True
            st["sent"] += 1

    def _sync_job(self, st: Counter) -> None:
        """Seat-sync copies confirmed tickets to the gate system."""
        confirmed = [o for o in self.orders.values() if o.status == CONFIRMED]
        if "seat_sync_corruption" in self.faults:
            good = [o for o in confirmed if o.gate_ok]
            rng = random.Random(self.seed * 7919 + self.t)
            k = min(len(good), rng.randint(2, 4))
            for o in rng.sample(good, k):
                o.gate_ok, o.corrupted_at = False, self.t
            if k:
                st["sync_corrupt"] += k
                self.effects.add("seat_sync_corruption")
        if self.t % SYNC_EVERY == 0 and confirmed:
            self.log("INFO", "seat-sync", f"run complete: synced={len(confirmed)} records")
            if "seat_sync_corruption" in self.faults and self._corrupted():
                self.log(
                    "WARN", "seat-sync", f"replica checksum drift on {self._corrupted()} rows (below alert threshold)"
                )

    def _refund_worker(self, st: Counter) -> None:
        pending = [o for o in self.orders.values() if o.status == REFUND_REQUESTED]
        if not pending:
            return
        if "refund_worker_failing" in self.faults:
            self.effects.add("refund_worker_failing")
            self.refund_batch += 1
            self.log("INFO", "refund-worker", f"gateway accepted batch GW-{self.refund_batch:04d} items={len(pending)}")
            oldest = max(self.t - o.refund_at for o in pending)
            if oldest >= 3:
                self.log("WARN", "refund-worker", f"settlement callback missing, oldest request {oldest}m")
            return
        settled = 0
        for o in pending:
            if o.refund_at < self.t:
                o.status = REFUNDED
                seat = self.seats.get(o.seat)
                if seat and o.id in seat.sold_to:
                    seat.sold_to.remove(o.id)
                    if not seat.sold_to and seat.status == "sold":
                        seat.status = "free"
                settled += 1
        if settled:
            st["settled"] += settled

    def _summary(self, st: Counter) -> None:
        if st["captured"]:
            self.log("INFO", "payment-gateway", f"captured={st['captured']}")
        if st["queue_dropped"]:
            self.log("INFO", "confirm-queue", f"publish rate limit reached ({QUEUE_RATE_CAP}/min, policy=drop-new)")
        if st["paid"] or st["sent"]:
            self.log(
                "INFO",
                "platform",
                f"minute summary: arrivals={st['arrivals']} paid={st['paid']} "
                f"confirmed_sent={st['sent']} queue_depth={len(self.queue)}",
            )
        if st["settled"]:
            self.log("INFO", "refund-worker", f"settled={st['settled']}")
        if st["render_err"] > 2:
            self.log("ERROR", "ticket-renderer", f"{st['render_err']} render failures this minute")

    def stranded(self) -> list[tuple[Order, str]]:
        out: list[tuple[Order, str]] = []
        for oid in self.paid + self.unrecorded:
            o = self.orders[oid]
            if o.status == PAID_NO_SEAT:
                out.append((o, o.reason))
            elif o.status in (SEATED, CONFIRMED):
                seat = self.seats[o.seat]
                if len(seat.sold_to) > 1 and o.id in seat.sold_to and seat.sold_to[0] != o.id:
                    out.append((o, "seat_double_sold"))
                elif o.status == SEATED and self.t - (o.seated_at or 0) > GRACE:
                    out.append((o, "no_ticket_sent"))
                elif o.status == CONFIRMED and not o.delivered and self.t - (o.confirmed_at or 0) > GRACE:
                    out.append((o, "ticket_not_delivered"))
                elif o.status == CONFIRMED and not o.gate_ok and self.t - (o.corrupted_at or 0) > GRACE:
                    out.append((o, "ticket_gate_mismatch"))
            elif o.status == PAYMENT_UNRECORDED and self.t - (o.paid_at or 0) > GRACE:
                out.append((o, "payment_unrecorded"))
            elif o.status == REFUND_REQUESTED and self.t - (o.refund_at or 0) > REFUND_SLA:
                out.append((o, "refund_stuck"))
        return out

    def _refunds_pending(self) -> int:
        return sum(1 for o in self.orders.values() if o.status == REFUND_REQUESTED)

    def oversold(self) -> int:
        return sum(1 for s in self.seats.values() if len(s.sold_to) > 1)

    def _corrupted(self) -> int:
        return sum(1 for o in self.orders.values() if not o.gate_ok)

    def _unreconciled(self) -> int:
        return sum(1 for oid in self.unrecorded if self.orders[oid].status == PAYMENT_UNRECORDED)

    def metrics(self) -> dict:
        s = self.stranded()
        pend = [o for o in self.orders.values() if o.status == REFUND_REQUESTED]
        return {
            "time": self.clock(),
            "queue_depth": len(self.queue),
            "oldest_queue_age_min": (self.t - self.queue[0][1]) if self.queue else 0,
            "confirm_consumers": 0 if "confirmation_worker_down" in self.faults else 1,
            "paid_3m": self._ws("paid"),
            "assign_errors_3m": self._ws("assign_err"),
            "seat_conflicts_3m": self._ws("conflicts"),
            "holds_expired_3m": self._ws("expired"),
            "ticket_render_errors_3m": self._ws("render_err"),
            "hold_ttl_min": self.hold_ttl,
            "seats_oversold": self.oversold(),
            "refunds_pending": len(pend),
            "oldest_refund_age_min": max((self.t - o.refund_at for o in pend), default=0),
            "gateway_captured_3m": self._ws("captured"),
            "payments_unreconciled": self._unreconciled(),
            "queue_dropped_3m": self._ws("queue_dropped"),
            "sync_mismatches": self._corrupted(),
            "sync_corrupted_3m": self._ws("sync_corrupt"),
            "paid_total": len(self.paid),
            "confirmed_total": sum(1 for o in self.orders.values() if o.status == CONFIRMED),
            "free_seats": sum(1 for x in self.seats.values() if x.status == "free"),
            "stranded_customers": len(s),
            "stranded_by_reason": dict(Counter(r for _, r in s)),
        }

    def truth_ok(self, stranded: list | None = None) -> bool:
        """Ground truth (hidden from agents): no active harmful fault and nobody left waiting."""
        s = self.stranded() if stranded is None else stranded
        return (
            not self.unresolved_faults()
            and not s
            and self._refunds_pending() == 0
            and self.oversold() == 0
            and self._unreconciled() == 0
            and self._corrupted() == 0
        )

    def fix_confirmation_worker(self) -> dict:
        was = "confirmation_worker_down" in self.faults
        self.faults.discard("confirmation_worker_down")
        expired = [x for x in self.queue if self.t - x[1] > MSG_TTL]
        self.queue = deque(x for x in self.queue if self.t - x[1] <= MSG_TTL)
        self.log("INFO", "ops-console", "confirmation worker restarted")
        if expired:
            self.log(
                "WARN",
                "confirm-queue",
                f"{len(expired)} messages older than {MSG_TTL}m expired during outage (visibility timeout)",
            )
        return {"effective": was, "expired_messages": len(expired)}

    def fix_seat_assigner(self) -> dict:
        was = "seat_assign_timeout" in self.faults
        self.faults.discard("seat_assign_timeout")
        self.log("INFO", "ops-console", "seat-assigner restarted")
        return {"effective": was}

    def fix_refund_worker(self) -> dict:
        was = "refund_worker_failing" in self.faults
        self.faults.discard("refund_worker_failing")
        self.log("INFO", "ops-console", "refund-worker restarted, re-submitting unsettled requests")
        return {"effective": was}

    def set_hold_ttl(self, minutes: int) -> dict:
        if not 1 <= minutes <= 60:
            raise ValueError("minutes must be 1..60")
        changed = minutes != self.hold_ttl
        self.hold_ttl = minutes
        self.log("INFO", "ops-console", f"hold ttl set to {minutes}m")
        return {"effective": changed and minutes >= 5 and self.hold_ttl >= 5, "hold_ttl_min": minutes}

    def enable_unique_check(self) -> dict:
        was = "duplicate_sale" in self.faults
        self.faults.discard("duplicate_sale")
        self.log("INFO", "ops-console", "unique index on seat_assignments(seat_id) re-created")
        return {"effective": was}

    def rollback_template(self) -> dict:
        was = "ticket_render_bug" in self.faults
        self.faults.discard("ticket_render_bug")
        self.changes.append(f"{self.clock()} rollback ticket-template v7 -> v6")
        self.log("INFO", "ops-console", "ticket template rolled back to v6")
        return {"effective": was}

    def fix_payment_webhook(self) -> dict:
        was = "payment_webhook_drop" in self.faults
        self.faults.discard("payment_webhook_drop")
        self.log("INFO", "ops-console", "payment-webhook consumer restarted (dedupe cache cleared)")
        return {"effective": was}

    def replay_payments(self) -> dict:
        """Reconcile with the gateway ledger: record captured payments the order DB never saw, then assign seats."""
        pending = [self.orders[i] for i in self.unrecorded if self.orders[i].status == PAYMENT_UNRECORDED]
        self.unrecorded = [i for i in self.unrecorded if self.orders[i].status != PAYMENT_UNRECORDED]
        tmp: Counter = Counter()
        for o in pending:
            self.paid.append(o.id)
            tmp["paid"] += 1
            self._assign(o, tmp)
        if self.window:
            self.window[-1].update(tmp)
        self.log("INFO", "ops-console", f"replayed gateway ledger: recorded {len(pending)} missing payments")
        return {"effective": len(pending) > 0, "recorded": len(pending)}

    def fix_seat_sync(self) -> dict:
        was = "seat_sync_corruption" in self.faults
        self.faults.discard("seat_sync_corruption")
        self.changes.append(f"{self.clock()} rollback seat-sync v3.1 -> v3.0")
        self.log("INFO", "ops-console", "seat-sync job rolled back to v3.0")
        return {"effective": was}

    def repair_records(self) -> dict:
        n = 0
        for o in self.orders.values():
            if not o.gate_ok:
                o.gate_ok, o.corrupted_at = True, None
                n += 1
        self.log("INFO", "ops-console", f"repaired {n} ticket-gate records from the primary database")
        return {"effective": n > 0, "repaired": n}

    def fix_queue_limit(self) -> dict:
        was = "queue_overflow_drop" in self.faults
        self.faults.discard("queue_overflow_drop")
        self.changes.append(f"{self.clock()} config confirm-queue: publish limit removed (autoscale)")
        self.log("INFO", "ops-console", "confirm-queue publish limit removed")
        return {"effective": was}

    def replay_confirmations(self) -> dict:
        queued = {oid for oid, _ in self.queue}
        n = 0
        for oid in self.paid:
            o = self.orders[oid]
            if o.status == SEATED and oid not in queued:
                self.queue.append((oid, self.t))
                n += 1
        self.log("INFO", "ops-console", f"replayed {n} confirmations")
        return {"effective": n > 0, "replayed": n}

    def resend_tickets(self) -> dict:
        ok = fail = 0
        for oid in self.paid:
            o = self.orders[oid]
            if o.status == CONFIRMED and not o.delivered:
                if "ticket_render_bug" in self.faults and o.lang == "ar":
                    fail += 1
                else:
                    o.delivered = True
                    ok += 1
        self.log("INFO", "ops-console", f"resent tickets ok={ok} failed={fail}")
        return {"effective": ok > 0, "resent": ok, "failed": fail}
