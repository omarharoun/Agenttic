"""Per-tenant LLM spend quotas (daily/monthly), enforced pre-run."""

import pytest

from agenttic.budget import BudgetExceededError, budget_context, check_pre_run, tenant_quota
from agenttic.registry.sqlite_store import Registry

CFG = {
    "budget": {"max_run_cost_usd": 0.0, "max_daily_cost_usd": 0.0},
    "quotas": {"default": {"daily_usd": 5.0, "monthly_usd": 50.0},
               "tiers": {"big": {"daily_usd": 1000.0, "monthly_usd": 20000.0}}},
}


def test_tenant_quota_resolution():
    assert tenant_quota(CFG, "big") == {"daily_usd": 1000.0, "monthly_usd": 20000.0}
    assert tenant_quota(CFG, "someone") == {"daily_usd": 5.0, "monthly_usd": 50.0}


def test_daily_quota_blocks(tmp_path):
    reg = Registry(tmp_path / "q.db", tenant="acme")  # uses default tier
    reg.record_spend("m", 4.0)                         # already spent $4 today
    # projected $2 -> 4+2=6 > daily 5 -> blocked
    with pytest.raises(BudgetExceededError):
        check_pre_run(CFG, reg, projected_usd=2.0)
    # projected $0.5 -> 4.5 <= 5 -> ok
    assert check_pre_run(CFG, reg, projected_usd=0.5) == []


def test_higher_tier_not_blocked(tmp_path):
    reg = Registry(tmp_path / "q.db", tenant="big")
    reg.record_spend("m", 4.0)
    assert check_pre_run(CFG, reg, projected_usd=100.0) == []   # well under 1000


def test_budget_context_reports_quota(tmp_path):
    reg = Registry(tmp_path / "q.db", tenant="acme")
    reg.record_spend("m", 4.0)
    ctx = budget_context(CFG, reg, projected_usd=2.0)
    assert ctx["quota_daily_usd"] == 5.0
    assert ctx["spent_today_usd"] == 4.0
    assert ctx["would_exceed_quota_daily"] is True
