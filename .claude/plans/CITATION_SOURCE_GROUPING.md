# Citation/Source Grouping Plan

**Status:** Implemented (CompanyGeneralHandler only)

## Goal
Gemini-style source citations: inline link icons per paragraph + consolidated sources section at end with paragraph associations.

## Backend

### New: `_collect_paragraph_sources()` in `handlers.py`
- Wraps `_process_source_tags()` output
- Passes all events through unchanged (preserves inline `sources` events)
- Tracks paragraph index via `\n\n` boundaries in `answer` events
- Emits `sources_grouped` event at stream end:
  ```json
  {"type": "sources_grouped", "body": {"sources": [{"name": "...", "url": "...", "paragraph_indices": [0, 2]}]}}
  ```

### Paragraph index tracking
- `\n\n` increments `paragraph_index`
- Sources after `\n\n` with no new content → belong to PREVIOUS paragraph (`has_content_in_current` flag)

### Google Search fix
- `:nitro:online` variant chaining was invalid for OpenRouter
- Fix: strip existing variant before appending `:online`

## Frontend

### `useChatAPI.ts`
- `sources` events: trim trailing `\n\n`, append links inline, re-add `\n\n`
- `sources_grouped` events: store in `thread.sources`

### `Chat.tsx`
- `thread.sources` → `ResourceChips` in "Sources" section
- Falls back to `thread.grounds` (Google Search) if no grouped sources

### `MarkdownContent.tsx`
- `[name](url)` → circular link icons with tooltip (right side)

## Migration TODO
- [ ] Wire `_collect_paragraph_sources` into `GeneralFinanceHandler`
- [ ] Wire into `CompanySpecificFinanceHandler`
- [ ] Wire into ETF handlers (need `_process_source_tags` first)
- [ ] Frontend: hover-to-highlight paragraph using `paragraphIndices`
