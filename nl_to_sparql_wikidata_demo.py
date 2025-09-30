# nl_to_sparql_wikidata_demo.py
# Requires: pip install requests
import re
import requests
import time

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"
HEADERS = {"User-Agent": "nl2sparql-demo/1.0 (you@example.com)"}
REQUEST_SLEEP = 0.05  # throttle between API requests

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

INTENT_PATTERNS = [
    (re.compile(r"\bcapital of\b|\bwhat is the capital of\b", re.I), "capital"),
    (re.compile(r"\bpopulation of\b|\bhow many people\b", re.I), "population_latest"),
    (re.compile(r"\bhead of state\b|\bpresident of\b|\bprime minister of\b", re.I), "head_of_state"),
    (re.compile(r"\bcontinent of\b|\bwhich continent\b|\bin continent\b", re.I), "continent"),
    (re.compile(r"\bcontains\b|\bwhich states\b|\bwhich provinces\b|\badministrative entities\b", re.I), "contains_admin"),
]

# intent -> type keywords used when inspecting descriptions or P31 labels
INTENT_TYPE_KEYWORDS = {
    "capital": ["country", "sovereign state", "nation"],
    "continent": ["country", "sovereign state", "nation"],
    "population_latest": ["country", "sovereign state", "nation", "city"],
    "head_of_state": ["country", "sovereign state", "nation"],
    "contains_admin": ["country", "administrative territorial entity", "state", "province", "region"],
}

def detect_intent(text):
    for pat, intent in INTENT_PATTERNS:
        if pat.search(text):
            return intent
    return None

def search_entity_candidates(label, language="en", limit=5):
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
    Selection order:
      1) exact label match
      2) description contains intent keywords
      3) P31 labels checked with priority (sovereign state/country first)
      4) fallback top candidate
    """
    if not candidates:
        return None
    # 1) exact label match
    for c in candidates:
        if (c.get("label") or "").strip().lower() == target_label_lower:
            return c["id"]
    # 2) description hints
    keywords = INTENT_TYPE_KEYWORDS.get(intent, [])
    for c in candidates:
        desc = (c.get("description") or "").lower()
        for kw in keywords:
            if kw in desc:
                return c["id"]
    # 3) strict P31 inspection with priority ordering
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
                target_entities = wbgetentities(target_ids, props="labels", languages="en")
                for tid, tdata in target_entities.items():
                    label = (tdata.get("labels", {}).get("en", {}).get("value") or "").lower()
                    if kw in label:
                        return cid
            except Exception:
                continue
    # 4) fallback
    return candidates[0]["id"]

def search_entity(label, intent):
    candidates = search_entity_candidates(label)
    target_label_lower = label.strip().lower()
    qid = choose_entity_candidate_strict(candidates, intent, target_label_lower)
    return qid

def run_sparql(sparql):
    r = requests.get(WIKIDATA_SPARQL, params={"query": sparql}, headers={**HEADERS, "Accept": "application/sparql-results+json"}, timeout=20)
    r.raise_for_status()
    return r.json()

def extract_entity_label(nl):
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
            label = re.sub(r"\b(contains|contain|contain\?|contain\.)\b$", "", label, flags=re.I).strip()
            return label
    parts = re.findall(r"[A-Za-z0-9\u00C0-\u024F]+", nl)
    if not parts:
        return nl
    return " ".join(parts[-2:])

def nl_to_sparql_run(nl_text):
    nl = nl_text.strip()
    intent = detect_intent(nl)
    if not intent:
        return {"error": "Intent not recognized. Add more templates."}
    entity_label = extract_entity_label(nl)
    # get candidates for debugging/fallback
    candidates = search_entity_candidates(entity_label)
    qid = choose_entity_candidate_strict(candidates, intent, entity_label.strip().lower())
    if not qid:
        return {"error": f"Entity not found for '{entity_label}'", "candidates": candidates}
    sparql = TEMPLATES[intent].replace("{QID}", qid)
    resp = run_sparql(sparql)
    bindings = resp.get("results", {}).get("bindings", [])
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