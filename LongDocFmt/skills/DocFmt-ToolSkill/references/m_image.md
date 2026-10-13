# M — image

Assign each picture to a known `Img*` using size (`width` / `height` / `hAlign`), caption, and nearby `before` / `after` paragraphs.

If several `ImgCluster*` exist, match size/`examples`; do not bind every image to one cluster. If only one image role exists, bind all images to it (`skip_if_single`).

When unsure, omit the id.

```json
{"by_layer":{"image":{"1":"ImgCluster0"}},"table_cells":{}}
```
