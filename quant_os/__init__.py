"""Quant OS — research, validation and lifecycle around the existing engine.

Additive by design. `earner.trading` remains the execution and risk core and is
imported, never replaced: it holds the deterministic risk gate, the order
lifecycle, reconciliation and the broker adapters, all of which are tested and
working. What lives here is the half the audit found missing — evidence that a
strategy deserves to run at all.

The ordering principle throughout: the filter is built before the generator.
Anything that can propose strategies is downstream of everything that can
reject them.
"""
