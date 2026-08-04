---
name: pricing-unit-economics-report
description: Design subscription tiers (Free/Pro/etc.) and their price points for a product whose marginal cost comes from metered API calls -- LLM tokens, image or video generation, any per-unit AI/cloud cost -- then produce a polished report showing the math. Use this whenever the user wants to set or revisit subscription prices, add a new tier, decide usage limits, check whether a tariff is profitable, or asks things like "what should we charge", "are we losing money on free users", "how many videos/generations should this plan include", or "make a doc/report on our pricing" -- even if they don't use the words "unit economics" or "pricing model" explicitly. Also trigger when the user gives a target margin or price and asks you to reverse-engineer sensible limits from it.
---

# Pricing & unit-economics report

Turns a table of per-unit AI costs into subscription tiers with real numbers behind
them, then writes those numbers up as a report someone (a cofounder, an investor) can
actually read. The output is always two things: a small set of tier limits + prices,
and a document explaining why those numbers are safe.

## Why this needs its own process

A tier built on a single "average user" cost estimate looks fine until a real user
maxes out every limit every month -- then the average was a fiction and the tier was
losing money the whole time. The core discipline here is modeling a **light, average,
and heavy** user for every tier, not just one, so a decision holds up under the
users who actually use what they paid for.

The other trap is stale or guessed prices. Model pricing for LLM/image/video APIs
changes often and sometimes a model gets renamed or deprecated with weeks of notice.
A pricing document built on remembered numbers can be wrong by the time anyone reads
it -- worse, it can be *silently* wrong, since nothing about a spreadsheet says "this
number might be six months old." Always re-fetch current pricing from the vendor's
own pricing page for whatever models are actually in use, right before doing the
math, even if you're confident you already know the number.

## The process

### 1. Get real per-unit costs

For every content type / format the product generates (text, image, video, whatever
applies), find the *current* price per unit from the vendor's own docs -- WebFetch the
official pricing page, don't rely on training memory. If the product's code already
calls a specific model (check for a config file, a `MODEL` constant, an API client),
use that exact model's price, not a similar-sounding one.

While you're there, sanity-check that the model ID the code actually uses still
exists and isn't deprecated -- pricing pages often double as the most current model
list, and catching a stale/wrong model ID here is a common and valuable side-effect
of this step. If the code and the vendor's docs disagree, say so before proceeding;
don't quietly paper over a mismatch by picking whichever number is convenient.

Convert to a **cost per generation**, not just a raw per-token or per-second price --
that requires a volume assumption (e.g. "~300 input / 200 output tokens for a typical
post", "8 seconds per video clip"). State that assumption plainly in the output; it's
usually the single most guessable number in the whole model, and the first thing
worth re-measuring once there's real traffic.

### 2. Design each tier backward from a target margin, not forward from a guess

Don't pick limits first and see what margin falls out -- start from what margin the
tier needs (the user will often tell you this directly, e.g. "keep it above 50%" or
"I'm fine with 30-40% on this one if it means way more video"), then solve for the
limits that hit it. The lever is usually the most expensive format (typically video):
raise or lower that one number until the heavy-usage margin lands where it should,
then set the cheaper formats generously since they barely move the number.

A cheap way to sanity-check a candidate limit set is `scripts/tier_economics.py`
(see below) -- iterate on the numbers there before committing to them in prose.

### 3. Model light / average / heavy usage for every paid tier

For each tier, compute the scenario at roughly 30%, 50%, and 100% of the tier's
limits (adjust the fractions if the user has a different sense of real usage
patterns). The heavy scenario is the one that matters most for solvency -- it's the
floor, not an edge case, especially for tiers explicitly marketed around a specific
heavy-use format (a "lots of video" tier will disproportionately attract people who
actually use lots of video).

Include the payment processor's fee in every scenario, not just AI cost -- a 2.9% +
$0.30-style fee is a reasonable placeholder if the processor isn't chosen yet, but
say so explicitly as an assumption.

### 4. Roll up into a portfolio view and a breakeven number

Two numbers make the report land for a business audience:

- **Portfolio projection**: pick a total-user count and a conversion rate (use the
  product's own target metric if one exists), split paying users across tiers by a
  stated (clearly-labeled-as-assumed) mix, and show the net monthly result. This is
  where a low-margin-but-high-price tier proves its worth -- it often contributes more
  *absolute* profit than a high-margin-but-low-price tier despite the lower
  percentage, which is worth calling out explicitly since the margin-% framing alone
  makes it look worse than it is.
- **Breakeven conversion rate**: given one paying user's average-scenario profit and
  one free user's cost, solve for the conversion rate at which free-tier cost is
  exactly offset. Compare it to the product's actual target conversion rate as a
  margin-of-safety check.

Both of these come straight out of `scripts/tier_economics.py --json` if you feed it
a `portfolio` block -- no need to hand-compute them.

### 5. Write it up

Read the `artifact-design` skill before building the document -- this is a real
report, not a throwaway table, and it's often shown to cofounders or investors, so it
deserves actual typographic care. That said, treat it as a **utilitarian** document
(polished, real hierarchy, restrained), not an editorial one: the content is the
point, not a hero section.

A structure that has worked well:

1. **Summary** — the headline price(s) and the one or two numbers a reader would
   ask for first (margin at the worst case, breakeven conversion).
2. **Cost per unit** — the raw numbers from step 1, with the vendor source and the
   volume assumption stated plainly.
3. **Free tier** — what it costs per user per month, and the reasoning for why each
   format is or isn't included free (the expensive format is almost always excluded
   or heavily capped).
4. **One section per paid tier** — the light/average/heavy scenario, presented so the
   reader can see all three at a glance (three cards side by side reads better than
   three separate paragraphs).
5. **Portfolio projection** and **breakeven** — the roll-up numbers from step 4.
6. **Open items** — anything discovered along the way that the numbers depend on but
   isn't resolved yet (an unconfirmed model ID, an unpicked payment processor, an
   assumption that needs real traffic to validate). Don't bury these in a footnote --
   a business decision built on an unstated assumption is the kind of thing that
   causes real surprise later.

Favor a numeric, ledger-like presentation for the figures themselves -- tabular
numbers, right-aligned columns, a monospace face for the digits reads as "these are
real, checkable numbers" in a way that plain prose doesn't. But choose the actual
palette and type pairing fresh for the product being reported on, per
`artifact-design` — don't reuse a previous report's colors just because it's the same
kind of document.

If this report updates numbers from an earlier version of itself, republish to the
same artifact file path (`Artifact` with the same `file_path` reuses the URL) rather
than minting a new link, so anyone with the old link still lands on current numbers.

## Bundled calculator

`scripts/tier_economics.py` takes a JSON spec and computes every number in steps 2-4
above -- run `python scripts/tier_economics.py --example` to see the exact shape (cost
per unit, one or more paid tiers with limits, a free tier, an optional portfolio
block). It prints a readable table by default; pass `--json` for machine-readable
output to pull numbers from while writing the report. Use it to iterate on candidate
limits quickly rather than re-deriving the arithmetic by hand each time -- it's the
same formulas either way, but the script won't drop a term or round inconsistently
partway through a long session.
