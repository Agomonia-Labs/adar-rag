# Distinguished-engineer-style post: the architecture behind ADAR (short version)

*Note: bold below is real Unicode bold (𝗹𝗶𝗸𝗲 𝘁𝗵𝗶𝘀), pastes into LinkedIn already formatted.*

---

𝗦𝗮𝗺𝗲 𝗿𝘂𝗻𝘁𝗶𝗺𝗲 𝗯𝗼𝗼𝗸𝘀 𝗮𝗽𝗽𝗼𝗶𝗻𝘁𝗺𝗲𝗻𝘁𝘀 𝗮𝗻𝗱 𝗮𝗻𝘀𝘄𝗲𝗿𝘀 𝗰𝗿𝗶𝗰𝗸𝗲𝘁 𝘁𝗿𝗶𝘃𝗶𝗮. 𝗢𝗻𝗲 𝗲𝗻𝘃 𝘃𝗮𝗿 𝗶𝘀 𝘁𝗵𝗲 𝗱𝗶𝗳𝗳𝗲𝗿𝗲𝗻𝗰𝗲.

We shipped 𝗔𝗗𝗔𝗥 𝗙𝗿𝗼𝗻𝘁 𝗗𝗲𝘀𝗸, a voice-and-chat agent that books real appointments — checks availability, holds the slot, confirms it, never double-books. Nothing about the runtime underneath is specific to scheduling.

Every agent graph is declared, not coded: a JSON file lists agents by name, and a two-pass compiler builds them at startup — leaf agents first (tools resolved against a domain-specific 𝗧𝗢𝗢𝗟_𝗥𝗘𝗚𝗜𝗦𝗧𝗥𝗬 of plain typed functions, no hand-written schemas), then orchestrator agents wired to those leaves. No routing logic in Python — the orchestrator's own instruction text is the router.

𝗻𝗼 𝘀𝘁𝗮𝘁𝗲 𝗺𝗮𝗰𝗵𝗶𝗻𝗲 tracks the booking flow. The model's context window carries "which practice, which provider, which slot" across tool calls — the tools themselves are stateless.

The one place we don't trust the model: concurrency. Two callers racing for the same slot hit a real Firestore transaction, not a prompt instruction. LLM judgment for the conversation, ACID guarantees for the write that matters.

𝗧𝗵𝗲 𝗽𝗮𝗿𝘁 𝗜'𝗱 𝗱𝗲𝗳𝗲𝗻𝗱 𝗶𝗻 𝗮 𝗱𝗲𝘀𝗶𝗴𝗻 𝗿𝗲𝘃𝗶𝗲𝘄: a new vertical is 𝗱𝗼𝗺𝗮𝗶𝗻 𝗳𝗶𝗹𝗲𝘀, 𝗻𝗼𝘁 𝗻𝗲𝘄 𝗰𝗼𝗱𝗲 — one env var, one config file, one tools module. Same compiler, same runtime, running products as different as a cricket-league Q&A bot and a multi-practice appointment scheduler.

#AgenticAI #MultiAgent #SoftwareArchitecture #LLM #SystemDesign
