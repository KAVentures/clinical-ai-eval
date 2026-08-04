# Private qualification packs — deliberately empty in the public repo

A qualification run needs a clinician-authored case pack: worlds, load-bearing
facts, and required dispositions written by someone qualified to write them.

Packs placed here are **git-ignored**. Publishing them would let a vendor tune
against the very cases used to qualify their product, which is how a robustness
benchmark stops measuring robustness.

`casepacks/patient/public_dev/` is the opposite: synthetic wiring fixtures, safe
to publish precisely because no result from them means anything.
