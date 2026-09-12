"""Simulator data pipeline: turns data/2026 raw telemetry into small versioned artifacts.

See the architecture plan for the measured facts this implements. Nothing here invents
geometry: every number is either derived from telemetry or tagged as a RULE/DEFAULT.
"""
