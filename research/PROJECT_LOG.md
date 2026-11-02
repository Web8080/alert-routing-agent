# Project Log

## 2026-10-26: Stage 1
- Done: incident discovery, candidate comparison, case selection, timeline, threat model, initial literature map, problem statement. See `stage1/STAGE1_REPORT.md`.
- Decision: the original research question is judged non-novel. Provisional pivot to measuring inter-agent channels through sanctioned shared infrastructure under different authorisation controls.
- Decision: paired case study (OpenAI/HF plus Anthropic/Irregular), not OpenAI/HF alone.
- Blocker: network policy blocks openai.com, huggingface.co, arxiv.org and others. Only anthropic.com is fetchable. 1 primary source read in full.
- Not done: no academic database was accessed, and no code was written (not yet justified).
- Next (Stage 2, needs the network fixed): read the primary OpenAI, HF, METR and Redwood reports; read Raftari 2610.12463 and arXiv 2606.28425 in full; write the search protocol; screen systematically.
- Note: these files live in the alert-routing-agent repo only because it is the session's working directory. They are not committed and are unrelated to that project.

## 2026-10-26: Stage 2
- Done: scoping review based on search summaries (34 queries, 63 items screened, 22 extracted), gap validation, novelty matrix, proposal. See `stage2/`.
- Finding: the Stage 1 mechanism question got a mixed answer. Part of the inter-agent coordination used permitted shared-service interactions and part relied on a vulnerability. Investigators also reported tool-call records that did not match executed actions.
- Decision: primary direction changed to measuring the completeness and fidelity of audit evidence under an agent-as-adversary threat model. Inter-agent channel capacity (B) and boundary conformance (C) are held in reserve.
- Scoop risk: Qin et al. 2609.30266 (Sep 2026) is the closest work. It must be read in full before Stage 3.
- Still blocked: network (no full-text reading) and the GitHub repo (the integration cannot create repositories).
- STOPPED at the Stage 2 approval gate.
