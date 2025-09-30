# nl_to_sparql_wikidata_demo.py
# Demo prototype: map simple English questions -> SPARQL -> Wikidata
# This project demonstrates a minimal NL->SPARQL pipeline:
# 1) detect intent, 2) extract entity text, 3) link to QID (wbsearchentities + P31 checks),
# 4) fill SPARQL template, 5) run query and return readable answers.

import re
import requests
import time

# API endpoints and client headers
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"
HEADERS = {"User-Agent": "nl2sparql-demo/1.0 (you@example.com)"}
REQUEST_SLEEP = 0.05  # small polite pause between API calls

# SPARQL templates for supported intents. I use {QID} as placeholder.
TEMPLATES = {
    "capital": "SELECT ?answer ?answerLabel WHERE { wd:{QID} wdt:P36 ?answer . SERVICE wikibase:label { bd:serviceParam wikibase:language \"en\". } } LIMIT 10",
    "continent": "SELECT ?answer ?answerLabel WHERE { wd:{QID} wdt:P30 ?answer . SERVICE wikibase:label { bd:serviceParam wikibase:language \"en\". } } LIMIT 10",
    "population_latest": (
        "SELECT ?population ?point WHERE { wd:{QID} p:P1082 ?ps . ?ps ps:P1082 ?population ."
        " OPTIONAL { ?ps pq:P585 ?point. } } ORDER BY DESC(?point) LIMIT 1"
    ),
    "head_of_state": "SELECT ?answer ?answerLabel WHERE { wd:{QID} wdt:P35 ?answer . SERVICE wikibase:label { bd:serviceParam wikibase:language \"en\". } } LIMIT 10",
    "contains_admin": "SELECT ?answer ?answerLabel WHERE { wd:{QID} wdt:P150 ?answer . SERVICE wikibase:label { bd:serviceParam wikibase:language \"en\". } } LIMIT 10",
}

# Regex patterns to detect intent from input questions.
INTENT_PATTERNS = [
    (re.compile(r"\bcapital of\b|\bwhat is the capital of\b", re.I), "capital"),
    (re.compile(r"\bpopulation of\b|\bhow many people\b", re.I), "population_latest"),
    (re.compile(r"\bhead of state\b|\bpresident of\b|\bprime minister of\b", re.I), "head_of_state"),
    (re.compile(r"\bcontinent of\b|\bwhich continent\b|\bin continent\b", re.I), "continent"),
    (re.compile(r"\bcontains\b|\bwhich states\b|\bwhich provinces\b|\badministrative entities\b", re.I), "contains_admin"),
]

# Keywords used to check P31 (instance-of) labels or candidate descriptions.
# This prefers candidates of the expected type (country vs state vs city).
INTENT_TYPE_KEYWORDS = {
    "capital": ["country", "sovereign state", "nation"],
    "continent": ["country", "sovereign state", "nation"],
    "population_latest": ["country", "sovereign state", "nation", "city"],
    "head_of_state": ["country", "sovereign state", "nation"],
    "contains_admin": ["country", "administrative territorial entity", "state", "province", "region"],
}

def detect_intent(text):
    """Return intent key for a question or None if unknown."""
    for pat, intent in INTENT_PATTERNS:
        if pat.search(text):
            return intent
    return None

def search_entity_candidates(label, language="en", limit=5):
    """Call wbsearchentities and return top candidate dicts (label, id, description)."""
    params = {
        "action": "wbsearchentities",
        "format": "json",
        "language": language,
        "search": label,
        "limit": limit
    }
    r = requests.get(WIKIDATA_API, params=params, headers=HEADERS, timeout=10)
    r.raise_for_status()
    time.sleep(REQUEST_SLEEP)
    return r.json().get("search", [])

def wbgetentities(ids, props="labels|claims", languages="en"):
    """Wrapper for wbgetentities. Accepts single id or list of ids."""
    params = {
        "action": "wbgetentities",
        "format": "json",
        "ids": "|".join(ids) if isinstance(ids, (list, tuple)) else ids,
        "props": props,
        "languages": languages
    }
    r = requests.get(WIKIDATA_API, params=params, headers=HEADERS, timeout=10)
    r.raise_for_status()
    time.sleep(REQUEST_SLEEP)
    return r.json().get("entities", {})

def choose_entity_candidate_strict(candidates, intent, target_label_lower):
    """
    Select best candidate QID from wbsearchentities results.
    Order:
      1) exact label match
      2) description contains intent keywords
      3) inspect P31 (instance of) labels with priority (sovereign state/country first)
      4) fallback to top candidate
    """
    if not candidates:
        return None
    # 1) exact label match
    for c in candidates:
        if (c.get("label") or "").strip().lower() == target_label_lower:
            return c["id"]
    # 2) description-based hint
    keywords = INTENT_TYPE_KEYWORDS.get(intent, [])
    for c in candidates:
        desc = (c.get("description") or "").lower()
        for kw in keywords:
            if kw in desc:
                return c["id"]
    # 3) strict P31 checks with priority ordering
    priority = ["sovereign state", "country", "nation", "state", "province", "city", "administrative territorial entity"]
    for kw in priority:
        for c in candidates:
            cid = c.get("id")
            try:
                entities = wbgetentities(cid, props="claims", languages="en")
                ent = entities.get(cid, {})
                claims = ent.get("claims", {})
                p31 = claims.get("P31", [])
                if not p31:
                    continue
                # collect target ids from P31 claims
                target_ids = []
                for claim in p31:
                    mainsnak = claim.get("mainsnak", {})
                    datavalue = mainsnak.get("datavalue", {})
                    if datavalue.get("type") == "wikibase-entityid":
                        targ = datavalue["value"].get("id")
                        if targ:
                            target_ids.append(targ)
                if not target_ids:
                    continue
                # fetch labels for P31 targets and look for priority keyword
                target_entities = wbgetentities(target_ids, props="labels", languages="en")
                for tid, tdata in target_entities.items():
                    label = (tdata.get("labels", {}).get("en", {}).get("value") or "").lower()
                    if kw in label:
                        return cid
            except Exception:
                # ignore transient API issues and try next candidate
                continue
    # 4) fallback to top candidate
    return candidates[0]["id"]

def search_entity(label, intent):
    """Top-level entity resolver returning one QID using strict selection."""
    candidates = search_entity_candidates(label)
    target_label_lower = label.strip().lower()
    qid = choose_entity_candidate_strict(candidates, intent, target_label_lower)
    return qid

def run_sparql(sparql):
    """Execute a SPARQL query at WDQS and return parsed JSON."""
    r = requests.get(WIKIDATA_SPARQL, params={"query": sparql}, headers={**HEADERS, "Accept": "application/sparql-results+json"}, timeout=20)
    r.raise_for_status()
    return r.json()

def extract_entity_label(nl):
    """
    Extract entity text from question using ordered regex patterns.
    Fallback: return last two meaningful words.
    """
    nl = nl.strip()
    patterns = [
        r"of\s+(.+?)[\?\.\!]?$",
        r"which\s+continent\s+is\s+(.+?)\s+in[\?\.\!]?$",
        r"which\s+.*\s+does\s+(.+?)\s+contain[\?\.\!]?$",
        r"does\s+(.+?)\s+contain[\?\.\!]?$",
        r"in\s+(.+?)[\?\.\!]?$",
        r"is\s+(.+?)\s+in[\?\.\!]?$",
        r"who is the .* of\s+(.+?)[\?\.\!]?$"
    ]
    for p in patterns:
        m = re.search(p, nl, re.I)
        if m:
            label = m.group(1).strip()
            # drop trailing verb leftovers like 'contain'
            label = re.sub(r"\b(contains|contain|contain\?|contain\.)\b$", "", label, flags=re.I).strip()
            return label
    parts = re.findall(r"[A-Za-z0-9\u00C0-\u024F]+", nl)
    if not parts:
        return nl
    return " ".join(parts[-2:])

def nl_to_sparql_run(nl_text):
    """
    Main pipeline function:
    - detect intent
    - extract entity label
    - get candidate QIDs and pick best with type-checking
    - fill template, run SPARQL, parse answers
    - if no answers return candidates for manual inspection
    """
    nl = nl_text.strip()
    intent = detect_intent(nl)
    if not intent:
        return {"error": "Intent not recognized. Add more templates."}

    entity_label = extract_entity_label(nl)

    # keep candidates for debugging/fallback
    candidates = search_entity_candidates(entity_label)

    # strict selection (may perform P31 checks)
    qid = choose_entity_candidate_strict(candidates, intent, entity_label.strip().lower())
    if not qid:
        return {"error": f"Entity not found for '{entity_label}'", "candidates": candidates}

    # build and run SPARQL
    sparql = TEMPLATES[intent].replace("{QID}", qid)
    resp = run_sparql(sparql)
    bindings = resp.get("results", {}).get("bindings", [])

    # parse results into a simple answers list
    answers = []
    for b in bindings:
        if "answerLabel" in b:
            answers.append(b["answerLabel"]["value"])
        elif "answer" in b and "value" in b["answer"]:
            answers.append(b["answer"]["value"])
        elif "population" in b:
            val = b["population"]["value"]
            pt = b.get("point", {}).get("value")
            answers.append({"population": val, "point": pt})

    # if query returned nothing, expose candidates so user can pick another QID
    if not answers:
        return {
            "intent": intent,
            "entity_label": entity_label,
            "qid": qid,
            "sparql": sparql,
            "answers": answers,
            "candidates": [{"id": c.get("id"), "label": c.get("label"), "description": c.get("description")} for c in candidates]
        }

    return {"intent": intent, "entity_label": entity_label, "qid": qid, "sparql": sparql, "answers": answers}

# ------------ runnable demo examples ------------
if __name__ == "__main__":
    examples = [
        "What is the capital of France?",
        "What is the population of India?",
        "Who is the president of France?",
        "Which continent is Japan in?",
        "Which administrative entities does India contain?",
        "What is the capital of Georgia?",
        "What is the capital of Springfield?"
    ]
    for q in examples:
        print("Q:", q)
        try:
            out = nl_to_sparql_run(q)
            print(out)
        except Exception as e:
            print("ERROR:", e)
        print("-" * 40)