# SQL++ Queries & Indexes

All queries target **Couchbase Lite** using the SQL++ (N1QL) dialect.  
Three collections are used: `nodes`, `edges`, and `chat_threads`.  
Schemas live in [`schema/`](../schema/).

---

## Collections & Schemas

| Collection | Schema | Description |
|---|---|---|
| `nodes` | [`node.schema.json`](../schema/node.schema.json) | One doc per graph node (file, function, class, etc.) — doc ID = `node_id` |
| `edges` | [`edge.schema.json`](../schema/edge.schema.json) | One doc per relationship — doc ID = `"{source}--{type}-->{target}"` |
| `chat_threads` | [`chat-thread.schema.json`](../schema/chat-thread.schema.json) | One doc per conversation thread — doc ID = UUID |
| *(embedded)* | [`spatial.schema.json`](../schema/spatial.schema.json) | 3D coordinates embedded inside each node's `spatial` field |

---

## Indexes

Created in [`graph_search/storage/cblite/store.py`](../graph_search/storage/cblite/store.py) → `_create_indexes()`.

### Value Indexes (nodes collection)

| Index Name | Collection | Expression | Purpose |
|---|---|---|---|
| `idx_type` | `nodes` | `type` | Filter nodes by entity type (function, class, file, …) |
| `idx_name` | `nodes` | `name` | Look up nodes by name |
| `idx_path` | `nodes` | `path` | Look up nodes by file path |

### Value Indexes (edges collection)

| Index Name | Collection | Expression | Purpose |
|---|---|---|---|
| `idx_source` | `edges` | `source` | Find edges leaving a given node |
| `idx_target` | `edges` | `target` | Find edges arriving at a given node |
| `idx_edge_type` | `edges` | `type` | Filter edges by relationship type |

### Vector Index (nodes collection — Enterprise Edition only)

| Index Name | Collection | Expression | Dimensions | Centroids | Purpose |
|---|---|---|---|---|---|
| `idx_embedding` | `nodes` | `embedding` | *(dynamic)* | `√(node_count)` | ANN search via `APPROX_VECTOR_DISTANCE` |

> Community Edition falls back to brute-force cosine similarity — no vector index is created.

---

## Queries

### Graph Storage — `graph_search/storage/cblite/store.py`

#### Load all nodes
```sql
SELECT META().id AS _id, * FROM nodes
```
Returns every node document with its doc ID.  
**Used by:** `CouchbaseLiteStore.load()`

#### Load all edges
```sql
SELECT META().id AS _id, * FROM edges
```
Returns every edge document with its doc ID.  
**Used by:** `CouchbaseLiteStore.load()`

#### Purge all documents in a collection
```sql
SELECT META().id AS _id FROM {collection_name}
```
Fetches all doc IDs before calling `purge_document()` on each.  
**Used by:** `CouchbaseLiteStore._purge_all()` — called for both `nodes` and `edges` before a full rebuild.

---

### Semantic Search — `graph_search/search/cblite_semantic.py`

#### Check for embeddings
```sql
SELECT COUNT(*) AS cnt FROM nodes WHERE embedding IS NOT NULL
```
Returns whether the database has any embedded nodes.  
**Used by:** `CouchbaseLiteSemanticSearch.has_embeddings()`

#### Vector index search (Enterprise Edition)
```sql
SELECT META(n).id AS _id, n.name, n.type, n.path,
       n.line_start, n.line_end,
       APPROX_VECTOR_DISTANCE(n.embedding, $vec) AS distance
FROM nodes AS n
[WHERE n.type = "<node_type>"]
ORDER BY distance
LIMIT <top_k>
```
Parameters: `$vec` (query embedding vector).  
Uses the `idx_embedding` vector index for approximate nearest-neighbor search.  
**Used by:** `CouchbaseLiteSemanticSearch._search_vector_index()`

#### Brute-force search (Community Edition)
```sql
SELECT META().id AS _id, name, type, path,
       line_start, line_end, embedding
FROM nodes
WHERE embedding IS NOT NULL
[AND type = "<node_type>"]
```
Fetches all embeddings; cosine similarity is computed in Python with NumPy.  
**Used by:** `CouchbaseLiteSemanticSearch._search_brute_force()`

---

### Chat History — `graph_search/chat/history.py`

#### Get a single thread by ID
No SQL query — uses a direct Couchbase Lite document GET via
`CBL.get_document_json(collection, doc_id)` since the document ID is known.  
**Used by:** `ChatHistory.get_thread()`

#### List all threads (summary)
```sql
SELECT META().id AS _id, title, created_at, updated_at, model,
       ARRAY_LENGTH(messages) AS message_count
FROM chat_threads
ORDER BY updated_at DESC
```
Returns thread summaries without full message bodies.  
**Used by:** `ChatHistory.list_threads()`
