# PDG and TOPs dependency workflow

Source: https://www.sidefx.com/docs/houdini/tops/intro.html

PDG/TOPs should describe data dependencies and repeatable work, not merely run a
list of commands. Identify work-item inputs, outputs, attributes, dependencies,
cache policy, and failure behavior before choosing processors or schedulers.

Use processors for work, partitions for meaningful aggregation, and explicit
dependencies for ordering. Distinguish static from dynamic generation when
downstream work depends on results discovered at cook time. Keep paths and
work-item attributes deterministic and portable.

Caching is part of the graph contract. A cached item must correspond to the same
inputs, parameters, code, and output. Do not declare success only because a file
with the right name exists.

Test a small local subset before scaling to many items or a farm. Validate
missing inputs, partial output, cancellation, retry behavior, and environment
differences. Typical failures are hidden dependencies, ambiguous invalidation,
accidental serial bottlenecks, and thousands of tiny tasks dominated by
scheduling overhead.
