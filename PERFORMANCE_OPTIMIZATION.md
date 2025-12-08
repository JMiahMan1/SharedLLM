# Performance Optimization: Pipeline Intent Classification

## Problem
The RAG API was experiencing slow response times (~28 seconds) due to redundant intent classification calls and multiple sequential Ollama API calls.

## Root Cause Analysis

### Redundant Intent Classification
Intent classification was happening **3 times** per request:
1. In `contextualize_query()` - line 132
2. In `generate_rag_stream()` - line 366 (REDUNDANT - intent already known)
3. In `_handle_single_command()` - line 269 (REDUNDANT - intent already known)

Each intent classification involves:
- Regex pattern matching (fast)
- Vector embedding computation (moderate cost)
- Similarity search against phrasebook (moderate cost)

### Multiple Ollama Calls
- `contextualize_query()` may call Ollama for context rewriting (conditional)
- `_llm_orchestrator()` always calls Ollama (required)

## Solution

### Optimization 1: Pass Intent Through Pipeline
Modified `contextualize_query()` to return intent information along with the refined query:
- **Before**: `refined = await contextualize_query(query, user, model)`
- **After**: `refined, intent, score, is_high_confidence = await contextualize_query(query, user, model)`

### Optimization 2: Eliminate Redundant Classifications
1. **In `generate_rag_stream()`**: Removed redundant `IntentClassifier.get_intent()` call, use intent from `contextualize_query()`
2. **In `_handle_single_command()`**: Added optional parameters to accept pre-computed intent, only classify if not provided
3. **In `try_handle_compound_command()`**: Pass intent through to `_handle_single_command()` for single commands

### Code Changes

#### `app/logic/pipeline.py`
- `contextualize_query()`: Now returns `(refined_query, intent, score, is_high_confidence)`
- `_handle_single_command()`: Added optional parameters `intent`, `score`, `is_high_confidence`
- `try_handle_compound_command()`: Added optional parameters to pass intent through
- `generate_rag_stream()`: Uses intent from `contextualize_query()` instead of re-classifying

#### `app/logic/__init__.py`
- Updated export to use `contextualize_query` from `pipeline.py` instead of `utils.py`

#### `app/main.py`
- Updated `/rag/query` endpoint to handle new `contextualize_query()` return signature

## Expected Performance Improvement

### Before Optimization
- Intent classification: 3x per request
- Estimated time: ~3-5 seconds for intent classification alone
- Total overhead: Significant

### After Optimization
- Intent classification: 1x per request (only in `contextualize_query()`)
- Estimated time savings: ~2-4 seconds per request
- Total overhead: Reduced by ~66% for intent classification

## Testing

To verify the optimization:
1. Run `python3 tools/test_connectivity.py` - should show faster response times
2. Run `python3 tools/profile_pipeline.py` - should show reduced processing time
3. Check server logs for `[PIPELINE DEBUG]` messages showing intent is only classified once

## Backward Compatibility

The changes maintain backward compatibility:
- For compound commands (multiple commands), each sub-command still gets its own intent classification (as intended)
- Single commands now benefit from intent caching
- The `_handle_single_command()` function gracefully handles missing intent (falls back to classification)

## Future Optimizations

Potential further improvements:
1. **Cache intent results** within a request session (Redis cache with short TTL)
2. **Parallelize** context retrieval (HA context, RAG context, web search) - already partially done
3. **Optimize Ollama calls**: Consider batching or using faster models for simple queries
4. **Add request-level caching**: Cache intent results for identical queries within a short time window

## Related Files

- `app/logic/pipeline.py` - Main pipeline logic
- `app/logic/intents/classifier.py` - Intent classification
- `app/intent_engine.py` - Vector-based intent engine
- `app/logic/utils.py` - Utility functions (contains old `contextualize_query` - now unused)

