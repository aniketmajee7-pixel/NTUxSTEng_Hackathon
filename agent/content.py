"""Content layer: coherent office prose and a shared WorldState (stdlib only).

Fixes two audit findings: template sentences repeated across a session (58-77% duplicate
n-grams) and documents unrelated to what was browsed. Prose comes from a slot grammar; the
WorldState carries facts picked up while browsing so the Calc sheet and Writer memo are about
the same thing (a Chimera-style realism metric). All in memory: python.exe must not read files.
"""
import random
import re

# A Tracery-style slot grammar (an order-2 Markov over a tiny corpus just reproduces it):
# coherent office prose with high distinct-n and no copied spans. {symbols} expand recursively.
# Each verb has its own object list so every expansion is grammatical ("the draft still needs
# a second look", never "the figure came in what we expected").
_GRAMMAR = {
    "sentence": [
        "{opener}, {clause}.",
        "{clause}, {tail}.",
        "{opener}, {clause}, {tail}.",
        "{clause}. {followup}.",
        "{opener}, {clause} and {clause2}.",
    ],
    "opener": [
        "Following up on {topic}", "Quick note on {topic}", "On {topic}", "Regarding {topic}",
        "After the call about {topic}", "Just circling back on {topic}", "As discussed, on {topic}",
        "Having looked at {topic} again", "For {topic}",
    ],
    "topic": [
        "the vendor quotes", "the Q4 budget", "the migration plan", "the training schedule",
        "the procurement request", "the shortlist", "the delivery dates", "the pilot results",
        "the headcount figures", "the licence counts", "next week's review", "the offsite",
    ],
    "clause": ["{subject} {pred}", "{subject} {pred}", "I {ithink} {subject2} {pred}"],
    "clause2": ["{subject2} {pred}", "the rest can wait until {day}"],
    "subject": ["the budget line", "the draft", "the timeline", "the proposal", "the cheaper option",
                "the latest quote", "the schedule", "the report", "the estimate"],
    "subject2": ["it", "the total", "that option", "the plan"],
    "pred": ["still needs {need}", "looks {state}", "came in {cmp}", "depends on {dep}",
             "lines up with {ref}", "should cover {cover}"],
    "need": ["sign-off from finance", "a second look", "one more check against the invoice",
             "a few more inputs from the team"],
    "state": ["fine for now", "about right", "a little high", "better than expected",
              "tight but workable"],
    "cmp": ["under budget", "slightly over budget", "close to the earlier estimate",
            "higher than we expected", "lower than last quarter"],
    "dep": ["the last quote", "sign-off from finance", "the delivery dates",
            "what procurement says", "how many units we order"],
    "ref": ["the earlier estimate", "our requirements", "the agreed scope", "the other quotes"],
    "cover": ["the whole team", "what we need this quarter", "the first phase",
              "most of the requirements"],
    "ithink": ["think", "reckon", "suspect", "would say"],
    "tail": ["so it is worth a check", "but nothing urgent", "which is good news",
             "though the timing is tight", "assuming the price holds", "if that still works",
             "pending the last quote", "so we can decide {when}"],
    "followup": ["I'll refresh the sheet when the rest arrive", "Shout if anything looks off",
                 "Happy to go through it {when}", "Flagging it now so nobody is caught out",
                 "We can tidy up the totals after the call", "That's all from me for now"],
    "when": ["today", "tomorrow", "this week", "by Friday", "before the review", "next week"],
    "day": ["tomorrow", "Friday", "next week", "after the review"],
}


def _expand(grammar, symbol, rng, depth=0):
    prod = rng.choice(grammar[symbol])
    if depth > 8:
        prod = grammar[symbol][0]
    return re.sub(r"\{(\w+)\}", lambda m: _expand(grammar, m.group(1), rng, depth + 1), prod)


def sentence(rng=random):
    """Generate one coherent office sentence from the slot grammar (no copied source spans)."""
    s = _expand(_GRAMMAR, "sentence", rng)
    return s[0].upper() + s[1:]


def fresh(make, seen, tries=4):
    """make() until it gives a sentence not in `seen` (a few tries, then accept a repeat),
    and record it. `make` may return None (nothing to say): that passes straight through."""
    s = make()
    for _ in range(tries):
        if s is None or s not in seen:
            break
        s = make()
    if s is not None:
        seen.add(s)
    return s


def world_sentence(world, rng=random):
    """A sentence grounded in what this session actually did (sites browsed, files saved,
    models noted), so memos cohere with the browsing; None if there is nothing to say yet."""
    options = []
    if world.visited:
        shop = rng.choice(world.visited)
        options.append(f"I had another look at the {shop} site earlier and nothing has changed "
                       f"on pricing.")
    sheets = [f for f in world.files if "comparison" in f.lower()]    # not the notes or a draft
    if sheets:
        options.append(f'The figures are in "{rng.choice(sheets)}" if you want the detail.')
    if world.findings:
        shop, model, price = rng.choice(world.findings)
        options.append(f"For reference, the {model} from {shop} was listed at S${price:,}.")
    if world.budget:
        options.append(f"Everything here assumes the S${world.budget:,} budget still stands.")
    if world.terms:
        shop, term = rng.choice(sorted(world.terms.items()))
        options.append(f"The {shop} homepage was pushing {term} when I checked.")
    if world.downloads:
        options.append("I also saved a copy of one of the shop pages to Downloads for reference.")
    return rng.choice(options) if options else None


_TODO = {
    "todo": ["{verb} {thing} {when}", "{verb} {thing}", "ask {who} about {thing}"],
    "verb": ["follow up on", "chase", "double-check", "send round", "book time for", "update"],
    "thing": ["the vendor quotes", "the delivery dates", "the Q4 budget", "the licence renewal",
              "the training slots", "the room booking", "the asset list", "the invoice"],
    "who": ["finance", "procurement", "IT", "the team lead"],
    "when": ["today", "tomorrow", "before Friday", "this week", "after lunch"],
}


def todo_line(rng=random):
    """A short to-do line for the scratch notes, e.g. '- chase the vendor quotes tomorrow'."""
    return "- " + _expand(_TODO, "todo", rng)


class WorldState:
    """What the worker 'knows' this session: a name, facts gathered while browsing, files saved.

    Lets the Writer memo reference the exact models and prices the Calc sheet lists and the
    shops actually visited.
    """

    _NAMES = ["Alex", "Sam", "Jordan", "Priya", "Wei", "Daniel", "Mei", "Raj"]

    def __init__(self, rng=random):
        self.name = rng.choice(self._NAMES)
        self.visited = []          # shop names actually browsed, in order
        self.findings = []         # (shop, model, price)
        self.budget = None
        self.files = []            # filenames saved this session
        self.terms = {}            # shop -> a word actually copied off its homepage
        self.downloads = []        # pages saved from Chrome (their saved names)

    def visit(self, shop):
        if shop not in self.visited:
            self.visited.append(shop)

    def note(self, shop, model, price):
        self.findings.append((shop, model, price))

    def saved(self, name):
        self.files.append(name)
