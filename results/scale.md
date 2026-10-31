# Scale: AI calls vs affected customers (scripted stand-in model)

*Reproducible: deterministic simulator, fixed seeds. code_fingerprint=0b5ab5c54fe7, python=3.13.18*

A rush of fans checks out while payments are silently dropped. One AI session plans the whole incident; automation applies the plan to every ticket.

| fans in the rush | tickets | solved | AI calls | AI sessions | wall time | platform healthy |
|---|---|---|---|---|---|---|
| 10 | 10 | 10/10 | 3 | 1 | 0.0s | True |
| 50 | 45 | 45/45 | 3 | 1 | 0.0s | True |
| 200 | 173 | 173/173 | 3 | 1 | 0.0s | True |
| 800 | 1069 | 1069/1069 | 3 | 1 | 0.1s | True |
