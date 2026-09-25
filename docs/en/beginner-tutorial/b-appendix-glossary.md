# Appendix B · Quick Reference

> One-line definitions of all terms with a chapter index. The corresponding chapters contain the complete explanations, code, prompts, input, and representative output inline.

| Term | One-line definition | Chapter |
|---|---|---|
| agent runtime | The layer above the LLM interface that carries context assembly, execution authority, state management, and interception extension | 1-1 |
| context assembly | Composing the system prompt, history, and tool table under uniform rules before every request | 1-1 |
| tool call | The protocol in which the model outputs structured intent, the framework executes it, and the result is fed back | 1-2 |
| execution authority | Final execution authority for actions rests with the framework; the model only proposes | 1-2 |
| tool specialization | An agent's capability is structurally defined by its tool table, not by prompt agreements | 1-3 |
| registered / visible | The two states of a tool: present (registry) versus allowed to propose (context tool table) | 1-3 |
| ReAct loop | A solving structure that alternates reasoning and tool calls, with "no tool call" as the main exit | 1-4 |
| termination condition | The loop's explicit exits: the main exit (no tool call) plus auxiliary caps (turns / budget / assertions) | 1-4 |
| event-driven | The concurrency model in which external stimuli are queued uniformly and consumed in order by a single loop | 2-1 |
| two entry types | Message entries: synchronous wait (get the result) and asynchronous delivery (deliver only) | 2-1 |
| steering | Injecting a high-priority message into a running turn; visible in the current turn without interrupting it | 2-1 |
| cooperative cancellation | Cancellation is a signal, not a kill; the executing body decides at checkpoints how to respond | 2-1 |
| deadlock (agent context) | A turn's call stack synchronously waits on its own agent, or the wait chain forms a cycle | 2-1 |
| role system | The Chat API's general abstraction that assigns roles by message origin (system/user/assistant/tool) | 2-2 |
| chunked content model | Message content is a list of blocks; multimodal and structured data are carried uniformly | 2-2 |
| pairing completeness | Call intent and result are paired one-to-one by ID, enforced at the construction layer | 2-2 |
| three-way division (declaration / assembly / configuration) | File-level separation of behavior, assembly, and environment concerns | 2-3 |
| composition root | The application entry point where the entire object graph is assembled in one place | 2-3 / 5-5 |
| creation-time validation | Configuration and parameter errors are reported when the component is created, not on first call | 2-3 |
| indirection layer | Logical names map to physical implementations through a lookup table; swapping implementations requires no code change | 2-3 |
| interception point | A point in the execution pipeline with a pre-registered custom function, in two grades: observation and interception | 2-4 |
| interception exits | The three standard exits: block / rewrite / allow | 2-4 |
| human approval | The engineering form of an interception point + asynchronous confirmation wait + a blocking exit | 2-4 |
| error channel | Business failure (fed back to the model), programming error (propagated), hard interception (rejected with reason) | 2-4 |
| dependency injection | The decoupling mechanism in which components declare what they need and the assembly side provides it | 3-1 |
| scope chain | The sharing rule in which values are looked up along the component hierarchy, nearest first | 3-1 |
| sensitive-data boundary | Credential-like values never enter context or disk, and their visibility narrows at deep nodes | 3-1 |
| record stream | History-like data is appended; deletions are expressed as markers; the backend cleans and rewrites periodically with replay invariance | 3-2 |
| write-behind | Memory is mutated first and written to disk asynchronously in the background; normal exit requires draining | 3-2 |
| crash-consistency window | The window in which committed-but-unsaved records are lost on crash while semantics stay self-consistent | 3-2 |
| extension point | A contract-bound intervention position reserved in the core pipeline for extensions | 3-3 |
| two-phase enablement | Extension activation with registration (once globally) separated from enablement (per agent) | 3-3 |
| group proxy | The proxy form that declares a whole group of tools as one and expands by composite name | 4-1 |
| composite name | Namespace-isolated naming of the form `<declared-name>--<server-tool-name>` | 4-1 |
| command injection guarding | The template discipline in which inserted values are quoted by default and raw concatenation is explicitly marked | 4-2 |
| exit-code semantics | A non-zero exit code is result data, not a failure signal | 4-2 |
| expected status codes | The set of status codes the caller declares as success; anything outside it counts as failure | 4-2 |
| context wall / permission wall / serialization wall | The three types of extension limits of a single agent | 5-1 |
| complexity tax | The communication, consistency, and debugging costs introduced by splitting | 5-1 |
| organization patterns | The five structures: orchestrator–worker / router / hierarchy / bus / fan-out | 5-2 |
| capability catalog | The worker list visible to the orchestrator: aliases, responsibility descriptions, visible parameters | 5-3 |
| self-sufficient delegation package | Delegation information must carry the complete task and context, because workers share no memory | 5-3 |
| type binding | Declaring available types without creating instances (catalog layer) | 5-4 |
| named resume | Addressing the same instance by semantic name, with history and state continuing | 5-4 |
| content/state separation | Output results carry produced content and execution state in separate fields | 5-4 |
| forgetting hierarchy | Stop (recoverable) → archive (recorded transcript retained) → physical deletion (application layer) | 5-4 |
| orchestration-driven | Two scheduling forms: model routing (flexible) and programmatic driving (deterministic) | 5-5 |
| configuration injection chain | The explicit value-resolution chain: startup parameters → provide → inject → template rendering | 5-5 |
