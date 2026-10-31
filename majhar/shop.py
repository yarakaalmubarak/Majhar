"""Storefront on top of the simulated platform: named customers, bookings and the demo crowd."""

from __future__ import annotations

import random
from collections import Counter, deque

from .sim import CONFIRMED, HOLD, PAYMENT_DROP_PCT, PRICES, Order, Sim

FANS = [
    "Noura",
    "Abdullah",
    "Reem",
    "Faisal",
    "Lama",
    "Khalid",
    "Hessa",
    "Turki",
    "Dana",
    "Majed",
    "Jawaher",
    "Saud",
    "Ghada",
    "Fahad",
    "Maha",
    "Nasser",
    "Shahad",
    "Rakan",
    "Joud",
    "Yousef",
    "Haya",
    "Omar",
    "Layan",
    "Bandar",
]


class ShopSim(Sim):
    def __init__(self, *args, **kw):
        kw.setdefault("arrivals", 0)
        self.feed: deque[dict] = deque(maxlen=600)
        self.feed_seq = 0
        self.protected: set[str] = set()
        super().__init__(*args, **kw)
        self.shoppers: dict[str, list[int]] = {}
        self.bookings: dict[str, list[int]] = {}
        self.booking_of: dict[int, str] = {}
        self._bk = 1001

    def log(self, level: str, comp: str, msg: str) -> None:
        super().log(level, comp, msg)
        self.note(level, comp, msg)

    def note(self, level: str, comp: str, msg: str) -> None:
        """An entry for the system log only (agent decisions, human approvals, injected failures)."""
        self.feed_seq += 1
        self.feed.append({"seq": self.feed_seq, "time": self.clock(), "level": level, "src": comp, "text": msg})

    def _arrivals(self, st: Counter) -> None:
        """Steady traffic after a rush."""
        hidden = [x for x in self.seats.values() if x.section in self.protected and x.status == "free"]
        for x in hidden:
            x.status = "kept"
        first = self.next_id
        try:
            super()._arrivals(st)
        finally:
            for x in hidden:
                if x.status == "kept":
                    x.status = "free"
        for oid in range(first, self.next_id):
            if oid in self.orders:
                self.orders[oid].customer = FANS[oid % len(FANS)]

    def inventory(self) -> dict[str, int]:
        free = {sec: 0 for sec in PRICES}
        for s in self.seats.values():
            if s.status == "free":
                free[s.section] += 1
        return free

    def orders_of(self, customer: str) -> list[Order]:
        return [self.orders[i] for i in self.shoppers.get(customer, [])]

    def _next_id(self, force_drop: bool = True) -> int:
        """Next order id."""
        oid = self.next_id
        if force_drop and "payment_webhook_drop" in self.faults:
            while (oid * 2246822519) % 100 >= PAYMENT_DROP_PCT:
                oid += 1
        self.next_id = oid + 1
        return oid

    def place_order(self, customer: str, section: str, lang: str = "en") -> Order | None:
        free = sorted(
            (s for s in self.seats.values() if s.status == "free" and s.section == section), key=lambda s: int(s.id[1:])
        )
        if not free:
            return None
        seat = free[0]
        oid = self._next_id()
        o = Order(oid, customer, seat.id, section, PRICES[section], lang, pay_at=self.t + 1)
        self.orders[oid] = o
        seat.status, seat.holder, seat.hold_expires = "held", oid, self.t + self.hold_ttl
        self.shoppers.setdefault(customer, []).append(oid)
        self.log("INFO", "storefront", f"order={oid} seat={seat.id} created, awaiting payment")
        return o

    def place_booking(
        self,
        customer: str,
        section: str,
        qty: int = 1,
        lang: str = "en",
        force_drop: bool = True,
        pay_now: bool = True,
        charge: bool = True,
    ) -> dict | None:
        """A customer buys `qty` tickets in one checkout."""
        qty = max(1, min(int(qty), 4))
        free = sorted(
            (s for s in self.seats.values() if s.status == "free" and s.section == section), key=lambda s: int(s.id[1:])
        )
        if len(free) < qty:
            return None
        ref = f"BK-{self._bk}"
        self._bk += 1
        ids = []
        for seat in free[:qty]:
            oid = self._next_id(force_drop)
            o = Order(oid, customer, seat.id, section, PRICES[section], lang, pay_at=self.t if pay_now else self.t + 1)
            self.orders[oid] = o
            seat.status, seat.holder, seat.hold_expires = "held", oid, self.t + self.hold_ttl
            self.shoppers.setdefault(customer, []).append(oid)
            self.booking_of[oid] = ref
            ids.append(oid)
        self.bookings[ref] = ids
        self.log("INFO", "storefront", f"booking={ref} customer={customer} tickets={qty} section={section}")
        if pay_now and charge:
            self.charge_now()
        return {"booking": ref, "order_ids": ids, "amount_sar": PRICES[section] * qty}

    def charge_now(self) -> None:
        """Process every payment that is due right now (the checkout does not wait for the next minute)."""
        st: Counter = Counter()
        self._payments(st)
        if self.window:
            self.window[-1].update(st)
        else:
            self.window.append(st)

    def rush(self, fans: int, rng: random.Random, sections: str = "BBC") -> int:
        """Rush hour: many fans check out at once."""
        n = 0
        for _ in range(fans):
            sec = rng.choice(sections)
            b = self.place_booking(
                rng.choice(FANS),
                sec,
                rng.choice([1, 1, 2, 2, 3]),
                rng.choice(["ar", "ar", "en"]),
                force_drop=False,
                charge=False,
            )
            n += len(b["order_ids"]) if b else 0
        self.charge_now()
        return n

    def sell_out(self, section: str, limit: int | None = None) -> int:
        """Other shoppers buy the remaining seats in the section (all of them, or `limit`), including seats whose hold
        lapsed while a payment was stuck.
        """
        taken = 0
        for s in sorted(self.seats.values(), key=lambda s: (s.section, int(s.id[1:]))):
            if s.section != section or (limit is not None and taken >= limit):
                continue
            holder = self.orders.get(s.holder) if s.status == "held" else None
            if s.status == "free" or (s.status == "held" and holder is not None and holder.status != HOLD):
                oid = self.next_id
                self.next_id += 1
                o = Order(
                    oid,
                    f"shopper-{oid}",
                    s.id,
                    section,
                    PRICES[section],
                    "en",
                    pay_at=self.t,
                    status=CONFIRMED,
                    paid_at=self.t,
                    seated_at=self.t,
                    confirmed_at=self.t,
                    delivered=True,
                )
                self.orders[oid] = o
                self.paid.append(oid)
                s.status, s.holder, s.sold_to = "sold", None, [oid]
                taken += 1
        self.log("INFO", "storefront", f"other shoppers bought {taken} seats in section {section}")
        return taken
