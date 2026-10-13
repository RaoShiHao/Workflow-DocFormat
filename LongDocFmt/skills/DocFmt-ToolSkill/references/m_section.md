# M — section

Assign each source section to a known section style.

**Look at:** leading `text`, header/footer copy in the preview row. Match `roles.json` examples. Cover / TOC / body are different clusters when the template differs (margins, `pageNumFmt`, `titlePage`, header/footer).

**Do not:** assume outline levels; treat `init_cues` as the target look (residual hints only); bind every section to one `Sec*` unless it is the only section role (`skip_if_single`).

When unsure, omit the id. JSON fragment only:

```json
{"by_layer":{"section":{"1":"SecCluster0"}},"table_cells":{}}
```
