# SAMPLE DATA — not a model output

Every JSON file in this directory is hand-written to match the shapes in
`API.md`. None of it came from a trained model, the rule engine, or the
planner. No route in this shape has been implemented yet
(`src/trackshift/serve/` and `artifacts/demo/` do not exist).

Use these fixtures only to develop and type-check a future fetch layer
against a known shape. Do not render them as if they were real predictions
— see `frontend/src/app/decision/DecisionView.tsx` for why: a plausible
placeholder number is worse than an empty panel when it can be mistaken
for a result.
