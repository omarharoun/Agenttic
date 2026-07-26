"""Hooks that let an EXTERNAL agent feed its own traces to agenttic.

`agenttic certify` can only drive agents it has an adapter for. A coding agent
(Claude Code, Cursor, an SWE agent) is not one of those — it runs on its own, in
someone's repo, on real work. The only way to verify it is to receive the traces
it produces, and a tool-use hook is the cheapest place to emit them: it sees the
tool name AND the arguments, which is where a shell command's risk actually lives.
"""
