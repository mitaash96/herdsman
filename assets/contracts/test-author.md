---
kind: contract
name: test-author
title: Test-author gate: tests written, production code untouched
role: test-author
---
Settles only with every changed path inside the initiative's declared write
routes, which name test paths only. Add the
project's test command under `required_checks` in a project copy: then a
test that fails against the current code fails the gate, so a finding stops
downstream work instead of settling as a pass.
