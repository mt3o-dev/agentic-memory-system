# Research Approach — How This Design Was Produced

This document is about *method*, not content. The design in the Linear tickets didn't come from one person's opinion — it came from a repeatable process. If you continue the design work (and there are open questions that need it), **use this same process** so the new work is consistent with the old and holds up to the same scrutiny.

---

## The core loop

Every non-trivial design decision went through this:

1. **Frame the actual question.** Strip the jargon, state the real tension in plain words. ("Should retrieval start from one seed or many?" not "optimize the seed-selection topology.") If you can't say it plainly, you don't understand it yet.
2. **Research from multiple angles ("lenses").** For each question, attack it from 3 genuinely different disciplines — not 3 flavors of the same take. E.g. the tier model was researched through *cognitive science*, *cache/systems engineering*, and *reinforcement learning* simultaneously. The lenses must be able to *disagree*, or they're not independent.
3. **Ground each lens in real sources.** Web search for current practice + papers, combined with first-principles reasoning. Cite what's measured vs what's reasoned. Believe surprising-but-sourced findings (e.g. "Kuzu was archived"); be skeptical of consensus-by-repetition.
4. **Converge to 2–4 concrete solutions**, not one. Name the tradeoffs each makes. State a recommendation, but preserve the alternatives.
5. **Stress-test before accepting.** Try to break the leading candidate with a concrete scenario. (The liveness model only survived because someone asked "what happens to a *foundation* when its slices archive?" — which broke the naive version and forced the GC-based fix.)
6. **Record decision *and* reasoning in Linear**, immediately — as a comment on the relevant ticket. Not just what was decided; *why*, and what was rejected. Open questions get recorded alongside conclusions, never papered over.

---

## Principles that kept the work honest

- **Separate orthogonal axes.** The single most productive move in this whole design was noticing when one "thing" was secretly two (salience vs liveness; tier vs type; content-retrieval vs provenance-query). When a mechanism feels like it's straining to do two jobs, split the axis. This happened repeatedly and resolved each time.
- **Prefer config dials to binaries.** Hard either/or choices repeatedly turned out to be a tunable parameter with a sensible default. When facing a binary, ask "is this actually a continuous knob?" Often yes (seed weights, penalty strength, facet count).
- **Derived, not stored.** Default to computing values from an authoritative log, with stored values as caches. This proved right for liveness, effective_score, trust, and dates. If you're about to add a stored column for something that *changes*, check whether it should be derived instead.
- **Drop ideas cleanly when the argument is clear.** The averaging idea for trust, the per-bundle ID alias, treating Procedure as just-another-node — all proposed, all dropped once argued through, with no lingering attachment. Being wrong fast is part of the method. Record *why* it was dropped (it's often as useful as the decision kept).
- **Adopt borrowed frameworks only where they change behavior.** The cognitive-science memory taxonomy was adopted for episodic/procedural (where dynamics genuinely differ) and *not* used to rename things that already worked. Borrowing a model because it's "real elsewhere" isn't the same as it being right here. Make each borrowed distinction earn its keep.
- **Let the design eat its own dogfood.** The normative constraints meant to steer a consuming agent (goal-first, vertical slices, don't over-engineer) also apply to building this project. If the process feels like it's over-engineering, that's the design working as intended — listen to it.

---

## What "good" looks like in a ticket

A well-worked ticket (see `MT3-20`, `MT3-18`, `MT3-23` as exemplars) has:
- An issue body stating the problem.
- One or more comments, each: the lenses researched, the sources, 2–4 solutions with tradeoffs, a recommendation, **and the open questions that remain**.
- Cross-references to the tickets it affects, and back-propagated updates when a decision here changes a decision there.
- Decisions marked as **locked** vs **leaning** vs **open**, explicitly.

When body and a later comment disagree, the comment wins (it's newer). The history is intentional — don't flatten it.

---

## How to continue the open work

For each open question in `03_NEXT_STEPS.md`:
1. Run the core loop above. Don't shortcut to an answer.
2. If it's a *config value*, don't over-research — ship a sensible default behind an interface and mark it "tune in practice."
3. If it's a *genuine fork*, produce the 2–4 solutions and surface the decision to the project owner rather than deciding unilaterally — several open items are deliberately left for a human call.
4. If implementing a slice *answers* an open question (often the case), record the empirical answer back to the design ticket. Implementation is a research instrument, not just execution.

---

## A note on scope discipline

The strongest recurring failure mode for a system like this is over-building. Two guards, both already in the method:
- **The wiki test.** If a proposed feature is mainly about humans browsing/editing the store comfortably, question it — that's drift toward reinventing Confluence, and the system loses its agent-first reason to exist. Keep the human layer thin.
- **The tracer-bullet rule.** Always have a thin end-to-end thing working before enriching any one layer. If you've spent days on a layer with nothing running, stop.
