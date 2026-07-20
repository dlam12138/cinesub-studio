"""Pinned, subtitle-oriented derivatives of WenYi.

Upstream: https://github.com/BigDawnGhost/wenyi
Release: v0.3.2
Commit: d07298e1139c631a5ddba0efc3c7a6956cf4b1af
License: MIT (see the adjacent LICENSE file)

Only the JSON recovery, agent-tier convention, prompt composition, rolling
context, reviewer and consistency concepts are adapted.  This package is not
an embedded copy of WenYi and deliberately excludes its document ingestion,
CLI, provider, database, polishing and write-back layers.
"""

UPSTREAM_PROJECT = "BigDawnGhost/wenyi"
UPSTREAM_RELEASE = "v0.3.2"
UPSTREAM_COMMIT = "d07298e1139c631a5ddba0efc3c7a6956cf4b1af"
UPSTREAM_LICENSE = "MIT"
ADAPTER_VERSION = "subtitle-adapter-v4"
PROMPT_VERSION = "wenyi-v032-subtitle-prompts-v2-batched-cross-line"
CACHE_SCHEMA_VERSION = 1
REPORT_SCHEMA_VERSION = 1
