---
kind: contract
name: architect
title: Architect gate: one decision document, nothing else written
role: architect
handoff: true
---
Settles only when the decision document was written at the handoff path the daemon
assigned (`.herdsman/handoffs/<initiative-id>.md`), which is this initiative's only
write route. Any other changed path is refused as an out-of-scope write.
