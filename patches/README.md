# Joern engine patch — partial-flow (MoCQ §4 hook)

`joern-mocq-partial-flow.patch` adds `Traversal[CfgNode].reachableByPartialFlows(source)` to Joern's
data-flow query engine: the taint paths that **stalled** before reaching a source (reached a
non-source parameter / unresolved call) — normally computed internally and discarded.

`JoernEngine.probe_missed` calls it so a failing taint query reports *how far propagation got and
where it broke* (`ProbeResult.frontier_path`), instead of only `no_flow`. Without the patch, the
engine falls back to a coarser line-level reachability frontier (stock CPGQL, no build).

## What it changes (`dataflowengineoss/`, ~45 lines, 4 files)

| file | change |
|---|---|
| `queryengine/package.scala` | `TaskSummary` gains a `partials: Vector[ReachableByResult]` field |
| `queryengine/TaskSolver.scala` | carry the per-task `partial` results out in the `TaskSummary` instead of only using them to spawn follow-up tasks |
| `queryengine/Engine.scala` | accumulate the partials across tasks; expose `Engine.lastPartialResults`; clear on `reset()` |
| `language/ExtendedCfgNode.scala` | new `reachableByPartialFlows` — runs the engine, returns `lastPartialResults` as `Path`s |

No existing behaviour changes: `reachableBy` / `reachableByFlows` are untouched.

## Build (opt-in — large)

```bash
git clone https://github.com/joernio/joern.git joern-src     # or the peng-hui/joern sink-filter fork
cd joern-src && git checkout v4.0.614
git apply ../MoCQ/patches/joern-mocq-partial-flow.patch   # path to this repo checkout
sbt "joerncli/stage"
# stage adds new-dated jars beside the old ones on the lib/* classpath — drop the stale copies:
cd joern-cli/target/universal/stage/lib
for p in dataflowengineoss macros console semanticcpg; do ls -t io.joern.$p-*.jar | tail -n +2 | xargs -r rm -f; done
```

Then point the engine at it: `JOERN_HOME=<.../joern-src>` in `.env`
(the `./joern`, `./joern-parse` wrappers at the checkout root).

`setup.sh` does this automatically when `JOERN_BUILD_FORK=1` is set.
