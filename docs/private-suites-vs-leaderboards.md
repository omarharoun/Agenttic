# Why private suites + integrity gates beat public leaderboards

*A procurement note (SPEC-6 Step 28).*

Public agent benchmarks are **structurally contaminated**. Their tasks live in
public repositories, so they end up in training corpora; a model can score well
because it memorised the answers, not because it can do the work. This is not a
hypothetical: Terminal-Bench 2.0 scores **0 on ABC item III.3** (contamination
resistance) in its *own* self-assessment (Merrill et al., arXiv:2601.11868;
checklist from Zhu et al., arXiv:2507.02825). An honest benchmark admitting it
cannot rule out contamination is exactly why a public leaderboard number is a
weak basis for a purchasing decision.

## What Agenttic does instead

1. **Private by construction.** Client suites are generated from your own
   business context and never published. There is no public copy to train on.

2. **A per-tenant canary.** Every suite version carries a canary string that is
   distinct per tenant and per version, embedded in the shipped artifacts. If an
   agent ever reproduces it, we know the suite (or a copy) reached its training
   data — and we say so.

3. **A perturbation probe.** We run the agent on the exact stored cases *and* on
   lightly perturbed variants of the same difficulty. An agent that aces the
   originals but fails the variants is memorising, not reasoning — flagged.

4. **The instruments are themselves verified.** Before any suite is used it must
   pass the oracle (solvable), dummy (non-vacuous) and exploit (cheat-resistant)
   gates, and it ships with an ABC benchmark-rigor scorecard. The certification
   bureau certifies its own instruments.

## The one line on every report

Each client scorecard carries a standard contamination line:

> Suite origin: private · canary: intact · perturbation gap: 0% · agent exposure: none detected

so the reader can see, at a glance, that the number reflects capability on tasks
the agent could not have seen — the opposite of a contaminated leaderboard.
