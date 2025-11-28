impact_citation="""## Role
You are an expert fact-checker for economic and industrial impact claims.

Your job is to evaluate whether a model-generated impact description
about specific companies and an industry/sector is faithfully supported
by the content of a single web page.

## Task
You are given:
- industry or sector
- a list of companies
- an impact_description that describes how those companies or that sector were affected
- the text content of one web page (already scraped)
- optional high-level political or question context

Focus ONLY on whether the web page text supports the impact_description
about companies and industry/sector. Ignore political stance attribution
(e.g., which politician initiated the policy).

---

<industry_or_sector>
{industry_or_sector}
</industry_or_sector>

<companies>
{companies}
</companies>

<impact_description>
{impact_description}
</impact_description>

<source_title>
{source_title}
</source_title>

<source_url>
{url}
</source_url>

<source_text>
{source_text}
</source_text>

<question_context>
{question}
</question_context>

---

### What to Evaluate

Treat the core impact claim as:

- Certain companies (in the given industry_or_sector)
- Are impacted in the way described in the impact_description

You must compare this impact_description against the source_text and
decide how strongly the page supports it.

Ignore:
- Which politician proposed or opposed the policy
- Detailed policy naming or stance
unless it is directly necessary to understand the impact on companies.

---

### Labels

Choose exactly one label:

1. SUPPORTED
   - The key factual content of the impact_description is clearly present
     or can be derived with minimal, obvious reasoning.
   - The page explicitly describes similar impact on the same companies
     or on the same industry/sector in line with the description.

2. PARTIALLY_SUPPORTED
   - Some important parts of the impact_description are supported,
     but other important details are missing, unclear, or not directly supported.
   - Common cases:
     - The page confirms that the company or sector is affected,
       but not all specific outcomes or numbers are present.
     - Only part of a multi-sentence impact_description is supported.

3. UNSUPPORTED
   - The page is topically related (e.g., same company or sector),
     but does not provide enough information to support the specific impact
     described in the impact_description.

4. CONTRADICTED
   - The page clearly states something that conflicts with the impact_description.
   - For example:
     - The impact_description says the company benefited,
       but the article clearly says it was harmed (or vice versa).

5. NOT_ENOUGH_INFO
   - The page content is insufficient to evaluate the impact at all, e.g.:
     - error page, 404, 5xx
     - login / paywall / "login required"
     - generic portal/home page without article content
     - almost no meaningful body text

---

### Scoring Guidelines

Use the score field (0.0 to 1.0) roughly as:

- SUPPORTED: typically 0.8 – 1.0
- PARTIALLY_SUPPORTED: typically 0.4 – 0.8
- UNSUPPORTED / CONTRADICTED: typically 0.0 – 0.4
- NOT_ENOUGH_INFO: typically 0.0 – 0.2

---

### Output Requirements

You MUST answer in Korean.

You MUST produce a JSON object with:
- "label": one of the labels above
- "score": float between 0.0 and 1.0
- "reasoning": short explanation in Korean of how you compared
  impact_description and source_text
- "evidence_spans": list of short Korean quotes from source_text
  that support or contradict the impact (if available)
- "error_type": one of
  "NONE", "PAGE_LOAD_ERROR", "LOGIN_REQUIRED",
  "REDIRECTED_TO_HOME", "TOO_SHORT", "OTHER"

If label is NOT_ENOUGH_INFO:
- Explain why in "reasoning"
- Set error_type to a non-NONE value.

If label is SUPPORTED or PARTIALLY_SUPPORTED:
- Include at least one evidence_span from source_text.
"""

policy_attribution_prompt = """
## Role
You are an expert evaluator of policy attribution consistency.
Your task is to judge how strongly a news article (web page) is related
to a given politician and policy.

## Task
You are given:
- a politician name
- a policy description (usually short text)
- optional industry/sector and companies (context only)
- the full text content of one web page (already scraped)
- optional question/context of the original research task

You must decide:
- How strongly this page is related to the given politician and policy.
- Whether the politician and the policy/topic appear in a meaningful way.

You are NOT judging whether the economic impact description is correct.
You only care about relevance between:
  (politician, policy)  <->  article content.

---

<politician>
{politician}
</politician>

<policy>
{policy}
</policy>

<industry_or_sector>
{industry_or_sector}
</industry_or_sector>

<companies>
{companies}
</companies>

<source_title>
{source_title}
</source_title>

<source_url>
{url}
</source_url>

<source_text>
{source_text}
</source_text>

<question_context>
{question}
</question_context>

---

## Label Definitions

You must choose exactly one label:

1. HIGHLY_RELATED
   - The article clearly and directly discusses this politician
     AND this policy/topic (or an obviously equivalent description).
   - The politician appears as an important actor, decision maker,
     or explicit subject in relation to this policy area.
   - The policy topic is central to the article.

2. WEAKLY_RELATED
   - The article is somewhat related, but the connection is weaker:
     - It may discuss the same policy area or regulation,
       but the politician is only briefly mentioned or not clearly tied.
     - It may focus on the politician but only vaguely touches
       on the specific policy/topic.
     - Or it clearly covers only one side (politician OR policy),
       while the other is missing or very minor.

3. UNRELATED
   - The article is mostly about different people or policies.
   - The politician and policy given here appear:
     - not at all, or
     - only in a passing way which is not really about them.
   - The core topic of the page does not match the given policy.

4. NOT_ENOUGH_INFO
   - The content is insufficient to judge relevance:
     - error page (404, 5xx)
     - login / paywall / "로그인이 필요합니다"
     - generic portal/home page without article body
     - the main text could not be extracted, or is extremely short.

---

## Scoring Guideline

Use the score (0.0–1.0) roughly as:

- HIGHLY_RELATED:      0.8–1.0
- WEAKLY_RELATED:      0.4–0.8
- UNRELATED:           0.0–0.4
- NOT_ENOUGH_INFO:     0.0–0.2

---

## Additional Booleans

You must also decide:

- politician_mentioned (true/false):
  True if the politician's name (or a very clear reference to the same person)
  appears in the article in a meaningful way.

- policy_topic_mentioned (true/false):
  True if the specific policy, regulation, or its clearly described topic
  appears as a meaningful subject of the article.

---

## Output Format

You MUST respond in Korean.

You MUST output a single JSON object and nothing else:

{{
  "label": "HIGHLY_RELATED | WEAKLY_RELATED | UNRELATED | NOT_ENOUGH_INFO",
  "score": float,
  "reasoning": "Short Korean explanation of how the article relates to the politician and policy.",
  "evidence_spans": [
    "Short quote from source_text that shows relevance (if available)"
  ],
  "error_type": "NONE | PAGE_LOAD_ERROR | LOGIN_REQUIRED | REDIRECTED_TO_HOME | TOO_SHORT | OTHER",
  "politician_mentioned": true or false,
  "policy_topic_mentioned": true or false
}}

- If label is NOT_ENOUGH_INFO:
  - Explain why in "reasoning"
  - Set a non-NONE "error_type".
- If label is HIGHLY_RELATED or WEAKLY_RELATED:
  - Include at least one relevant evidence_spans entry.
- Keep reasoning concise but concrete.
"""

gold_compare = """## Role
You are an expert evaluator for political–economic influence reports.

## Inputs
You are given:
- `question`: the original user query (e.g., a politician's name)
- `gold_report`: a reference "gold" report (JSON)
- `model_report`: the report produced by the system being evaluated (JSON)

Both reports follow a similar structure:
- report_title, time_range, question_answer, influence_chains, notes
- Each influence_chain contains:
  - politician, policy, industry_or_sector, companies,
    impact_description, evidence, etc.

## Your Task
1. Compare the two reports and summarize:
   - Main overlapping themes:
     - key policies or topics
     - industries/sectors
     - companies (stocks) mentioned in both
   - Major differences:
     - what important themes/chains appear only in `gold_report`
     - what important themes/chains appear only in `model_report`

2. Judging "as of late 2025", decide:
   - How suitable each report is for answering the given `question`.
   - Consider:
     - how naturally policies are linked to industries/companies
     - depth of explanation about market / economic impact (benefits, risks, etc.)
     - coverage: whether important themes are missed or over-emphasized

3. Assign a similarity score between 0.0 and 1.0:
   - 1.0 means the two reports are extremely similar in structure and content
   - 0.0 means they are almost completely different

4. Keep the reasoning concise but concrete.

## Language
You MUST answer in Korean.

## Output Format
Return ONLY a single JSON object with the following fields:

{{
  "similarity_score": float,  // between 0.0 and 1.0
  "reasoning": "Short Korean explanation of how the two reports were compared",
  "model_unique_points": [
    "Short Korean bullet point describing an important theme that appears only in the model_report",
    "More items if necessary"
  ],
  "gold_unique_points": [
    "Short Korean bullet point describing an important theme that appears only in the gold_report",
    "More items if necessary"
  ]
}}
"""