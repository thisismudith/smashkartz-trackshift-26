"""Simulator data pipeline: turns a raw TracingInsights mirror into small versioned
artifacts. The mirror is data/raw/tracinginsights/<year>, resolved in paths.py -- no
module here hardcodes a season.

See the architecture plan for the measured facts this implements. Nothing here invents
geometry: every number is either derived from telemetry or tagged as a RULE/DEFAULT.
"""
