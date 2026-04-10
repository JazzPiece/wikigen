# Roadmap

## Shipped

### `--log-file PATH` flag on `wikigen ingest`
Tees full stdout+stderr run transcript to a file. Useful for auditing long runs,
debugging errors, and keeping a record of what was processed.

```bash
wikigen ingest --log-file ./run.log
wikigen ingest --full --log-file C:/logs/wikigen-2026-04-10.log
```

---

## Planned features

### Feature 1 — Incremental re-compile with reference updates

When new files are added to the source folder, the wiki should:
1. Detect them (already works via hash state)
2. Generate the new article
3. **Update existing articles that should reference the new content** — scan existing pages whose topics overlap and append wikilinks to their `## Related` sections

**Current behavior:** `ingest --incremental` handles 1 & 2. Step 3 (back-propagating references to existing pages) is not yet implemented.

**Implementation plan:**
- After ingesting new files, run a reverse cross-reference pass
- For each new article, find existing articles with overlapping entities/topics
- Ask LLM: "Should [[existing-page]] link to [[new-page]]?"
- If confidence >= threshold, append the link and rewrite the existing article
- Flag: `wikigen ingest --update-refs`

---

### Feature 2 — LLM note enhancement with user confirmation (git-diff style)

The LLM reviews existing wiki articles and proposes improvements:
- Expanding thin summaries with better synthesis
- Adding missing entity links
- Fixing stale cross-references
- Flagging contradictions between pages

**For minor edits** (adding a wikilink, fixing a tag): apply automatically.

**For major changes** (rewriting a summary, merging pages, deleting content): show a `git diff`-style preview and ask for confirmation before writing:

```
--- wiki/Backstitch/access-control-policy.pdf.md (current)
+++ wiki/Backstitch/access-control-policy.pdf.md (proposed)
@@ -12,6 +12,8 @@ tags: [pdf, policy, security]
 ## Summary
-Access control policy covering user provisioning.
+Access control policy covering user provisioning, MFA requirements,
+and quarterly access reviews. References the SOC 2 Type 2 controls
+for logical access (CC6.1-CC6.3).

Accept this change? [y/N/skip/quit]
```

**Implementation plan:**
- New command: `wikigen enhance [--auto-minor] [--dry-run]`
- LLM reads each article + related pages, proposes a revised version
- Diff is computed with Python `difflib`
- Changes classified as minor/major based on % of lines changed (threshold configurable)
- Minor changes applied automatically; major changes shown for approval
- Approved/rejected decisions logged to `log.md`
