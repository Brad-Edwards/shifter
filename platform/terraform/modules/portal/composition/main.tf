# Portal Composition Module (AWS)
#
# The repeated dev/proof/prod portal resource graph, extracted so a new AWS
# environment is a thin root selecting backend + tfvars rather than another
# copied main.tf (#688).
#
# Ownership split:
#   environment root  - terraform/provider blocks, lockfile, backend config and
#                       state key, terraform_remote_state reads, the public
#                       variable contract (names/types/defaults/validation),
#                       the public output contract, and moved.tf.
#   this module       - the resource graph and its internal wiring.
#
# Environment variation travels on explicit typed inputs (enable_ctfd,
# deletion protection, token validity, warm pool, alarm wiring, secret
# recovery window) and never on the environment name. Remote-state values
# arrive as typed scalars, never as an opaque remote-state object.
#
# The graph is organized into sibling files by concern - kms, network,
# database, identity, messaging, compute, storage, secrets, engine,
# observability. Terraform evaluates all sibling files as one module, so this
# layout carries no state-address meaning.
