# Project Context & Guidance

## Goal

For now, the goal with this repo is to **fetch and analyze Polymarket data for
Formula 1 (F1) related markets**.

## Why this repo

This upstream repo is used as a base because it already has a working method to
**fetch Polymarket trades from the blockchain**. Building that from scratch would be
significant work, so the existing blockchain fetch is the main reason for using it.

## Scope of use

- **In scope:** the data-fetching implementation. We have extended it with:
  - additional ways to fetch data (e.g. the Polymarket Data API path), and
  - the ability to save only certain data to disk (tag-filtered blockchain fetch),
    to avoid the full dataset's large disk footprint (>60GB) when only a subset
    (e.g. F1) is wanted.
- **Not in scope (for now):** the repo's analysis tooling. We are not currently
  interested in using the built-in analysis scripts; the focus is on data fetching.

## Programming guidance

- Implement everything in the **simplest, cleanest way with minimal code**.
- **No over-engineering.** Prefer the smallest change that solves the problem;
  avoid speculative abstractions and unnecessary configuration.
