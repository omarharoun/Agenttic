"""Postgres backend + row-level tenant isolation.

Skipped unless ASCORE_TEST_PG points at a Postgres instance (CI sets it). The
same Registry code runs on SQLite (default) and Postgres; here we prove that on
a *shared* database, tenants are isolated by tenant_id.
"""

import os
import uuid

import pytest

PG = os.environ.get("ASCORE_TEST_PG")
pytestmark = pytest.mark.skipif(
    not PG, reason="set ASCORE_TEST_PG to a Postgres URL to run")


def test_migrations_and_row_level_isolation():
    from agenttic.registry.sqlite_store import Registry, make_engine
    from agenttic.schema.agent import DeclaredAgent
    from agenttic.schema.testcase import TestCase, TestSuite

    engine = make_engine(PG)  # also runs migrations via Registry below
    t1, t2 = f"t_{uuid.uuid4().hex[:8]}", f"t_{uuid.uuid4().hex[:8]}"
    r1 = Registry(engine=engine, tenant=t1)
    r2 = Registry(engine=engine, tenant=t2)

    # catalog isolation on a shared DB
    r1.register_agent(DeclaredAgent(agent_id="shared-name", variant="reference"))
    assert [a["agent_id"] for a in r1.list_declared_agents()] == ["shared-name"]
    assert r2.list_declared_agents() == []

    # suites with the SAME id coexist across tenants (row-level unique)
    for reg, ctx in ((r1, "ctx1"), (r2, "ctx2")):
        cases = [TestCase(test_id="c0", suite_id="s", task_description="t",
                          input={}, rubric_id="r")]
        reg.save_suite(TestSuite(suite_id="s", business_context=ctx,
                                 test_ids=["c0"], approved=True), cases)
    assert r1.get_suite("s")[0].business_context == "ctx1"
    assert r2.get_suite("s")[0].business_context == "ctx2"

    # spend ledger is per-tenant
    r1.record_spend("m", 1.0)
    assert r1.spend_today() == 1.0 and r2.spend_today() == 0.0
