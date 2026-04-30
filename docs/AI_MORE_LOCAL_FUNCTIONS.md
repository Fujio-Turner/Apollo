**Here are the most valuable additional local functions I’d recommend adding to your Apollo tool catalog.**

These suggestions are designed to fit *seamlessly* into the existing workflow (read-only, graph-aware, budget-conscious, Markdown-friendly, and focused on reducing tool-rounds or giving the assistant superpowers it currently lacks). I prioritized things that:

- Complement the current graph + file tools without duplication  
- Help with real-world pain points (large projects, OOP, git context, batching, architecture)  
- Keep the ~3-round budget rule intact by enabling wider nets or deeper single calls  

### Top-Tier Recommendations (add these first)

1. **`batch_get_nodes`**  
   **Why it’s huge**: `get_node` is single-shot. In multi-symbol questions you often need 3–8 nodes. This cuts rounds dramatically.  
   ```json
   {
     "name": "batch_get_nodes",
     "description": "Get full details for multiple nodes in one call (callers, callees, source, edges, etc.).",
     "parameters": {
       "type": "object",
       "properties": {
         "node_ids": { "type": "array", "items": { "type": "string" } }
       },
       "required": ["node_ids"]
     }
   }
   ```

2. **`get_paths_between`** (or `find_graph_paths`)  
   **Why it’s gold**: Answers “how is X connected to Y?” instantly — the #1 question when debugging architecture or refactoring.  
   ```json
   {
     "name": "get_paths_between",
     "description": "Find all paths (or shortest path) between two nodes with optional edge-type filter.",
     "parameters": {
       "type": "object",
       "properties": {
         "start_node_id": { "type": "string" },
         "end_node_id": { "type": "string" },
         "max_length": { "type": "integer", "default": 5 },
         "edge_types": { "type": "array", "items": { "type": "string" } }
       },
       "required": ["start_node_id", "end_node_id"]
     }
   }
   ```

3. **`get_directory_tree`** (or `list_project_files`)  
   **Why it’s essential**: No good way right now to see the *actual folder structure* at a glance. `project_search` is grep, not ls.  
   ```json
   {
     "name": "get_directory_tree",
     "description": "Recursive directory listing with optional depth, glob filter, and metadata (size, last modified if available).",
     "parameters": {
       "type": "object",
       "properties": {
         "root": { "type": "string", "description": "Optional sub-directory" },
         "depth": { "type": "integer", "default": 3 },
         "glob": { "type": "string" }
       }
     }
   }
   ```

4. **`get_git_context`** (or `get_file_history`)  
   **Why it’s a game-changer**: Almost every real codebase question involves “who changed this and why?”  
   ```json
   {
     "name": "get_git_context",
     "description": "Git blame + recent commits for a file, function, or line range.",
     "parameters": {
       "type": "object",
       "properties": {
         "path": { "type": "string" },
         "name": { "type": "string", "description": "Optional function/class name" },
         "line_start": { "type": "integer" },
         "line_end": { "type": "integer" },
         "limit": { "type": "integer", "default": 10 }
       },
       "required": ["path"]
     }
   }
   ```

### Strong Second-Tier (very useful, add after the top 4)

5. **`get_inheritance_tree`** – For any class node, returns full ancestor + descendant hierarchy (huge for OOP codebases).

6. **`get_transitive_imports`** – Full import dependency tree (direct + indirect) for a file or module. Perfect for “what will break if I change this?”

7. **`search_notes_fulltext`** – Full-text search across *all* user notes/bookmarks/highlights (currently you can only list or filter by tag/target).

8. **`get_code_metrics`** – Cyclomatic complexity, LOC, Halstead, cognitive complexity per function/file (cheap static analysis most indexers can already compute).

9. **`batch_file_sections`** – Same idea as `batch_get_nodes` but for multiple line-range requests in one call (great for large files).

10. **`find_test_correspondents`** – Given a function/class, returns the most likely test functions that cover it (by naming convention, decorators, or graph links).

### Quick Wins / Nice-to-Haves
- `get_subgraph` (multiple starting nodes + depth)  
- `detect_entry_points` (finds `__main__`, CLI commands, FastAPI/Flask routes, etc.)  
- `project_stats_detailed` (LOC by directory, top 20 largest files, language breakdown)  
- `search_graph_by_signature` (e.g., “functions that take (user_id: str, amount: int)”)  

Would any of these be particularly valuable for the kinds of questions you usually ask Apollo? I can flesh out the exact JSON schema + parameter descriptions (and even suggest how to update the system prompt workflow section) for whichever ones you want to implement first. Just say the word and I’ll give you the ready-to-paste tool blocks.