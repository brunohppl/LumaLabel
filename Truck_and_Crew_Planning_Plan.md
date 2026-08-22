# Truck & Crew Planning — a mechanism for efficient scheduling

*LUMA Warehouse App — planning document, August 2026*

## The goal, stated precisely

Every scheduled day is a budget: each person and each truck has **480 minutes
(07:30–15:30)**. Wastage is the part of that budget spent neither on site nor
driving between sites. The mechanism's job is to help the scheduler spend as
close to 480 productive minutes per person as is safe, while never breaking a
hard rule (an install whose stock wasn't loaded, two jobs a truck can't reach
in time, a heavy job with one person on it).

The right shape for this is an **assistant, not an autopilot**: the app
measures, warns and proposes; the scheduler decides. The scheduler knows
things the system never will — a fragile client, a slow lift, a new hire.

## Why we can't optimise yet — and what to fix first

An optimiser is only as good as three numbers we currently guess:

1. **How long a job really takes.** Today every transport visit is 60 min and
   every styling visit 180, regardless of whether it's a 1-bed apartment or a
   5-bed house. We now sync property type/size from Monday and we know each
   job's item count — but we have no *actuals* to calibrate against.
2. **How long the driving takes.** Addresses exist and the Distance Matrix
   key is already used for ETAs, but travel time plays no part in the plan.
   Two jobs 40 minutes apart can currently be scheduled back-to-back.
3. **How big the load is.** Trucks differ, jobs differ; the only current
   signal is the scheduler's memory. Item count is a workable first proxy
   (it's already in the database), refined later by bulky-item flags.

**Phase 1 is therefore data, not algorithms.** Everything after it gets
smarter for free as these numbers improve.

## Phase 1 — capture the truth (1–2 weeks of work)

- **Actuals from the team view.** The Navigate button already captures
  location and time. Add one more tap: **"Done"** on the job card, recording
  actual start and finish per crew. No new screens; two timestamps.
- **Duration model, dumb on purpose.** A nightly job averages actuals by
  (job type × property size band × item-count band). Until enough data
  exists, seed with the current defaults. The popover then *pre-fills* its
  duration from this table instead of a constant — the scheduler can always
  override, and overrides are themselves signal.
- **Travel matrix.** When a day is saved, fetch drive times between that
  day's consecutive jobs per column (cached by address pair, so the API cost
  is small and shrinks over time).
- **Staff availability.** A simple table of who is off on which date.
  Crew membership already exists on day_teams; availability is the missing
  half of staffing.

## Phase 2 — make the day legible (the biggest visible win)

On the runsheet, per column and per day:

- **Capacity meter** on each column header: `booked + travel = 6.5h of 8h`,
  green → amber → red. Idle minutes are wastage made visible.
- **Sequence warnings** on tiles: "18 min drive from #402 — only 10 min gap".
- **Load/install pairing check**: tomorrow's installs whose load isn't
  placed today show up as a banner *today*, when there's still time to fix it.
- **People math**: a day needs N people across its crews; the roster has M
  available. Show the difference before it becomes a 6 am phone call.
- **A daily wastage number** (idle person-minutes), tracked over weeks. This
  is the metric that tells us whether any of this is working.

None of this moves a tile. It makes the cost of the current plan visible,
which is most of the value of "optimisation" at this scale.

## Phase 3 — suggestions with reasons

With durations, travel and sizes trustworthy:

- **Slot proposals.** For each unscheduled tray job, compute the placements
  that fit: right truck size, crew size adequate for the job's size band,
  travel gaps respected, load slot available the day before. Rank by least
  added travel + best balance across columns. Show the top option on the
  tray tile — "fits Nemo 10:00 after #417, 12 min drive" — one tap to apply.
- **Crew-size rule, explicit and editable.** e.g. items ≥ 40 or 4-bed+ → 2
  people; small pickup → 1; two trucks when volume proxy exceeds one truck's
  capacity. Rules live in a settings table the scheduler can change, not in
  code.
- **"Plan tomorrow" button.** Runs the same proposal logic across all
  unscheduled jobs for a date and presents a draft day — every placement
  with its reason — to accept, adjust or discard as a whole.

## Phase 4 — a real optimiser, only if Phase 3 leaves value on the table

The formal problem is vehicle routing with time windows plus crew sizing.
At LUMA's scale (≈3 trucks, ≈8 people, a dozen tasks a day) Google OR-Tools
solves this in seconds, and could co-plan load day and install day together —
the coupling greedy suggestions handle least well. But it's a black box to
the person using it, and if the durations feeding it are wrong it is
*confidently* wrong. It earns its place only if the wastage number plateaus
under Phase 3.

## Hard rules the mechanism must never break

1. A day runs 07:30–15:30; nothing scheduled to finish after 15:30.
2. An install's stock must be on a truck (or explicitly a transfer) by the
   evening before.
3. Travel time between consecutive jobs in a column must fit the gap.
4. Crew size ≥ the job's minimum for its size band.
5. A truck is one place at a time; a person is on one crew per day.
6. Every automatic action is visible, attributed and reversible.

## Suggested order

| Step | Effort | Depends on |
|---|---|---|
| Done button + actuals | small | nothing |
| Availability table | small | nothing |
| Duration model + popover pre-fill | small | actuals accruing |
| Travel matrix + sequence warnings | medium | nothing |
| Capacity meters + wastage number | medium | travel matrix |
| Load/install pairing banner | small | nothing |
| Slot proposals on tray tiles | medium | all above |
| Plan-tomorrow draft | medium | proposals |
| OR-Tools solver | large | proof the rest plateaus |

The first three rows can start immediately and each is useful alone. I'd
begin with the Done button — every week without it is a week of training
data lost.
