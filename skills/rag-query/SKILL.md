---
name: rag-query
description: "Query the Block Operations military doctrine RAG API. Use when: user asks about army field manuals, military doctrine, army regulations, FM/TM/ATP publications, specific military topics (river crossings, cold weather operations, reconnaissance, etc.), or any question that could be answered by U.S. military publications. The API covers 280+ field manuals, technical manuals, and ATP publications. Trigger even if the user does not explicitly say 'RAG', 'API', or 'query' — if it's a military doctrine question, query the RAG first. When in doubt, query it."
---

# RAG Query Skill

## What this skill does
When you receive a military doctrine question, query the Block Operations RAG API and use the results as context to answer accurately.

## When to trigger
- Any question about U.S. military doctrine, field manuals, technical manuals
- Questions about Army, Marine Corps, Navy, or Air Force publications
- Questions about specific FM/TM/ATP/AR numbers
- Questions about military operations, tactics, logistics, leadership, training
- Even vague questions like "what does the Army say about X?" should trigger a query

## API Details

| Item | Value |
|------|-------|
| URL | `https://<PUBLIC_HOST>/api/query` |
| Method | POST |
| Auth | Bearer token |
| Key | `Bp18OLgIfbUXOVXsrpZEbpwozgsnk7ANIugm9XTXMik` |
| Collection | `army-docs` |
| Max results | 5 (good default) |

## How to query

```bash
curl -s -X POST https://<PUBLIC_HOST>/api/query \
  -H "Authorization: Bearer Bp18OLgIfbUXOVXsrpZEbpwozgsnk7ANIugm9XTXMik" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "How do you conduct a river crossing operation?",
    "top_k": 5,
    "collection": "army-docs"
  }'
```

## Response format

The API returns a JSON array of results. Each result has:
- `text` — the chunk text
- `doc_id` — document identifier  
- `title` — full document title
- `page` — page number
- `score` — relevance score (higher = better, typically 0.5+ is good)

## How to use the results

1. Parse the JSON response
2. Format results as a readable context block
3. Answer the user's question using the retrieved context
4. Cite the source: title, FM/TM number, and page

## Output template for answers

```
Based on {result[0]['title']} (FM X-X):

{concise answer drawing from the retrieved chunks}

Sources:
  1. {title}, p. {page} — {score}
  2. {title}, p. {page} — {score}
  [etc.]
```

## Important rules
- Always include source citations (doc title + page number)
- If all scores are below 0.5, say "I didn't find strong matches in the military doctrine database. Could you rephrase?"
- If the API fails, fall back to your knowledge — do not say "I couldn't query the API"
- Include 2–3 short relevant quotes from the chunks to support your answer
- Never make up a specific FM/TM number or quote — always verify against the retrieved text

## Test queries
1. "What does FM 100-15 say about corps operations?"
2. "How should a platoon leader conduct a reconnaissance?"
3. "What are the principles of military briefing?"
