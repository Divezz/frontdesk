# FRONTDESK

After-hours enquiry handling for small businesses.

Most enquiries to a small business arrive when nobody's there to answer them —
evenings, Sundays, the hour after closing. They sit in a contact form until
Monday, and a good share of them go somewhere else in the meantime.

FRONTDESK answers them from the business's own information, and refuses to
answer from anything else.

**[Try the demo →](https://divezz.github.io/frontdesk/)** · runs entirely in
your browser, no server, no API key.

```
$ python build.py --interview
==============================================================
  FRONTDESK — new business
  Nine questions. Blank line moves you on.
==============================================================

Business name
  Northside Barbers
...
→ drafting knowledge base

✓ Northside Barbers — 9 entries
  demos/northside-barbers.html
```

## Two views, one engine

The demo has a switch in the corner.

**Shopfront** is what the business's customers see — quiet, warm, nothing that
looks like software.

**Under the hood** shows the same conversation with the retrieval trace beside
it: every candidate entry, its score, whether it cleared the confidence
threshold, and how long the match took.

That switch is the argument. Owners don't distrust chatbots in the abstract —
they distrust them because they've seen one quote a price that wasn't real.
Showing the scoring makes "it can't make things up" inspectable rather than a
promise.

## It stops when it doesn't know

Below the confidence threshold there's no improvisation. It says so, takes the
customer's details, and logs the question to `unanswered.jsonl`. That log is the
product's second life: every month it's a list of exactly what customers asked
that the business has never put in writing.

## Building one for a business

Most small businesses don't have a website worth scraping, so there are three
ways in:

```bash
python build.py --url https://someshop.co.uk   # they have a site
python build.py --notes their-listing.txt      # Google listing, Instagram bio, price list
python build.py --interview                    # nine questions, five minutes
```

All three feed the same drafting step, which is instructed to use only facts
present in the source. No prices given, no pricing entry.

## Files

| File | What it is |
|---|---|
| `index.html` | The demo. Self-contained: no server, no API key, no internet. The matching engine in it is the real one, running client-side. |
| `build.py` | Builds a demo for a specific business from a website, notes, or an interview. Standard library only, plus `anthropic`. |
| `server.py` | Production backend: FastAPI + Claude, retrieval over the knowledge base, lead capture, unanswered logging. |
| `knowledge.json` | A business's information as retrievable chunks — the only file that changes per client. |

## Retrieval

IDF-weighted keyword matching, not embeddings. A small business has 10–40
knowledge entries; at that size keyword scoring is just as accurate, costs
nothing, adds no vector database, and — the deciding factor — stays legible when
the owner asks why it said what it said. Multi-word keys ("how much", "get in")
score higher than single tokens because they're less ambiguous; near-stems
("booking" ≈ "book") score lower but still count.

Swap in embeddings if a knowledge base ever passes a few hundred entries.
Nothing else has to change.

## Running the live version

```bash
pip install fastapi uvicorn anthropic
export ANTHROPIC_API_KEY=sk-ant-...
export BUSINESS_NAME="Northside Barbers"
uvicorn server:app --reload

curl -X POST localhost:8000/chat -H 'content-type: application/json' \
     -d '{"message":"how much is a skin fade?"}'
```

Retrieval keeps each exchange to roughly 700–1,200 tokens, so a typical small
business runs at a few pounds a month.

## Crawler etiquette

`build.py --url` reads robots.txt and honours it, identifies itself in the
user-agent, caps itself at six pages, and waits between requests. It's reading a
stranger's site in order to build them something.

---

Built by Daniel · Manchester
