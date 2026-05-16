# AI Trace Visualizer

A fullscreen, interactive timeline of every step the chat AI took to
answer a question — from the initial LLM request through each tool
call against the local Apollo graph, all the way to the streamed final
answer.

Visually inspired by Couchbase's
[query plan diagrams](https://docs.couchbase.com/cloud/n1ql/_images/join-order-hint.png):
each step is a node positioned by **wall-clock time** (X axis) and
**round / lane** (Y axis), with size and brightness encoding how long
the step took.

---

## How to open it

1. In the chat panel, send a message and wait for the assistant reply.
2. Below the assistant bubble, click the small **▸ Trace** strip to
   expand the per-message trace.
3. The expanded trace header now shows two action buttons on the
   right:
   - 📋 **Copy** — copy the trace as plain text
   - 📊 **Visualize** — open the timeline overlay
4. Click **Visualize**. A dark, transparent overlay (same style as the
   graph and word-cloud "expand" overlays) covers the page with the
   timeline diagram.
5. Close with the × button, click outside the card, or press **Esc**.

---

## What you're looking at

```diagram
╭───────────── AI Trace Timeline ─────────────╮
│  ■ AI request (0:00:02.345)                 │
│  ● Apollo tool (0:00:01.234)                │
│  ▼ Result     (0:00:00.000)                 │
│  ▲ Issue      (0:00:00.000)                 │
├─────────────────────────────────────────────┤
│  Total 3.6s · Rounds 2 · Tool calls 5 · …   │
├─────────────────────────────────────────────┤
│  Finish    │              ✎────────●        │
│  Round 1   │            ■──○──○             │
│  Round 0   │     ■──○──○──○                 │
│  Setup     │  ▲                             │
│            └───────────────────────────────▶│
│                       elapsed (s)           │
╰─────────────────────────────────────────────╯
```

### Axes

- **X axis = `elapsed (s)`** — true wall-clock seconds since the
  request was sent to the LLM. Captured server-side via the new
  `t_elapsed` field on every trace event, so it includes AI thinking
  time, network latency, and any gaps between steps.
- **Y axis = lane** — one horizontal swim-lane per logical phase:
  - `Setup` — request preparation
  - `Round 0`, `Round 1`, … — each tool-calling round the model went
    through
  - `Finish` — stream begin, done, return_result, errors

### Nodes (every step is a node)

Each step the backend emits gets a node with:

| Property         | Encoding                                            |
| ---------------- | --------------------------------------------------- |
| **Position**     | (X = `t_elapsed`, Y = lane)                         |
| **Color family** | Category (legend, see below)                        |
| **Brightness**   | For Apollo tools: scales with how slow the call was |
| **Symbol size**  | 14 → 50 px, scales with the step's duration         |
| **Shape**        | Sub-kind within a category (see legend table)       |
| **Glow**         | Shadow blur scales with duration → slowest "shines" |

### Edges (arrows between nodes)

Curved arrows connect each step to the **next step in execution
order**, so you can read the flow even when the X positions are
clustered.

---

## Legend

The top legend groups every step into one of four high-level
categories. **Click any legend entry to hide/show that whole category**
— a fast way to isolate, say, only the Apollo tool calls.

The trailing `(H:MM:SS.mmm)` value is the **cumulative wall-clock time
spent in that category** across the entire request.

| Symbol | Category        | Includes                               | Time source                         |
| ------ | --------------- | -------------------------------------- | ----------------------------------- |
| ■      | **AI request**  | `request`, `round`, `stream_begin`     | Σ `round.dt` + `done.stream_dt`     |
| ●      | **Apollo tool** | `tool_call`, `tool_return`             | Σ `tool_return.dt`                  |
| ▼      | **Result**      | `done`, `return_result`                | terminal markers (no own duration)  |
| ▲      | **Issue**       | `error`, `rounds_exhausted`            | terminal markers (no own duration)  |

### Sub-shapes within a category

Even within a single color family, the symbol shape tells you exactly
which kind of step it is:

| Shape    | Step             | Meaning                                              |
| -------- | ---------------- | ---------------------------------------------------- |
| Triangle | `request`        | The chat service sent the prompt to the LLM         |
| Rect     | `round`          | LLM completion finished thinking and replied        |
| Circle   | `tool_call` / `tool_return` | Apollo executed a local tool          |
| Diamond  | `stream_begin`   | LLM started streaming the final answer              |
| Pin      | `done` / `return_result` | Final answer delivered                      |
| Triangle | `error`          | Something failed                                    |
| Arrow    | `rounds_exhausted` | LLM hit the tool-calling round limit              |

### Brightness / glow legend

The two swatches in the upper-right legend (`slow` / `fast`) show the
brightness scale used **for Apollo tool nodes**. The slowest tool call
in the trace is fully saturated and casts the strongest shadow; the
fastest is faded.

---

## Hover for details (IN / OUT)

Hovering any node opens a tooltip with full metadata for that step.
For tool calls in particular, you'll see:

- **🔧 *tool_name*** — header with duration in seconds
- **IN** — the truncated arguments the LLM passed in (`args_preview`)
- **OUT** — the first ~240 chars of what the tool returned (`preview`)
- **bytes** — raw JSON size of the result
- **toon** — if the result was re-encoded as TOON for the LLM, shows
  the smaller byte size and percent saved
- **@ X.XXs** — exact wall-clock offset of this step

Other event kinds show appropriately scoped fields (rounds show
finish-reason and tool-call count; `done` shows tokens and bytes; etc.).

---

## Summary bar

Above the chart, four pill-cards summarize the whole trace at a
glance:

- **Total** — total wall-clock time of the request (from `done.total_dt`)
- **Rounds** — how many tool-calling rounds the model used
- **Tool calls** — total number of Apollo tool invocations
- **Slowest** — name and duration of the single slowest tool call

---

## How to read common patterns

### "Where did the time go?"

Look at the top legend's `(H:MM:SS.mmm)` per category:

- Big **AI request** time → the LLM is the bottleneck (slow model,
  long prompts, or many rounds of tool-calling)
- Big **Apollo tool** time → local tools (graph search, file reads,
  etc.) are the bottleneck

Then on the chart, find the node with the **biggest, brightest glow**
— that's the single slowest step. Hover it for IN/OUT details.

### "Why did this take so many rounds?"

Count the round lanes on the Y axis. Each round = one full
LLM round-trip. Click a round's circle nodes (the tool calls) and
read their IN args to understand what the model was iteratively
gathering before it had enough context to answer.

### "What did the AI actually run against my graph?"

Click the **AI request** legend entry to **hide** all the LLM
bookkeeping nodes. What's left is just the Apollo tool calls — every
piece of work that touched your local data.

### "Did anything fail?"

If the **Issue** category in the legend shows anything other than
`(0:00:00.000)`, look for red triangle / arrow nodes in the `Finish`
lane. Hover for the error message and which phase it happened in.

---

## Implementation notes (for developers)

- **Backend** ([chat/service.py](../../chat/service.py)) — every
  yielded `step` event now carries
  `"t_elapsed": round(time.time() - t_start, 3)`. This is a hard
  guarantee that wall-clock time is preserved across the SSE wire,
  even when there are gaps between events.
- **SSE relay** ([web/server.py](../../web/server.py)) — already
  forwards the entire step dict via `json.dumps(ev, default=str)`,
  so no changes were needed; `t_elapsed` rides along automatically.
- **Frontend** ([web/static/app.js](../../web/static/app.js)) —
  - `_openTraceVisualizer(steps)` mounts the overlay
  - `_buildTraceVizGraph(steps)` walks the steps once, assigning each
    a `(t_elapsed, lane)` coordinate, then constructs an
    [ECharts graph-on-cartesian](https://echarts.apache.org/examples/en/editor.html?c=graph-grid)
    series with categories, custom symbols, glow shadows, and a rich
    HTML tooltip
  - `_closeTraceVisualizer()` disposes the chart and removes the
    window-resize listener
- **CSS** ([web/static/app.css](../../web/static/app.css)) — reuses
  the existing `.pane-overlay` shell (same dark transparent backdrop
  used by the graph / word-cloud expand overlays). Visualizer-specific
  styles live under `.trace-viz-*` and `.tv-summary*`.

### Adding a new trace event kind

1. Yield a new `{"type": "step", "phase": "your_kind", "rid": rid,
   "t_elapsed": round(time.time() - t_start, 3), ...}` from
   `ChatService.chat_stream`.
2. In `_buildTraceVizGraph` (frontend), add a branch in the
   `for (const s of steps)` loop that assigns it a lane and
   `addNode(...)`.
3. Update `categoryFor`, `colorFor`, and `symbolFor` so the new kind
   gets a category, color, and shape.
4. (Optional) Add a tooltip branch in `tooltipFormatter` for any
   special fields you want to show on hover.
