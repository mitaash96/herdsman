---
kind: contract
name: reviewer
title: Reviewer gate: one review document, nothing else written
role: reviewer
handoff: true
---
Settles only when the review document was written at the handoff path the daemon
assigned (`handoffs/<initiative-id>.md`), which is this initiative's only
write route. Any other changed path is refused as an out-of-scope write.
