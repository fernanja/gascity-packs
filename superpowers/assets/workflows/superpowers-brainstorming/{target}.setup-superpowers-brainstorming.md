Prepare the Superpowers brainstorming context.

Resolve the build target, artifact root, design candidate path, requirements
artifact path, optional context bundle, and brainstorming approval mode. Use
workflow root metadata `gc.var.brainstorming_approval_mode` when present;
otherwise default to `autonomous`.

Create the brainstorming artifact directory under the build artifact root. Do
not copy pack scripts anywhere: the design-approval and written-spec loop gate
(`../assets/scripts/checks/design-review-approved.sh`) is resolved by gc to the
imported `gc` pack's shipped script when the formula is cooked.

Write a compact context note for the design-approval loop and written-spec
loop.

Do not invoke provider-native subagents or upstream plugin runtime commands.
This Gas City graph stage is the delegation mechanism.
