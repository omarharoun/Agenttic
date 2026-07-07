"""Enforcement gateway (SPEC-2 M11–M15).

An inline gateway over an agent's tool calls and results: hash-verified policy
load → Lane 1 (deterministic) → Lane 2 (classifiers) → append-only log → async
Lane 3 enqueue. Policies are compiled from certification evidence (M12); nothing
enforces without a logged decision (Hard Rule 19).
"""
