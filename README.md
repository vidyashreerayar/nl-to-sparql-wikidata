# NL-to-SPARQL over Wikidata (Demo Prototype)

This project is a **demo pipeline** that converts natural language questions into SPARQL queries and executes them against the [Wikidata Query Service](https://query.wikidata.org/).

## Overview

- Input: A natural language question (e.g. *"What is the capital of France?"*).  
- Processing:
  1. **Intent detection** – simple regex-based classification maps the question to one of a few supported query types.
  2. **Entity extraction** – extracts the main entity label (e.g. *France*, *India*).
  3. **Entity linking** – queries Wikidata API (`wbsearchentities`) for candidate QIDs and applies type checks using `P31` (*instance of*) to prefer correct types (e.g. country vs U.S. state).
  4. **SPARQL generation** – fills a template query with the resolved QID.
  5. **Execution** – sends the SPARQL query to the Wikidata endpoint and parses the JSON response.
- Output: Human-readable answer(s).

## Supported Question Types

- Capital of a country (`P36`)  
- Continent of a country (`P30`)  
- Population (latest, `P1082` + `P585`)  
- Head of state (`P35`)  
- Administrative entities contained in a country (`P150`)  

Examples:
```text
Q: What is the capital of France?
A: Paris

Q: What is the population of India?
A: 1,326,093,247 (2020-07-01)

Q: Who is the president of France?
A: Emmanuel Macron

Q: Which continent is Japan in?
A: Asia

Q: Which administrative entities does India contain?
A: Gujarat, Andhra Pradesh, Arunachal Pradesh, ...
Installation

Requirements:

Python 3.8+

requests library

Install dependencies:

pip install requests

Usage

Run the demo script:

python nl_to_sparql_wikidata_demo.py


The script contains a small list of sample questions in the examples array.
You can edit this list or call the nl_to_sparql_run() function with your own input.

Example run:

Q: What is the capital of France?
{'intent': 'capital', 'entity_label': 'France', 'qid': 'Q142', 'answers': ['Paris']}

Limitations

Only a small set of question types are supported.

Intent detection is regex-based (no NLP model).

Entity linking uses heuristics and type-checking; ambiguous cases may still need manual selection.

No caching, batching, or advanced error handling.

Answers depend on Wikidata completeness.

Purpose

Showcase a working NL → SPARQL → Wikidata pipeline.

Demonstrate ability to combine NLP preprocessing, knowledge graph querying, and API integration.

Intended for learning and recruitment demonstration.