# 双泵机组对比图来源

两张图使用同一套历史交付几何和相同的斜侧面相机方向，尺寸均为 2560×1440。

- 项目：`dual-pump-skid-validation-002`，历史交付 r1。
- 交付 ZIP SHA-256：`9e423f02a76f7fe9b1954a8f1d3e8a8c916acc17d5d6dab81b1e1137021db96f`。
- `dual-pump-blender.png`：原交付包中的 `blender/renders/overview-v1.png`，未修改图片。
- `dual-pump-cad.png`：从交付包提取 `cad/skid-v1.FCStd` 到临时目录，在 FreeCAD 中设置与 Blender overview 一致的相机方向、位置和正交视域，使用 Flat Lines 工程显示模式导出。
- CAD 预览隐藏 5 个非物理检修空间对象，显示 36 个物理对象，与 Blender 总览的展示范围对应。
- CAD 文件未保存；导出后验证临时 FCStd 与历史交付内的文件字节一致。

| 图片 | SHA-256 |
|---|---|
| CAD | `70ac908deeb2559890cbeade5908c43920c4a69ebc69d8fa5950f9b6e2efbfb0` |
| Blender | `2042b85d5ce26810e43767937737ea30c288e89c208adecbd2f4c3afff51bee9` |

这些图片用于仓库成果展示，不是新增的正式工程交付或重新验收。图片保存在仓库中，不加入运行 Skill 的文本发布包清单。
