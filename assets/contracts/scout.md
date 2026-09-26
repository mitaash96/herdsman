---
kind: contract
name: scout
title: Scout gate: one decision packet, nothing else written
role: scout
handoff: true
---
Settles only when the decision packet was written at the handoff path the daemon
assigned (`handoffs/<initiative-id>.md`), which is this initiative's only
write route. Any other changed path is refused as an out-of-scope write.
