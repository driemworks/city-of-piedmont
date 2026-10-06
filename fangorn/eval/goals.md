# What Piedmont Minutes is betting on

`eval/golden.jsonl` makes this precise: every check names the goal it serves, and
`westmarch-eval` scores each goal on its own. Change this file first, then the questions,
then (in a separate commit) the recipe.

**The bet (2026-10-05):** a city's own record, made searchable by meaning and drivable by
agents with no server, is more useful to the people it governs than a list of PDFs, and
shows what this stack makes of government logs in general.

## residents

The people of Piedmont and their city government: what did the council decide about a
thing, when, and where is it in the official record.

- Questions: a street, a building, a vehicle, a fund, a board ("the knuckle boom truck",
  "Civic Center renovations", "Zoning Board of Adjustments"), in plain words.
- Needs: every result jumps to the page of the official PDF it came from.

## researchers

People studying municipal government: patterns across meetings — what kinds of business,
how often, how much is spent, how the record changes over time.

- Questions: by kind (resolutions, ordinances, bills, contracts) and by year; counts an
  agent's filter must reach.
- Needs: `kind` and `year` filled on every item; every meeting since 2024 present.

## Guardrails, not goals

The record must be the record: every item carries its meeting's PDF sha256 and a page, and
no item is letterhead or a signature line.
