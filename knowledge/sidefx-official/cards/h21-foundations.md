# H21 Foundations: end-to-end procedural shot workflow

Source: https://www.sidefx.com/tutorials/h21-foundations-welcome/

Use this card when a request spans several disciplines and the correct answer is
a coherent Houdini project rather than a collection of disconnected nodes.

Recommended sequence:

1. Establish real-world scale, orientation, frame range, output purpose, and the
   Houdini build before authoring.
2. Separate reusable source geometry, simulation inputs, look development,
   lighting, camera, and render outputs. Preserve procedural controls at the
   highest useful level instead of baking every intermediate result.
3. Validate modeling silhouettes and topology before simulation or shading.
   Validate simulation scale and collisions before spending time on materials.
4. Build materials and lighting against a stable asset. Use a representative
   preview camera and low-cost render settings while iterating.
5. Make final SOP, material, LOP, camera, and render outputs explicit. Re-open or
   re-cook critical outputs before claiming completion.

Preserve the order of operations rather than copying the tutorial asset.
Common failures include inconsistent scale, starting final materials before the
geometry is stable, mixing temporary experiments into the main graph, rendering
from an accidental camera, and trusting an unverified cached result.
