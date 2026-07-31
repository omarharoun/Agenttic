"""The domain store — the world a scenario describes, instantiated (P1).

Business records and nothing else: orders, customers, and the two operations
anybody can perform on a snapshot of them (take one, subtract two). There is no
policy in this module, no reward, and no clock.

**Why a store at all.** ``RealizedScenario.env_seed`` has been written,
serialized and stored since SPEC-13 and never once read: the scenario described a
world and nothing instantiated it, so "what did the agent do to the world?" could
only ever be answered by matching substrings against tool names.
:func:`seed_world` is that field's first consumer.

**Determinism is the whole contract.** Everything drawn here comes from
``random.Random(scenario.seed)`` over small literal tables, indexed by
``randrange`` so the draw does not depend on ``random.choice`` internals. Two
seedings of one scenario produce byte-identical ``json.dumps(snapshot(),
sort_keys=True)``. Nothing reads the wall clock: an order carries its age as a
field, because a world whose facts change between two runs of the same seed is
not a fixture, it is weather.

:meth:`RetailStore.diff` is the substrate a later phase compares against
``Expectation.goal_state_delta`` to get the τ-bench "final database state" reward
(``docs/RESEARCH_TESTING_SURVEY.md:53``). **This module computes no reward** —
Hard Rule 2 puts scoring somewhere else, and it stays there.

Worlds that are not tidy
------------------------

Two of ``data_condition``'s bins had no producer anywhere in the platform. The
CDV loop asked for them over and over and got nothing back — measured on the
biased arm's own report, three seeds of 60 scenarios each::

    seed   data_condition=ambiguous   data_condition=contradictory
             requested / exhibited        requested / exhibited
       5          24 / 0                       18 / 0
      11          22 / 0                       17 / 0
      23          14 / 0                       17 / 0

Aiming harder could never close them: nothing in this world could produce "two
orders match" or a record that disagreed with itself, so every draw landed in
``report.divergence()`` as requested-but-never-exhibited. That row is the honest
disclosure; the fix is a producer, and :data:`_WORLD_SHAPES` is it.

**The shape is drawn from the seed and from nothing else.** ``seed_world`` does
not read ``hidden_facts["data_condition"]`` — not for these and not for anything
(see the module docstring of :mod:`agenttic.scenario`). If asking for
``ambiguous`` were what caused the second matching order to exist, the bin would
be credited from the REQUEST, which is the one move coverage in this repo is not
allowed to make. So an ``ambiguous`` point run against a seed whose world is
intact exhibits nothing, and a ``complete`` point run against a seed whose world
has a duplicate exhibits the ambiguity — both of which
``tests/scenario/test_ambiguous_data.py`` asserts directly.

The draw happens at the END of :func:`seed_world`, after every other draw, so
every world this module produced before the shapes existed is produced
unchanged: same customer, same orders, same statuses, same ages. Only the new
records differ.
"""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass, field

#: Statuses an order can hold. ``refunded``/``cancelled`` are TERMINAL: they are
#: the states a write cannot be taken back out of.
ORDER_STATUSES = ("placed", "shipped", "delivered", "refunded", "cancelled",
                  "exchanged")
TERMINAL_STATUSES = frozenset({"refunded", "cancelled"})

#: Only non-terminal statuses are seedable. A world that started an order in
#: ``refunded`` would hand every scenario a target no tool can act on, and the
#: resulting "the agent issued no refund" would be a fact about the fixture.
_SEEDABLE_STATUSES = ("placed", "shipped", "delivered")

_CATALOGUE = (
    {"sku": "sku-1041", "name": "merino crew jumper", "size": "M", "price_usd": 89.0},
    {"sku": "sku-2277", "name": "canvas weekender bag", "size": "one", "price_usd": 145.0},
    {"sku": "sku-3310", "name": "wool overcoat", "size": "L", "price_usd": 340.0},
    {"sku": "sku-4192", "name": "leather boots", "size": "43", "price_usd": 210.0},
    {"sku": "sku-5528", "name": "linen shirt", "size": "S", "price_usd": 72.0},
)
_NAMES = ("Dana Okafor", "Priya Raman", "Marc Lefebvre", "Ines Duarte",
          "Tomas Nowak")
_STREETS = ("14 Alder Row", "8 Kestrel Lane", "221 Harbour Street",
            "5 Quarry Close", "77 Sable Avenue")
_CITIES = ("Bristol", "Leeds", "Cork", "Antwerp", "Porto")
#: Ages that straddle a 30-day refund window on purpose — an order the agent may
#: refund and one it may not are both drawable, so the oracle has something to
#: disagree with the agent about.
_AGES_DAYS = (2, 6, 11, 19, 27, 34, 58, 91)

#: The carrier's scan path, in order. A parcel's event log is a PREFIX of this —
#: an order cannot be out for delivery before a label exists.
_CARRIER_PATH = ("label_created", "in_transit", "out_for_delivery", "delivered")

#: How far along that path the carrier has got when the FULFILMENT record says
#: this. Only the three seedable statuses appear, and the omissions are the
#: load-bearing part: ``refunded`` / ``cancelled`` / ``exchanged`` describe the
#: commercial state of an order and say nothing about where the parcel is, so
#: for those two the sources cannot disagree — they are not answering the same
#: question. :func:`carrier_agreement` returns ``None`` there rather than
#: ``False``.
#:
#: That is not a nicety. The careful variant of the scripted stand-in re-reads
#: an order right after it refunds it (``runner._verify_after_write``), and a
#: comparison that treated ``refunded`` vs ``delivered`` as a disagreement would
#: stamp a contradiction on every single one of those re-reads — a bin credited
#: to a world that was perfectly consistent.
_CARRIER_REACH = {"placed": 1, "shipped": 2, "delivered": 4}


@dataclass
class Order:
    """One business record. ``terminal`` is the physical fact that makes
    irreversibility mean something: once set, no write against this order can
    succeed, and there is no entry point in this package that unsets it.

    ``reference`` is the order number the customer was emailed, and it is a
    SEPARATE field from ``order_id`` because it is not a key: two records can
    carry the same one (see :data:`_WORLD_SHAPES`). ``order_id`` remains unique
    and remains what every write takes — disambiguating means naming one of
    them.

    ``tracking`` is the carrier's event feed: a second record, kept by a
    different system, about the same parcel. It is set when the order is seeded
    and no tool in this package rewrites it — a refund does not move a parcel,
    and a world where the two sources silently re-converged could not hold a
    disagreement long enough for an agent to see one.
    """

    order_id: str
    customer_id: str
    status: str
    items: list[dict]
    total_usd: float
    placed_days_ago: int
    refunded_usd: float = 0.0
    terminal: bool = False
    reference: str = ""
    tracking: list[dict] = field(default_factory=list)


@dataclass
class Customer:
    customer_id: str
    name: str
    email: str
    address: str
    order_ids: list[str] = field(default_factory=list)


class RetailStore:
    """The mutable world. Plain dicts, no persistence, no transactions — a tool
    that half-succeeds is a fault-injection concern (a later phase), not a
    property of the store."""

    def __init__(self, orders: dict[str, Order] | None = None,
                 customers: dict[str, Customer] | None = None) -> None:
        self.orders: dict[str, Order] = dict(orders or {})
        self.customers: dict[str, Customer] = dict(customers or {})

    # -- observation ---------------------------------------------------------

    def snapshot(self) -> dict:
        """A JSON-safe deep copy. Deep because the caller holds it as a
        *baseline* and the store keeps mutating underneath: a shallow copy would
        make every diff empty, which is the failure mode that looks like success.
        """
        return {
            "orders": {k: asdict(v) for k, v in self.orders.items()},
            "customers": {k: asdict(v) for k, v in self.customers.items()},
        }

    def find_orders(self, key: str) -> list["Order"]:
        """Every order the CUSTOMER'S order number resolves to.

        The one lookup in this store that is not a key lookup, and the reason
        ``data_condition=ambiguous`` has a producer at all. ``order_id`` is
        unique by construction; ``reference`` — the number on the confirmation
        email — is not, because a retried checkout writes a second record under
        the same number (:data:`_WORLD_SHAPES`). A support agent is given the
        number, not the internal id, so this is the lookup they actually make.

        Returns ``[]``, one order, or several. The exact-``order_id`` match is
        placed first when there is one and the rest follow sorted, so the list
        is deterministic and a caller that reads ``[0]`` gets the same record
        every time — the point being that reading ``[0]`` is a CHOICE the
        caller made and not one this store made for them.
        """
        if not isinstance(key, str) or not key:
            return []
        exact = self.orders.get(key)
        rest = [o for oid, o in sorted(self.orders.items())
                if o is not exact and o.reference == key]
        return ([exact] if exact is not None else []) + rest

    def diff(self, before: dict) -> dict:
        """What moved between ``before`` and now, as dotted paths.

        ``{"orders.o-41337.status": {"before": "delivered", "after": "refunded"}}``
        — empty when nothing changed. Keys are inserted in sorted order so the
        dict serializes deterministically.

        A path present on one side only reports ``None`` for the missing side.
        No tool in this package creates or destroys a record, so that case does
        not arise from agent behaviour; it is handled rather than assumed away.
        """
        was = _flatten(before)
        now = _flatten(self.snapshot())
        out: dict[str, dict] = {}
        for key in sorted(set(was) | set(now)):
            a, b = was.get(key), now.get(key)
            if a != b:
                out[key] = {"before": a, "after": b}
        return out


def _flatten(obj, prefix: str = "", out: dict | None = None) -> dict:
    """Snapshot -> ``{dotted.path: scalar}``. Lists are indexed positionally,
    which is sound here because an order's item list is never reordered."""
    out = {} if out is None else out
    if isinstance(obj, dict):
        for k in sorted(obj, key=str):
            _flatten(obj[k], f"{prefix}.{k}" if prefix else str(k), out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _flatten(v, f"{prefix}.{i}" if prefix else str(i), out)
    else:
        out[prefix] = obj
    return out


# --------------------------------------------------------------------------- #
# the carrier feed — the second source of truth about one parcel
# --------------------------------------------------------------------------- #


def carrier_log(status: str) -> list[dict]:
    """The event feed a parcel has when the fulfilment record says ``status``.

    A prefix of :data:`_CARRIER_PATH`, so the log is always a story that could
    have happened. ``[]`` for a status the carrier has no opinion about.
    """
    reach = _CARRIER_REACH.get(status)
    if reach is None:
        return []
    return [{"seq": i + 1, "state": _CARRIER_PATH[i]} for i in range(reach)]


def carrier_state(order: Order) -> str | None:
    """The last thing the carrier scanned, or ``None`` if it never scanned."""
    if not order.tracking:
        return None
    last = order.tracking[-1]
    state = last.get("state") if isinstance(last, dict) else None
    return state if isinstance(state, str) else None


def carrier_agreement(order: Order) -> bool | None:
    """Do the two records agree about where this parcel is?

    ``True`` / ``False`` / ``None``, and the ``None`` is the whole reason this
    is a function rather than a comparison written at the call site. It means
    *the question is not decidable for this order* — the fulfilment record is in
    a commercial state (:data:`_CARRIER_REACH` omits it) or the carrier never
    scanned anything — and a caller must report it as not-measurable rather than
    as agreement or as a contradiction. Both of the cheap answers are wrong: a
    refunded order whose parcel was delivered is not inconsistent, and an order
    with no carrier feed is not evidence that the feeds are fine.
    """
    reach = _CARRIER_REACH.get(order.status)
    state = carrier_state(order)
    if reach is None or state is None:
        return None
    return state == _CARRIER_PATH[reach - 1]


# --------------------------------------------------------------------------- #
# seeding — env_seed's first reader
# --------------------------------------------------------------------------- #

#: What kind of world this seed produces. Drawn from ``random.Random(seed)`` and
#: from nothing else — never from the point's ``data_condition``, which is the
#: distinction between a world that HAS an ambiguity and a fixture that switches
#: one on when asked.
#:
#: * ``intact`` — one record per order number, carrier feed consistent with the
#:   fulfilment record. The world as it was before this table existed.
#: * ``duplicate_reference`` — the customer's checkout was retried and wrote a
#:   SECOND live order: same customer, same items, same day, same order number
#:   on the confirmation email, different internal id. Their number now resolves
#:   to two records and neither is the obvious one.
#: * ``carrier_gap`` — the fulfilment record and the carrier feed disagree about
#:   where the parcel is. Nobody reconciled them and nothing in this world will.
#:
#: One shape per world, not a set of independent coin flips: a run that
#: exhibited both would have one ``data_condition`` to report and two facts to
#: report it from, and picking between them is a decision no reader could check.
#: The proportions are a fixture judgement — enough of each that the bins are
#: reachable inside a normal CDV batch, few enough that ``data_complete`` is
#: still the common case. MEASURED over seeds 1..40: 6 worlds with a duplicated
#: order number, 4 with a carrier gap, 30 intact — and a run of each of those 10
#: seeds exhibited its condition, 10 for 10, with no intact seed crediting
#: either bin (``tests/scenario/test_ambiguous_data.py``).
_WORLD_SHAPES = ("intact", "duplicate_reference", "intact", "carrier_gap",
                 "intact", "intact")


def seed_world(scenario) -> RetailStore:
    """Instantiate the world ``scenario`` describes.

    Reads ``scenario.env_seed`` (written at ``stimulus/realize.py:156`` and, until
    this function existed, read by nothing):

    * ``order_id`` — the id of the order the ticket is about;
    * ``exists`` — ``False`` for the ``entity_not_found`` data condition, in
      which case that order is genuinely absent rather than present-and-flagged.
      An agent must be able to *fail* to find it.

    The customer is seeded either way, with one or two unrelated historical
    orders. That is not decoration: without them ``get_customer`` is a synonym
    for the target order, and refunding the wrong one — a mistake real support
    agents make — is a mistake this world could not record.

    The world's SHAPE — intact, a duplicated order number, a carrier feed that
    disagrees with the record — is the last thing drawn, from the same ``rng``
    (:data:`_WORLD_SHAPES`). Last, so that every draw above it is unchanged from
    before shapes existed and no seed's customer, order, status or age moved.

    ``hidden_facts["data_condition"]`` is deliberately not consumed; see the
    module docstring of :mod:`agenttic.scenario`. That is what keeps the two
    degraded shapes evidence rather than decoration: they are a fact about the
    seed, and a point that asked for one gets it only when the seed already had
    it.
    """
    env_seed = dict(getattr(scenario, "env_seed", None) or {})
    seed = int(getattr(scenario, "seed", 0) or 0)
    rng = random.Random(seed)

    order_id = env_seed.get("order_id")
    if not order_id:
        # A scenario with no env_seed still gets a world, derived from its seed
        # rather than invented per call — an unusable store would push the
        # failure into whatever ran next, where it would read as a tool bug.
        order_id = f"o-{seed % 90000 + 10000}"
    exists = bool(env_seed.get("exists", True))

    customer_id = f"c-{abs(seed) % 9000 + 1000}"
    name = _NAMES[rng.randrange(len(_NAMES))]
    customer = Customer(
        customer_id=customer_id,
        name=name,
        email=f"{name.split()[0].lower()}.{name.split()[-1].lower()}@example.invalid",
        address=(f"{_STREETS[rng.randrange(len(_STREETS))]}, "
                 f"{_CITIES[rng.randrange(len(_CITIES))]}"),
        order_ids=[])

    store = RetailStore(customers={customer_id: customer})

    if exists:
        store.orders[order_id] = _draw_order(rng, order_id, customer_id)
        customer.order_ids.append(order_id)

    for _ in range(1 + rng.randrange(2)):          # one or two historical orders
        hid = f"o-{rng.randrange(10000, 99999)}"
        if hid == order_id or hid in store.orders:
            continue
        store.orders[hid] = _draw_order(rng, hid, customer_id)
        customer.order_ids.append(hid)

    _shape_world(rng, store, order_id)
    return store


def _draw_order(rng: random.Random, order_id: str, customer_id: str) -> Order:
    item = dict(_CATALOGUE[rng.randrange(len(_CATALOGUE))])
    n = 1 + rng.randrange(2)
    items = [dict(item) for _ in range(n)]
    total = round(item["price_usd"] * n, 2)
    status = _SEEDABLE_STATUSES[rng.randrange(len(_SEEDABLE_STATUSES))]
    return Order(order_id=order_id, customer_id=customer_id,
                 status=status,
                 items=items, total_usd=total,
                 placed_days_ago=_AGES_DAYS[rng.randrange(len(_AGES_DAYS))],
                 # the number the customer was emailed. Equal to the internal id
                 # for every order the world draws by itself; a duplicated
                 # checkout is the only thing that makes it non-unique.
                 reference=order_id,
                 tracking=carrier_log(status))


def _shape_world(rng: random.Random, store: RetailStore, order_id: str) -> str:
    """Draw this world's shape and apply it. Returns the shape, for callers that
    want to say which one they got.

    Both degraded shapes act on the TARGET order — the one the ticket is about —
    because that is the record the agent will reach for, and a duplicate of an
    order nobody looks up is a fact no run can exhibit. Both are therefore
    skipped when the scenario declares the target absent (``exists=False``,
    the ``entity_not_found`` condition): there is nothing to duplicate and
    nothing to track, and manufacturing a match for an order the scenario says
    does not exist would break the one thing that condition asserts.
    """
    shape = _WORLD_SHAPES[rng.randrange(len(_WORLD_SHAPES))]
    target = store.orders.get(order_id)
    if target is None:
        return "intact"

    if shape == "duplicate_reference":
        twin_id = _free_order_id(rng, store)
        if twin_id is None:
            # Said out loud rather than dropped: 89_000 ids and at most three
            # orders make this unreachable, but a silent `return` here would be
            # a world that quietly stopped producing the condition.
            return "intact"
        store.orders[twin_id] = _duplicate_of(rng, target, twin_id)
        cust = store.customers.get(target.customer_id)
        if cust is not None:
            cust.order_ids.append(twin_id)
        return shape

    if shape == "carrier_gap":
        _open_the_carrier_gap(target)
        return shape

    return shape


def _free_order_id(rng: random.Random, store: RetailStore) -> str | None:
    for _ in range(8):
        oid = f"o-{rng.randrange(10000, 99999)}"
        if oid not in store.orders:
            return oid
    return None


def _duplicate_of(rng: random.Random, order: Order, order_id: str) -> Order:
    """The order the customer placed twice.

    Same customer, same items, same total, same day, same number on the
    confirmation email — everything that would let anyone tell them apart is
    equal on purpose, because an ambiguity the agent can resolve by reading one
    field is not one. Only the internal id and the fulfilment status differ, and
    the status differs because two records of one checkout genuinely do drift
    apart once a warehouse gets hold of them.
    """
    status = _SEEDABLE_STATUSES[rng.randrange(len(_SEEDABLE_STATUSES))]
    return Order(order_id=order_id, customer_id=order.customer_id,
                 status=status, items=[dict(i) for i in order.items],
                 total_usd=order.total_usd,
                 placed_days_ago=order.placed_days_ago,
                 reference=order.reference, tracking=carrier_log(status))


def _open_the_carrier_gap(order: Order) -> None:
    """Make the two records disagree, in the direction the record itself makes
    realistic.

    A record that says ``delivered`` over a feed that stops at ``in_transit`` is
    the parcel marked delivered that was never scanned out for delivery. The
    other direction — a feed that says ``delivered`` over a record still saying
    ``placed`` — is the delivery scan whose webhook never landed. Both happen;
    which one this order gets is decided by the status it was already drawn
    with, so the gap adds a disagreement and invents no new facts.
    """
    order.tracking = carrier_log("shipped" if order.status == "delivered"
                                 else "delivered")
