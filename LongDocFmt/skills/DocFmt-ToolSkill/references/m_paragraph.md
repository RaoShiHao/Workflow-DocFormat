# M — paragraph.body

Classify each body paragraph into a known `Para*` using **text**. Skip empty / whitespace-only paragraphs.

**Look at:** preview `text` vs `roles.json` examples. `outlineLvl` is a residual cue when present — do not assume a heading outline exists.

**Do not:** treat the paragraph’s current formatting as the target style; paste examples into chat; classify in assistant prose; bind every paragraph to the `Para*` with the largest `n` unless it is the only body role (`skip_if_single`).

When unsure, omit the id. One whole-layer JSON fragment:

```json
{"by_layer":{"paragraph.body":{"1":"ParaCluster0","3":"ParaCluster2"}},"table_cells":{}}
```
